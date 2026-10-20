"""Tests for privacy-aware Agent audit events."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from app.agent.models import AgentChatRequest, AgentChatResponse, AgentIntent, AgentResponseStatus, ToolName
from app.services.agent_audit_service import AgentAuditService


class AgentAuditServiceTests(unittest.TestCase):
    def test_event_hashes_message_without_storing_plain_text(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            audit = AgentAuditService(Path(temporary_directory) / "audit.sqlite3")
            request = AgentChatRequest(message="敏感的用户提问")
            response = AgentChatResponse(
                intent=AgentIntent.KNOWLEDGE_QA,
                status=AgentResponseStatus.ANSWERED,
                message="回答",
                requested_tool=ToolName.RETRIEVE_KNOWLEDGE,
                citations=["doc:chunk"],
                skill_name="knowledge-grounded-answer",
                skill_version="v0.1",
            )
            audit.record(request, response)
            event = audit.list_events()[0]

            self.assertNotIn("敏感的用户提问", str(event))
            self.assertEqual(event["citation_count"], 1)
            self.assertEqual(event["requested_tool"], "retrieve_knowledge")
            self.assertEqual(event["trace_id"], str(request.trace_id))
            self.assertEqual(event["skill_name"], "knowledge-grounded-answer")
            self.assertEqual(event["skill_version"], "v0.1")

    def test_events_can_be_filtered_by_trace(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            audit = AgentAuditService(Path(temporary_directory) / "audit.sqlite3")
            request = AgentChatRequest(message="过滤追踪")
            audit.record(request, AgentChatResponse(intent=AgentIntent.KNOWLEDGE_QA, status=AgentResponseStatus.ANSWERED, message="回答"))
            self.assertEqual(len(audit.list_events(trace_id=str(request.trace_id))), 1)
            self.assertEqual(audit.list_events(trace_id=str(uuid4())), [])

    def test_event_preserves_report_reference_without_message_content(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            audit = AgentAuditService(Path(temporary_directory) / "audit.sqlite3")
            report_job_id = uuid4()
            request = AgentChatRequest(message="不可保存的报告原文")
            response = AgentChatResponse(
                intent=AgentIntent.REPORT,
                status=AgentResponseStatus.REPORT_RETRIEVED,
                message="已读取报告任务状态。",
                requested_tool=ToolName.GET_REPORT,
                report_job_id=report_job_id,
            )
            audit.record(request, response)
            event = audit.list_events()[0]

            self.assertEqual(event["report_job_id"], str(report_job_id))
            self.assertNotIn("不可保存的报告原文", str(event))


if __name__ == "__main__":
    unittest.main()
