from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger
from app.services.review_service import ReviewService


class CompletedJobService:
    def __init__(self, job_id):
        self._job_id = job_id

    def get(self, job_id):
        if job_id != self._job_id:
            raise KeyError(job_id)
        return SimpleNamespace(status="completed")


class ReviewAuditLedgerRouteTests(unittest.TestCase):
    def test_confirmed_review_creation_records_trace_linked_event(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            job_id = uuid4()
            original_jobs = app.state.analysis_job_service
            original_reviews = app.state.review_service
            original_ledger = app.state.audit_ledger
            try:
                app.state.analysis_job_service = CompletedJobService(job_id)
                app.state.review_service = ReviewService(root / "reviews.sqlite3")
                app.state.audit_ledger = AuditLedger(root / "ledger.sqlite3")
                trace_id = uuid4()
                with TestClient(app) as client:
                    response = client.post("/api/v2/reviews", json={
                        "job_id": str(job_id), "reason": "需要人工确认", "confirm": True,
                        "trace_id": str(trace_id),
                    })
                self.assertEqual(response.status_code, 200)
                events = app.state.audit_ledger.list_events(trace_id=str(trace_id))
                self.assertEqual(events[0]["event_type"], "review_created")
                self.assertEqual(events[0]["resource_id"], str(job_id))
            finally:
                app.state.analysis_job_service = original_jobs
                app.state.review_service = original_reviews
                app.state.audit_ledger = original_ledger


if __name__ == "__main__":
    unittest.main()
