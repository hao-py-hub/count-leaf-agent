"""Protocol-level safety checks for the read-only MCP adapter."""

import unittest
from uuid import uuid4

from app.agent.function_executor import FunctionToolExecutor
from app.mcp.server import MCP_PROTOCOL_VERSION, McpServer, list_tools


def executor() -> FunctionToolExecutor:
    return FunctionToolExecutor(
        get_analysis_result=lambda job_id, project_id: {"job_id": str(job_id)},
        check_quality=lambda asset_id: {"asset_id": str(asset_id), "ok": True},
        check_job_quality=lambda job_id, project_id: {"job_id": str(job_id), "ok": True},
        search_history=lambda query, project_id: {"query": query, "project_id": project_id},
        summarize_history=lambda query, project_id: {"scope_query": query, "project_id": project_id},
        get_review=lambda review_id, project_id: {"review_id": str(review_id)},
        retrieve_knowledge=lambda query, project_id: [{"title": query, "project_id": project_id}],
        get_batch_progress=lambda batch_id, project_id: {"batch_id": str(batch_id)},
        get_report=lambda report_job_id, project_id: {"report_job_id": str(report_job_id)},
    )


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = McpServer(executor())

    def test_lists_the_shared_read_only_tool_set(self) -> None:
        names = {tool["name"] for tool in list_tools()}
        self.assertEqual(names, {
            "get_analysis_result", "quality_check", "search_history",
            "aggregate_metrics", "get_review", "retrieve_knowledge",
        })
        self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in list_tools()))
        optional_tools = {tool["name"]: tool for tool in list_tools()}
        self.assertEqual(optional_tools["search_history"]["inputSchema"]["required"], [])
        self.assertEqual(optional_tools["aggregate_metrics"]["inputSchema"]["required"], [])

    def test_initialize_negotiates_protocol(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)

    def test_executes_a_read_only_tool(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_history", "arguments": {"query": "N0"}}})
        self.assertFalse(response["result"]["isError"])
        self.assertIn("N0", response["result"]["content"][0]["text"])
        self.assertIn("conversation_id", response["result"]["_meta"])

    def test_forwards_project_scope_to_read_only_tool(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "search_history", "arguments": {"query": "N0", "project_id": "mcp-project"}}})
        self.assertIn("mcp-project", response["result"]["content"][0]["text"])

    def test_write_tool_is_not_available_through_mcp(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "submit_analysis", "arguments": {"asset_id": str(uuid4())}}})
        self.assertTrue(response["result"]["isError"])

    def test_invalid_tool_arguments_return_tool_error(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "search_history", "arguments": {"query": "N0", "extra": True}}})
        self.assertTrue(response["result"]["isError"])

    def test_unknown_tool_returns_a_safe_tool_error(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "read_environment", "arguments": {}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["content"][0]["text"], "该 MCP 服务不支持此工具")

    def test_audits_external_calls_without_arguments(self) -> None:
        audit_records = []
        server = McpServer(executor(), audit_tool_call=lambda **record: audit_records.append(record))
        response = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "search_history", "arguments": {"query": "private-value"}}})
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(len(audit_records), 1)
        self.assertEqual(audit_records[0]["tool_name"], "search_history")
        self.assertEqual(audit_records[0]["result_status"], "completed")
        self.assertNotIn("private-value", str({key: value for key, value in audit_records[0].items() if key != "arguments"}))

    def test_invalid_arguments_are_audited(self) -> None:
        audit_records = []
        server = McpServer(executor(), audit_tool_call=lambda **record: audit_records.append(record))
        response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "search_history", "arguments": {"query": "N0", "extra": True}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(audit_records[0]["result_status"], "invalid_arguments")

    def test_mcp_call_can_write_to_unified_audit_ledger(self) -> None:
        ledger_records = []
        server = McpServer(executor(), audit_event=lambda **record: ledger_records.append(record))
        response = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "search_history", "arguments": {"query": "private-value"}}})
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(ledger_records[0]["source"], "mcp")
        self.assertEqual(ledger_records[0]["event_type"], "mcp_call")
        self.assertNotIn("private-value", str(ledger_records[0]))

    def test_forbidden_mcp_call_is_written_to_unified_audit(self) -> None:
        ledger_records = []
        server = McpServer(executor(), audit_event=lambda **record: ledger_records.append(record))
        server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "read_environment", "arguments": {}}})
        self.assertEqual(ledger_records[0]["event_type"], "mcp_forbidden")
        self.assertTrue(ledger_records[0]["trace_id"])


if __name__ == "__main__":
    unittest.main()
