"""Safety contracts for enterprise read-only Function Calling execution."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agent.function_executor import FunctionToolCall, FunctionToolExecutor
from app.agent.models import ToolName
from app.main import app


def executor() -> FunctionToolExecutor:
    return FunctionToolExecutor(
        get_analysis_result=lambda job_id, project_id: {"run_id": str(job_id)},
        get_analysis_evidence=lambda job_id, project_id: {"job_id": str(job_id), "project_id": project_id, "review_feedback": []},
        check_quality=lambda asset_id: {"ok": True},
        check_job_quality=lambda job_id, project_id: {"job_id": str(job_id), "ok": True},
        search_history=lambda query, project_id: {"query": query, "project_id": project_id},
        summarize_history=lambda query, project_id: {"query": query, "project_id": project_id},
        get_review=lambda review_id, project_id: {"review_id": str(review_id)},
        retrieve_knowledge=lambda query, project_id: [{"title": query, "project_id": project_id}],
        get_batch_progress=lambda batch_id, project_id: {"batch_id": str(batch_id), "status": "running"},
        get_report=lambda report_job_id, project_id: {"report_job_id": str(report_job_id), "status": "completed"},
    )


class AgentFunctionExecutorTests(unittest.TestCase):
    def setUp(self):
        self.original_executor = app.state.function_tool_executor
        app.state.function_tool_executor = executor()

    def tearDown(self):
        app.state.function_tool_executor = self.original_executor

    def test_read_only_history_query_executes(self):
        result = executor().execute(
            FunctionToolCall(name=ToolName.SEARCH_HISTORY, arguments={"query": "N0"})
        )
        self.assertEqual(result["query"], "N0")

    def test_project_scoped_read_tools_forward_validated_project_id(self):
        history = executor().execute(
            FunctionToolCall(name=ToolName.SEARCH_HISTORY, arguments={"query": "N0", "project_id": "project-a"})
        )
        knowledge = executor().execute(
            FunctionToolCall(name=ToolName.RETRIEVE_KNOWLEDGE, arguments={"query": "拍摄规范", "project_id": "project-a"})
        )
        self.assertEqual(history["project_id"], "project-a")
        self.assertEqual(knowledge["hits"][0]["project_id"], "project-a")

    def test_function_call_reads_project_scoped_analysis_evidence(self):
        job_id = uuid4()
        result = executor().execute(
            FunctionToolCall(
                name=ToolName.GET_ANALYSIS_EVIDENCE,
                arguments={"analysis_job_id": str(job_id), "project_id": "project-a"},
            )
        )
        self.assertEqual(result["job_id"], str(job_id))
        self.assertEqual(result["project_id"], "project-a")

    def test_write_tool_is_rejected(self):
        with self.assertRaises(PermissionError):
            executor().execute(
                FunctionToolCall(name=ToolName.SUBMIT_ANALYSIS, arguments={"asset_id": str(uuid4())})
            )

    def test_extra_arguments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "额外字段"):
            executor().execute(
                FunctionToolCall(name=ToolName.SEARCH_HISTORY, arguments={"query": "N0", "sql": "DROP"})
            )

    def test_quality_check_accepts_one_job_reference(self):
        job_id = uuid4()
        result = executor().execute(
            FunctionToolCall(name=ToolName.QUALITY_CHECK, arguments={"analysis_job_id": str(job_id)})
        )
        self.assertEqual(result["job_id"], str(job_id))

    def test_quality_check_rejects_ambiguous_resource_references(self):
        with self.assertRaisesRegex(ValueError, "只能提供"):
            executor().execute(
                FunctionToolCall(
                    name=ToolName.QUALITY_CHECK,
                    arguments={"asset_id": str(uuid4()), "analysis_job_id": str(uuid4())},
                )
            )

    def test_function_call_reads_batch_progress_but_mcp_does_not_expose_it(self):
        batch_id = uuid4()
        result = executor().execute(
            FunctionToolCall(name=ToolName.GET_BATCH_PROGRESS, arguments={"batch_id": str(batch_id)})
        )
        self.assertEqual(result["batch_id"], str(batch_id))

    def test_function_call_reads_report_but_write_tools_remain_rejected(self):
        report_job_id = uuid4()
        result = executor().execute(
            FunctionToolCall(name=ToolName.GET_REPORT, arguments={"report_job_id": str(report_job_id)})
        )
        self.assertEqual(result["report_job_id"], str(report_job_id))

    def test_http_report_read_audit_uses_report_job_reference(self):
        report_job_id = uuid4()
        conversation_id = uuid4()
        response = TestClient(app).post(
            "/api/v2/agent/function-tools/execute",
            json={
                "name": "get_report",
                "arguments": {"report_job_id": str(report_job_id)},
                "conversation_id": str(conversation_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        call = TestClient(app).get(
            f"/api/v2/agent/conversations/{conversation_id}/tool-calls"
        ).json()["tool_calls"][0]
        self.assertEqual(call["result_reference"], str(report_job_id))

    def test_http_write_tool_returns_forbidden(self):
        response = TestClient(app).post(
            "/api/v2/agent/function-tools/execute",
            json={"name": "submit_analysis", "arguments": {"asset_id": str(uuid4())}},
        )
        self.assertEqual(response.status_code, 403)

    def test_http_read_only_call_returns_replay_identifiers(self):
        conversation_id = uuid4()
        trace_id = uuid4()
        response = TestClient(app).post(
            "/api/v2/agent/function-tools/execute",
            json={
                "name": "search_history", "arguments": {"query": "N0"},
                "conversation_id": str(conversation_id), "trace_id": str(trace_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["conversation_id"], str(conversation_id))
        replay = TestClient(app).get(f"/api/v2/agent/conversations/{conversation_id}/tool-calls")
        item = replay.json()["tool_calls"][0]
        self.assertEqual(item["result_status"], "completed")
        self.assertNotIn("N0", str(item))

    def test_forbidden_call_is_audited(self):
        conversation_id = uuid4()
        response = TestClient(app).post(
            "/api/v2/agent/function-tools/execute",
            json={"name": "submit_analysis", "arguments": {"asset_id": str(uuid4())}, "conversation_id": str(conversation_id)},
        )
        self.assertEqual(response.status_code, 403)
        replay = TestClient(app).get(f"/api/v2/agent/conversations/{conversation_id}/tool-calls")
        self.assertEqual(replay.json()["tool_calls"][0]["result_status"], "forbidden")

    def test_recent_audit_includes_function_call_tool_metadata(self):
        response = TestClient(app).post(
            "/api/v2/agent/function-tools/execute",
            json={"name": "search_history", "arguments": {"query": "private-query"}},
        )
        self.assertEqual(response.status_code, 200)
        audit = TestClient(app).get("/api/v2/agent/audit")
        self.assertEqual(audit.status_code, 200)
        entry = audit.json()["tool_calls"][0]
        self.assertEqual(entry["call_origin"], "function_call")
        self.assertEqual(entry["tool_name"], "search_history")
        self.assertNotIn("private-query", str(entry))

    def test_tool_audit_export_contains_only_redacted_metadata(self):
        TestClient(app).post(
            "/api/v2/agent/function-tools/execute",
            json={"name": "search_history", "arguments": {"query": "private-query"}},
        )
        response = TestClient(app).get("/api/v2/agent/audit/export?limit=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("input_sha256", response.text)
        self.assertNotIn("private-query", response.text)


if __name__ == "__main__":
    unittest.main()
