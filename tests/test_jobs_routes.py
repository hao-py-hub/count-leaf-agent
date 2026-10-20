"""HTTP contracts for the standard confirmed analysis-job API."""

from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


class JobService:
    def __init__(self) -> None:
        self.asset_id = uuid4()
        self.job_id = uuid4()
        self.submissions = 0
        self.last_project_id = None
        self.retry_calls = 0
        self.cancel_calls = 0
        self.revoke_calls = 0

    def submit(self, asset_id, **kwargs):
        if asset_id != self.asset_id:
            raise KeyError(asset_id)
        self.submissions += 1
        self.last_project_id = kwargs.get("project_id")
        return self.job_id

    def get(self, job_id):
        if job_id != self.job_id:
            raise KeyError(job_id)
        return SimpleNamespace(
            job_id=self.job_id,
            asset_id=self.asset_id,
            status="completed",
            created_at="2026-08-16T00:00:00+00:00",
            updated_at="2026-08-16T00:00:01+00:00",
            run_id="run-001",
            error_message=None,
            result={"type": "result", "run_id": "run-001", "total_pods": 12},
            as_dict=lambda: {
                "job_id": self.job_id,
                "asset_id": self.asset_id,
                "status": "completed",
                "created_at": "2026-08-16T00:00:00+00:00",
                "updated_at": "2026-08-16T00:00:01+00:00",
                "run_id": "run-001",
                "error_message": None,
                "result": {"type": "result", "run_id": "run-001", "total_pods": 12},
            },
        )

    def retry_failed(self, job_id):
        if job_id != self.job_id:
            raise KeyError(job_id)
        self.retry_calls += 1
        return self.job_id

    def cancel_queued(self, job_id):
        if job_id != self.job_id:
            raise KeyError(job_id)
        self.cancel_calls += 1
        return self.get(job_id)

    def revoke_cache(self, job_id):
        if job_id != self.job_id:
            raise KeyError(job_id)
        self.revoke_calls += 1

    def list_recent(self, *, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须介于 1 和 100")
        return [self.get(self.job_id)]


class JobsRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.service = JobService()
        self.original_service = app.state.analysis_job_service
        app.state.analysis_job_service = self.service

    def tearDown(self):
        app.state.analysis_job_service = self.original_service

    def test_submission_requires_confirmation_before_creating_job(self):
        response = self.client.post("/api/v2/jobs/analysis", json={"asset_id": str(self.service.asset_id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "confirmation_required")
        self.assertEqual(self.service.submissions, 0)

    def test_confirmed_submission_returns_real_job(self):
        response = self.client.post(
            "/api/v2/jobs/analysis",
            json={"asset_id": str(self.service.asset_id), "confirm": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], "default")

    def test_confirmed_submission_passes_project_scope(self):
        response = self.client.post(
            "/api/v2/jobs/analysis",
            json={"asset_id": str(self.service.asset_id), "confirm": True, "project_id": "trial-a"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.last_project_id, "trial-a")
        self.assertEqual(response.json()["job_id"], str(self.service.job_id))
        self.assertEqual(self.service.submissions, 1)

    def test_submission_normalizes_project_scope_before_service_call(self):
        response = self.client.post(
            "/api/v2/jobs/analysis",
            json={"asset_id": str(self.service.asset_id), "confirm": True, "project_id": "  trial-a  "},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.last_project_id, "trial-a")

    def test_standard_job_event_endpoint_emits_terminal_state(self):
        response = self.client.get(f"/api/v2/jobs/{self.service.job_id}/events")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: terminal", response.text)

    def test_review_required_job_result_remains_readable(self):
        original_get = self.service.get

        def get_review_required(job_id):
            job = original_get(job_id)
            job.status = "review_required"
            return job

        self.service.get = get_review_required
        response = self.client.get(f"/api/v2/jobs/{self.service.job_id}/result")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["total_pods"], 12)

    def test_standard_list_route_returns_task_metadata(self):
        response = self.client.get("/api/v2/jobs?limit=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["job_id"], str(self.service.job_id))

    def test_standard_retry_and_cancel_routes_return_task_metadata(self):
        retry = self.client.post(f"/api/v2/jobs/{self.service.job_id}/retry")
        cancel = self.client.post(f"/api/v2/jobs/{self.service.job_id}/cancel")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(retry.json()["status"], "confirmation_required")
        self.assertEqual(cancel.json()["status"], "confirmation_required")

        retry = self.client.post(f"/api/v2/jobs/{self.service.job_id}/retry", json={"confirm": True})
        cancel = self.client.post(f"/api/v2/jobs/{self.service.job_id}/cancel", json={"confirm": True})
        self.assertEqual(retry.json()["job_id"], str(self.service.job_id))
        self.assertEqual(cancel.json()["status"], "completed")

    def test_wrong_project_cannot_trigger_task_write_operations(self):
        """错误项目必须在调用任何写服务前被拒绝。"""
        for operation in ("retry", "cancel", "cache-revocation"):
            response = self.client.post(
                f"/api/v2/jobs/{self.service.job_id}/{operation}",
                json={"confirm": True, "project_id": "trial-b"},
            )
            self.assertEqual(response.status_code, 404)

        self.assertEqual(self.service.retry_calls, 0)
        self.assertEqual(self.service.cancel_calls, 0)
        self.assertEqual(self.service.revoke_calls, 0)


if __name__ == "__main__":
    unittest.main()
