"""Contract tests for the local task state SSE endpoint."""

from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


class CompletedJobService:
    def __init__(self, job_id):
        self._job_id = job_id

    def get(self, job_id):
        if job_id != self._job_id:
            raise KeyError("分析任务不存在")
        return SimpleNamespace(
            job_id=job_id,
            status="completed",
            updated_at="2026-08-09T00:00:00+00:00",
            run_id="run-001",
            error_message=None,
        )


class AgentEventStreamTests(unittest.TestCase):

    def test_same_conversation_id_cannot_be_reused_by_another_project(self) -> None:
        conversation_id = uuid4()
        first = self.client.post("/api/v2/agent/chat", json={"message": "查询历史记录", "conversation_id": str(conversation_id), "project_id": "trial-a"})
        second = self.client.post("/api/v2/agent/chat", json={"message": "查询历史记录", "conversation_id": str(conversation_id), "project_id": "trial-b"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
    def setUp(self):
        self.client = TestClient(app)
        self.original_service = app.state.analysis_job_service
        self.job_id = uuid4()
        app.state.analysis_job_service = CompletedJobService(self.job_id)

    def tearDown(self):
        app.state.analysis_job_service = self.original_service

    def test_completed_job_emits_status_and_terminal_events(self):
        response = self.client.get(f"/api/v2/agent/jobs/{self.job_id}/events")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: status", response.text)
        self.assertIn("event: terminal", response.text)
        self.assertIn('"status": "completed"', response.text)

    def test_missing_job_is_rejected_before_stream_starts(self):
        response = self.client.get(f"/api/v2/agent/jobs/{uuid4()}/events")
        self.assertEqual(response.status_code, 404)

    def test_conversation_snapshot_stream_is_privacy_preserving(self):
        chat = self.client.post("/api/v2/agent/chat", json={"message": "查询历史记录"})
        conversation_id = chat.json()["conversation_id"]
        response = self.client.get(f"/api/v2/agent/conversations/{conversation_id}/events?follow=false")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: conversation", response.text)
        self.assertIn('"requested_tool": "search_history"', response.text)
        self.assertNotIn("查询历史记录", response.text)

    def test_tool_call_endpoint_returns_redacted_execution_metadata(self):
        chat = self.client.post("/api/v2/agent/chat", json={"message": "查询历史记录"})
        conversation_id = chat.json()["conversation_id"]
        response = self.client.get(f"/api/v2/agent/conversations/{conversation_id}/tool-calls")
        item = response.json()["tool_calls"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(item["tool_name"], "search_history")
        self.assertIn("duration_ms", item)
        self.assertNotIn("查询历史记录", str(item))

    def test_chat_analysis_requires_confirmation_before_tool_execution(self):
        response = self.client.post(
            "/api/v2/agent/chat",
            json={"message": "分析图片", "asset_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "confirmation_required")
        self.assertEqual(response.json()["requested_tool"], "submit_analysis")


if __name__ == "__main__":
    unittest.main()
