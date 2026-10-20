from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from uuid import uuid4

from app.services.audit_ledger import AuditLedger
from app.services.domain_events import InMemoryDomainEventPublisher
from app.services.report_job_service import ReportJobService


class ReportAuditLifecycleTests(unittest.TestCase):
    def _wait(self, service, job_id, ledger, trace_id):
        deadline = time.monotonic() + 2
        while (service.get(job_id).status in {"queued", "running"} or not ledger.list_events(trace_id=trace_id)) and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_report_completion_is_audited(self):
        with TemporaryDirectory() as directory:
            ledger = AuditLedger(Path(directory) / "ledger.sqlite3"); trace_id = str(uuid4())
            service = ReportJobService(lambda job_id, question: "报告", audit_event=ledger.event)
            job = service.submit(uuid4(), "问题", trace_id=trace_id); self._wait(service, job.report_job_id, ledger, trace_id)
            self.assertEqual(ledger.list_events(trace_id=trace_id)[0]["event_type"], "report_completed")

    def test_report_failure_is_audited(self):
        with TemporaryDirectory() as directory:
            ledger = AuditLedger(Path(directory) / "ledger.sqlite3"); trace_id = str(uuid4())
            service = ReportJobService(lambda job_id, question: (_ for _ in ()).throw(RuntimeError("x")), audit_event=ledger.event)
            job = service.submit(uuid4(), "问题", trace_id=trace_id); self._wait(service, job.report_job_id, ledger, trace_id)
            self.assertEqual(ledger.list_events(trace_id=trace_id)[0]["event_type"], "report_failed")

    def test_completed_report_publishes_project_scoped_domain_event(self):
        publisher = InMemoryDomainEventPublisher()
        service = ReportJobService(
            lambda job_id, question: "报告", domain_event_publisher=publisher,
        )
        job = service.submit(uuid4(), "问题", trace_id="trace-report", project_id="trial-a")
        deadline = time.monotonic() + 2
        while service.get(job.report_job_id).status in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual([event.event_type for event in publisher.events], ["ReportGenerated"])
        self.assertEqual(publisher.events[0].project_id, "trial-a")
        self.assertEqual(publisher.events[0].trace_id, "trace-report")

    def test_completed_report_survives_service_recreation(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "reports.sqlite3"
            service = ReportJobService(lambda job_id, question: "持久化报告", database_path=database_path)
            job = service.submit(uuid4(), "问题", project_id="project-a")
            deadline = time.monotonic() + 2
            while service.get(job.report_job_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)

            restored = ReportJobService(lambda job_id, question: "不应执行", database_path=database_path)
            record = restored.get(job.report_job_id)
            self.assertEqual(record.status, "completed")
            self.assertEqual(record.report, "持久化报告")
            self.assertEqual(record.project_id, "project-a")

    def test_incomplete_report_is_marked_failed_after_recreation(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "reports.sqlite3"
            service = ReportJobService(lambda job_id, question: "报告", database_path=database_path)
            job = service.submit(uuid4(), "问题")
            deadline = time.monotonic() + 2
            while service.get(job.report_job_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
            with service._connect() as connection:
                connection.execute("UPDATE report_jobs SET status='running', report=NULL WHERE report_job_id=?", (str(job.report_job_id),))

            restored = ReportJobService(lambda job_id, question: "不应执行", database_path=database_path)
            record = restored.get(job.report_job_id)
            self.assertEqual(record.status, "failed")
            self.assertEqual(record.error_message, "服务重启前报告任务未完成")
