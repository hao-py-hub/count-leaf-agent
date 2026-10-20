import io
import json
import unittest
from unittest.mock import patch

from app.agent.function_executor import FunctionToolExecutor
from app.mcp.server import McpServer, run_stdio


def build_executor():
    return FunctionToolExecutor(
        get_analysis_result=lambda value: {"analysis_job_id": str(value)},
        check_quality=lambda value: {"ok": True},
        check_job_quality=lambda value: {"ok": True},
        search_history=lambda value, project_id: {"query": value, "project_id": project_id},
        summarize_history=lambda value, project_id: {"query": value, "project_id": project_id},
        get_review=lambda value: {"review_id": str(value)},
        retrieve_knowledge=lambda value, project_id: [{"title": value, "project_id": project_id}],
        get_batch_progress=lambda value: {"batch_id": str(value)},
        get_report=lambda value: {"report_job_id": str(value)},
    )


class McpStdioSmokeTests(unittest.TestCase):
    def test_external_agent_stdio_sequence(self) -> None:
        messages = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_history", "arguments": {"query": "N0"}}}),
        ]) + "\n"
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(messages)), patch("sys.stdout", output):
            run_stdio(lambda: McpServer(build_executor()))
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("search_history", {item["name"] for item in responses[1]["result"]["tools"]})
        self.assertFalse(responses[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
