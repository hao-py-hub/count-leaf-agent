import tempfile
import unittest
from pathlib import Path

from app.services.audit_ledger import AuditLedger
from app.services.domain_events import DomainEvent, SqliteDomainEventPublisher


class DomainEventReconciliationTests(unittest.TestCase):
    def test_projection_and_audit_counts_are_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "ledger.sqlite3"
            publisher = SqliteDomainEventPublisher(database)
            ledger = AuditLedger(database)
            event = DomainEvent("AnalysisCompleted", "AnalysisRun", "job-1", "trial-a", "trace-a")
            publisher.publish(event)
            ledger.event(event_type="analysis_completed", source="worker", trace_id="trace-a", resource_id="asset-1")
            report = publisher.reconcile_audit_events(project_id="trial-a", trace_id="trace-a")
            self.assertTrue(report["in_sync"])
            self.assertEqual(report["comparisons"]["AnalysisCompleted"]["difference"], 0)

    def test_manual_review_event_reconciles_with_review_created_audit_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "ledger.sqlite3"
            publisher = SqliteDomainEventPublisher(database)
            ledger = AuditLedger(database)
            publisher.publish(DomainEvent("ReviewCreated", "ReviewCase", "review-1", "trial-a", "trace-a"))
            ledger.event(event_type="review_created", source="review_api", trace_id="trace-a", resource_id="review-1")
            report = publisher.reconcile_audit_events(project_id="trial-a", trace_id="trace-a")
            self.assertTrue(report["in_sync"])
            self.assertEqual(report["comparisons"]["ReviewCreated"]["difference"], 0)
