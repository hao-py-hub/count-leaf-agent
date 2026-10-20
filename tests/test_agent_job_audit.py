from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger


class AgentJobAuditTests(unittest.TestCase):
    def test_confirmed_retry_and_cancel_write_audit_events(self) -> None:
        original_service = app.state.analysis_job_service
        original_ledger = app.state.audit_ledger
        try:
            parent = uuid4(); retry = uuid4(); trace_id = uuid4()
            class Service:
                def retry_failed(self, requested): return retry
                def cancel_queued(self, requested): return SimpleNamespace(job_id=requested, asset_id=uuid4(), status="cancelled", created_at="2026-01-01", updated_at="2026-01-01", run_id=None, error_message=None, cache_hit=False, trace_id=None, retry_of=None, as_dict=lambda: {"job_id": str(requested), "asset_id": str(uuid4()), "status": "cancelled", "created_at": "2026-01-01", "updated_at": "2026-01-01", "run_id": None, "error_message": None, "cache_hit": False, "trace_id": None, "retry_of": None})
                def get(self, requested): return SimpleNamespace(job_id=retry, asset_id=uuid4(), status="queued", created_at="2026-01-01", updated_at="2026-01-01", run_id=None, error_message=None, cache_hit=False, trace_id=None, retry_of=str(parent), as_dict=lambda: {"job_id": str(retry), "asset_id": str(uuid4()), "status": "queued", "created_at": "2026-01-01", "updated_at": "2026-01-01", "run_id": None, "error_message": None, "cache_hit": False, "trace_id": None, "retry_of": str(parent)})
            with __import__("tempfile").TemporaryDirectory() as directory:
                app.state.analysis_job_service = Service(); app.state.audit_ledger = AuditLedger(__import__("pathlib").Path(directory) / "ledger.sqlite3")
                with TestClient(app) as client:
                    response = client.post(f"/api/v2/agent/jobs/{parent}/retry?confirm=true&trace_id={trace_id}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(app.state.audit_ledger.list_events(trace_id=str(trace_id))[0]["event_type"], "analysis_retried")
        finally:
            app.state.analysis_job_service = original_service; app.state.audit_ledger = original_ledger


if __name__ == "__main__":
    unittest.main()
