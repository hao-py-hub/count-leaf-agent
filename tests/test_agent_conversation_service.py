"""Tests for privacy-preserving Agent conversation replay."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from app.agent.models import AgentChatRequest, AgentChatResponse, AgentIntent, AgentResponseStatus, ToolName
from app.services.agent_conversation_service import AgentConversationService


class AgentConversationServiceTests(unittest.TestCase):
    def test_replay_preserves_tool_metadata_without_raw_message(self) -> None:
        with TemporaryDirectory() as directory:
            service = AgentConversationService(Path(directory) / "conversations.sqlite3")
            conversation_id = uuid4()
            request = AgentChatRequest(message="私密提问内容", conversation_id=conversation_id)
            response = AgentChatResponse(
                intent=AgentIntent.DATA_ANALYSIS,
                status=AgentResponseStatus.DATA_RETRIEVED,
                message="已查询到 1 条历史记录。",
                trace_id=request.trace_id,
                requested_tool=ToolName.SEARCH_HISTORY,
                skill_name="project-statistics",
                skill_version="v0.1",
            )
            service.record(request, response, duration_ms=12)

            event = service.list_events(str(conversation_id))[0]
            self.assertEqual(event["requested_tool"], "search_history")
            self.assertEqual(event["trace_id"], str(request.trace_id))
            self.assertEqual(event["skill_name"], "project-statistics")
            self.assertEqual(event["skill_version"], "v0.1")
            self.assertNotIn("私密提问内容", str(event))
            self.assertEqual(len(event["message_sha256"]), 64)
            tool_call = service.list_tool_calls(str(conversation_id))[0]
            self.assertEqual(tool_call["tool_name"], "search_history")
            self.assertEqual(tool_call["duration_ms"], 12)
            self.assertEqual(tool_call["call_origin"], "agent_workflow")
            self.assertNotIn("私密提问内容", str(tool_call))

    def test_external_tool_calls_include_origin_in_recent_audit(self) -> None:
        with TemporaryDirectory() as directory:
            service = AgentConversationService(Path(directory) / "conversations.sqlite3")
            conversation_id = uuid4()
            service.record_external_tool_call(
                conversation_id=str(conversation_id), trace_id=str(uuid4()),
                tool_name="search_history", arguments='{"query":"secret"}',
                result_status="completed", result_reference=None, duration_ms=8,
                call_origin="mcp",
            )
            entry = service.list_recent_tool_calls()[0]
            self.assertEqual(entry["call_origin"], "mcp")
            self.assertEqual(entry["tool_name"], "search_history")
            self.assertNotIn("secret", str(entry))

    def test_trace_replay_aggregates_workflow_and_tool_calls(self) -> None:
        with TemporaryDirectory() as directory:
            service = AgentConversationService(Path(directory) / "conversations.sqlite3")
            request = AgentChatRequest(message="按追踪回放")
            response = AgentChatResponse(intent=AgentIntent.DATA_ANALYSIS, status=AgentResponseStatus.DATA_RETRIEVED, message="完成", trace_id=request.trace_id, requested_tool=ToolName.SEARCH_HISTORY)
            service.record(request, response, duration_ms=3)
            replay = service.list_trace_events(str(request.trace_id))
            self.assertEqual(replay["trace_id"], str(request.trace_id))
            self.assertEqual(len(replay["events"]), 1)
            self.assertEqual(len(replay["tool_calls"]), 1)

    def test_missing_conversation_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            service = AgentConversationService(Path(directory) / "conversations.sqlite3")
            with self.assertRaises(KeyError):
                service.list_events(str(uuid4()))
            with self.assertRaises(KeyError):
                service.list_tool_calls(str(uuid4()))

    def test_report_reference_is_retained_without_user_content(self) -> None:
        with TemporaryDirectory() as directory:
            service = AgentConversationService(Path(directory) / "conversations.sqlite3")
            conversation_id = uuid4()
            report_job_id = uuid4()
            request = AgentChatRequest(message="私密报告请求", conversation_id=conversation_id)
            response = AgentChatResponse(
                intent=AgentIntent.REPORT,
                status=AgentResponseStatus.REPORT_RETRIEVED,
                message="已读取报告任务状态。",
                requested_tool=ToolName.GET_REPORT,
                report_job_id=report_job_id,
            )
            service.record(request, response, duration_ms=4)

            event = service.list_events(str(conversation_id))[0]
            call = service.list_tool_calls(str(conversation_id))[0]
            self.assertEqual(event["report_job_id"], str(report_job_id))
            self.assertEqual(call["result_reference"], str(report_job_id))
            self.assertNotIn("私密报告请求", str(event))

    def test_session_summary_cache_is_scoped_and_versioned(self) -> None:
        with TemporaryDirectory() as directory:
            service = AgentConversationService(Path(directory) / "conversations.sqlite3")
            conversation_id = str(uuid4())
            stored = service.cache_session_summary(
                conversation_id=conversation_id, query=" N0  ", history_version="2:v1",
                summary={"scope_query": "N0", "sample_count": 2, "completed_count": 1,
                         "failed_count": 1, "average_total_pods": 10.0, "raw": "discard"},
                reference_ids=["run-1"],
            )
            self.assertFalse(stored["cache_hit"])
            cached = service.get_session_summary(
                conversation_id=conversation_id, query="n0", history_version="2:v1"
            )
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(cached["reference_ids"], ["run-1"])
            self.assertNotIn("raw", cached)
            self.assertIsNone(service.get_session_summary(
                conversation_id=conversation_id, query="n0", history_version="3:v2"
            ))


if __name__ == "__main__":
    unittest.main()
