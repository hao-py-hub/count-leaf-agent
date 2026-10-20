from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


class AgentJobConfirmationTests(unittest.TestCase):
    def test_agent_retry_and_cancel_require_confirmation(self) -> None:
        original = app.state.analysis_job_service
        try:
            job_id = uuid4()
            class Service:
                def retry_failed(self, requested): return requested
                def cancel_queued(self, requested): return SimpleNamespace(job_id=requested, asset_id=uuid4(), status="cancelled", created_at="2026-01-01", updated_at="2026-01-01", run_id=None, error_message=None, cache_hit=False, trace_id=None, retry_of=None, as_dict=lambda: {"job_id": str(requested), "asset_id": str(uuid4()), "status": "cancelled", "created_at": "2026-01-01", "updated_at": "2026-01-01", "run_id": None, "error_message": None, "cache_hit": False, "trace_id": None, "retry_of": None})
            app.state.analysis_job_service = Service()
            with TestClient(app) as client:
                retry = client.post(f"/api/v2/agent/jobs/{job_id}/retry")
                cancel = client.post(f"/api/v2/agent/jobs/{job_id}/cancel")
            self.assertEqual(retry.json()["status"], "confirmation_required")
            self.assertEqual(cancel.json()["status"], "confirmation_required")
        finally:
            app.state.analysis_job_service = original


if __name__ == "__main__":
    unittest.main()
