from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from app.main import _create_review_with_trace, _resolve_review_with_trace, _submit_report_with_trace, app
from app.services.audit_ledger import AuditLedger
from app.services.review_service import ReviewService


class AgentReviewAuditTests(unittest.TestCase):
    def test_agent_review_callback_writes_unified_audit(self) -> None:
        original_reviews = app.state.review_service
        original_ledger = app.state.audit_ledger
        original_jobs = app.state.analysis_job_service
        try:
            with TemporaryDirectory() as temporary_directory:
                app.state.review_service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
                app.state.audit_ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                job_id = uuid4(); app.state.analysis_job_service = SimpleNamespace(get=lambda requested: SimpleNamespace(status="completed"))
                trace_id = uuid4()
                review_id = _create_review_with_trace(job_id, "人工复核", trace_id)
                events = app.state.audit_ledger.list_events(trace_id=str(trace_id))
                self.assertEqual(review_id, UUID(events[0]["metadata"]["review_id"]))
                self.assertEqual(events[0]["source"], "agent")
        finally:
            app.state.review_service = original_reviews
            app.state.audit_ledger = original_ledger
            app.state.analysis_job_service = original_jobs

    def test_agent_review_resolution_writes_request_trace(self) -> None:
        original_ledger = app.state.audit_ledger
        try:
            with TemporaryDirectory() as temporary_directory:
                app.state.audit_ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                review_id = uuid4(); trace_id = uuid4()
                with patch("app.main.ReviewResolutionService") as resolution_service:
                    resolution_service.return_value.resolve.return_value = {
                        "status": "resolved", "run_id": "run-1", "history_revision": 2,
                    }
                    _resolve_review_with_trace(review_id, "人工确认", {"plants": []}, trace_id)
                event = app.state.audit_ledger.list_events(trace_id=str(trace_id))[0]
                self.assertEqual(event["event_type"], "review_resolved")
                self.assertEqual(event["resource_id"], str(review_id))
        finally:
            app.state.audit_ledger = original_ledger

    def test_agent_report_submission_writes_request_trace(self) -> None:
        import app.main as main_module

        original_ledger = app.state.audit_ledger
        original_service = main_module.report_job_service
        try:
            with TemporaryDirectory() as temporary_directory:
                app.state.audit_ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                report_job_id = uuid4(); job_id = uuid4(); trace_id = uuid4()
                main_module.report_job_service = SimpleNamespace(
                    submit=lambda requested_job_id, question, trace_id=None: SimpleNamespace(
                        report_job_id=report_job_id, status="queued", trace_id=trace_id,
                    )
                )
                submitted = _submit_report_with_trace(job_id, "生成报告", trace_id)
                event = app.state.audit_ledger.list_events(trace_id=str(trace_id))[0]
                self.assertEqual(submitted, {"report_job_id": str(report_job_id), "status": "queued"})
                self.assertEqual(event["event_type"], "report_submitted")
                self.assertEqual(event["metadata"]["analysis_job_id"], str(job_id))
        finally:
            app.state.audit_ledger = original_ledger
            main_module.report_job_service = original_service


if __name__ == "__main__":
    unittest.main()
