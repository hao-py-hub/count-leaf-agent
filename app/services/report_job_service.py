"""本地部署的进程内异步报告生成服务。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID, uuid4
from pathlib import Path
import sqlite3
from contextlib import contextmanager

from .domain_events import DomainEvent, DomainEventPublisher


@dataclass
class ReportJob:
    report_job_id: UUID
    analysis_job_id: UUID
    status: str
    created_at: str
    report: str | None = None
    error_message: str | None = None
    trace_id: str | None = None
    project_id: str = "default"


class ReportJobService:
    def __init__(self, generate, audit_event=None, domain_event_publisher: DomainEventPublisher | None = None, database_path: Path | None = None):
        self._generate = generate
        self._audit_event = audit_event
        self._domain_event_publisher = domain_event_publisher
        self._jobs = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report")
        self._database_path = database_path
        if database_path:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS report_jobs(report_job_id TEXT PRIMARY KEY, analysis_job_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, report TEXT, error_message TEXT, trace_id TEXT, project_id TEXT NOT NULL)")
                connection.execute("UPDATE report_jobs SET status='failed', error_message='服务重启前报告任务未完成' WHERE status IN ('queued','running')")

    def submit(self, analysis_job_id: UUID, question: str | None, trace_id: str | None = None, project_id: str = "default") -> ReportJob:
        job = ReportJob(uuid4(), analysis_job_id, "queued", datetime.now(timezone.utc).isoformat(timespec="seconds"), trace_id=trace_id, project_id=project_id)
        with self._lock:
            self._jobs[job.report_job_id] = job
        self._save(job)
        self._executor.submit(self._run, job.report_job_id, question)
        return job

    def get(self, report_job_id: UUID) -> ReportJob:
        with self._lock:
            if report_job_id in self._jobs:
                return ReportJob(**self._jobs[report_job_id].__dict__)
        if self._database_path:
            with self._connect() as connection:
                row = connection.execute("SELECT report_job_id, analysis_job_id, status, created_at, report, error_message, trace_id, project_id FROM report_jobs WHERE report_job_id=?", (str(report_job_id),)).fetchone()
            if row:
                return ReportJob(UUID(row[0]), UUID(row[1]), row[2], row[3], row[4], row[5], row[6], row[7])
        raise KeyError("报告任务不存在")

    def _run(self, report_job_id: UUID, question: str | None) -> None:
        with self._lock:
            job = self._jobs[report_job_id]
            job.status = "running"
            self._save(job)
        try:
            report = self._generate(job.analysis_job_id, question or "")
        except Exception:
            with self._lock:
                self._jobs[report_job_id].status = "failed"
                self._jobs[report_job_id].error_message = "报告生成失败"
                self._save(self._jobs[report_job_id])
            if self._audit_event:
                self._audit_event(event_type="report_failed", source="report_worker", trace_id=job.trace_id, resource_id=str(report_job_id), status="failed", metadata={"analysis_job_id": str(job.analysis_job_id)}, project_id=job.project_id)
            return
        with self._lock:
            self._jobs[report_job_id].status = "completed"
            self._jobs[report_job_id].report = report
            self._save(self._jobs[report_job_id])
        if self._audit_event:
            self._audit_event(event_type="report_completed", source="report_worker", trace_id=job.trace_id, resource_id=str(report_job_id), status="completed", metadata={"analysis_job_id": str(job.analysis_job_id)}, project_id=job.project_id)
        if self._domain_event_publisher:
            self._domain_event_publisher.publish(DomainEvent(
                "ReportGenerated", "PhenotypeReport", str(report_job_id), job.project_id,
                job.trace_id, metadata={"analysis_job_id": str(job.analysis_job_id)},
            ))

    def _save(self, job: ReportJob) -> None:
        if not self._database_path:
            return
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO report_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(job.report_job_id), str(job.analysis_job_id), job.status, job.created_at, job.report, job.error_message, job.trace_id, job.project_id))

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._database_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
