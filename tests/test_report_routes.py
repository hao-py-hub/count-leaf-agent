"""HTTP contracts for confirmed report generation."""

from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


class CompletedJobService:
    def __init__(self, job_id) -> None:
        self.job_id = job_id

    def get(self, job_id):
        if job_id != self.job_id:
            raise KeyError(job_id)
        return SimpleNamespace(
            job_id=job_id,
            status="completed",
            result={"type": "result", "run_id": "run-001", "total_pods": 8},
        )


class CompletedReportService:
    def __init__(self, report_job_id, analysis_job_id) -> None:
        self.report_job_id = report_job_id
        self.analysis_job_id = analysis_job_id

    def get(self, requested_id):
        if requested_id != self.report_job_id:
            raise KeyError(requested_id)
        return SimpleNamespace(
            report_job_id=requested_id,
            analysis_job_id=self.analysis_job_id,
            status="completed",
            report="报告内容",
            error_message=None,
        )


class ReportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.job_id = uuid4()
        self.original_service = app.state.analysis_job_service
        self.original_report_service = app.state.report_job_service
        app.state.analysis_job_service = CompletedJobService(self.job_id)

    def tearDown(self) -> None:
        app.state.analysis_job_service = self.original_service
        app.state.report_job_service = self.original_report_service
        self.client.close()

    def test_report_generation_requires_server_side_confirmation(self) -> None:
        response = self.client.post(
            f"/api/v2/agent/jobs/{self.job_id}/report",
            json={"question": "关注总角果数"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "confirmation_required")
        self.assertEqual(response.json()["job_id"], str(self.job_id))

    def test_completed_report_emits_terminal_event(self) -> None:
        report_job_id = uuid4()
        app.state.report_job_service = CompletedReportService(report_job_id, self.job_id)
        response = self.client.get(f"/api/v2/agent/reports/{report_job_id}/events?follow=false")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: status", response.text)
        self.assertIn("event: terminal", response.text)
        self.assertIn('"status": "completed"', response.text)

    def test_report_read_rejects_a_different_project_scope(self) -> None:
        report_job_id = uuid4()
        app.state.report_job_service = SimpleNamespace(get=lambda _: SimpleNamespace(
            report_job_id=report_job_id, analysis_job_id=self.job_id, status="completed",
            report="报告内容", error_message=None, trace_id=None, project_id="trial-a",
        ))
        response = self.client.get(f"/api/v2/agent/reports/{report_job_id}?project_id=trial-b")
        self.assertEqual(response.status_code, 404)

    def test_report_creation_rejects_a_different_project_scope(self) -> None:
        app.state.analysis_job_service = SimpleNamespace(get=lambda _: SimpleNamespace(
            job_id=self.job_id, status="completed", project_id="trial-a",
            result={"type": "result", "run_id": "run-001", "total_pods": 8},
        ))
        response = self.client.post(
            f"/api/v2/agent/jobs/{self.job_id}/report",
            json={"confirm": True, "project_id": "trial-b"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
