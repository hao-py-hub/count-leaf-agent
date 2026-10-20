from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger


class BatchAuditEventsTests(unittest.TestCase):
    def test_confirmed_batch_submission_writes_audit_event(self) -> None:
        original_service = app.state.batch_analysis_service
        original_ledger = app.state.audit_ledger
        try:
            batch_id = uuid4(); trace_id = uuid4()
            class Service:
                def validate_assets(self, asset_ids): return None
                def submit_confirmed(self, asset_ids, trace_id=None): return SimpleNamespace(batch_id=batch_id, job_ids=[uuid4()])
            with TemporaryDirectory() as directory:
                app.state.batch_analysis_service = Service(); app.state.audit_ledger = AuditLedger(Path(directory) / "ledger.sqlite3")
                with TestClient(app) as client:
                    response = client.post("/api/v2/batches", json={"asset_ids": [str(uuid4())], "confirm": True, "trace_id": str(trace_id)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(app.state.audit_ledger.list_events(trace_id=str(trace_id))[0]["event_type"], "batch_submitted")
        finally:
            app.state.batch_analysis_service = original_service; app.state.audit_ledger = original_ledger

    def test_confirmed_batch_cancel_records_cancelled_and_running_counts(self) -> None:
        original_service = app.state.batch_analysis_service
        original_ledger = app.state.audit_ledger
        try:
            batch_id = uuid4(); trace_id = uuid4()
            test_case = self
            class Service:
                def get_status(self, requested_batch_id):
                    test_case.assertEqual(requested_batch_id, batch_id)
                    return {"project_id": "default"}
                def cancel_queued(self, requested_batch_id):
                    test_case.assertEqual(requested_batch_id, batch_id)
                    return {"batch_id": str(batch_id), "cancelled_count": 2, "running_count": 1}
            with TemporaryDirectory() as directory:
                app.state.batch_analysis_service = Service(); app.state.audit_ledger = AuditLedger(Path(directory) / "ledger.sqlite3")
                with TestClient(app) as client:
                    response = client.post(f"/api/v2/batches/{batch_id}/cancel", json={"confirm": True, "trace_id": str(trace_id)})
                self.assertEqual(response.status_code, 200)
                event = app.state.audit_ledger.list_events(trace_id=str(trace_id))[0]
                self.assertEqual(event["event_type"], "batch_cancelled")
                self.assertEqual(event["metadata"], {"cancelled_count": 2, "running_count": 1})
        finally:
            app.state.batch_analysis_service = original_service; app.state.audit_ledger = original_ledger


if __name__ == "__main__":
    unittest.main()
