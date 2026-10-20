"""Human review task lifecycle for analysis results."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4


class ReviewService:
    ALLOWED_REASON_CODES = frozenset({"manual", "image_quality", "count_mismatch", "model_conflict"})
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database_path = database_path
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS review_tasks(
                review_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, reason TEXT NOT NULL,
                status TEXT NOT NULL, resolution TEXT, created_at TEXT NOT NULL, resolved_at TEXT,
                run_id TEXT, history_revision INTEGER, project_id TEXT NOT NULL DEFAULT 'default'
            )""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(review_tasks)")}
            if "run_id" not in columns:
                connection.execute("ALTER TABLE review_tasks ADD COLUMN run_id TEXT")
            if "history_revision" not in columns:
                connection.execute("ALTER TABLE review_tasks ADD COLUMN history_revision INTEGER")
            if "trace_id" not in columns:
                connection.execute("ALTER TABLE review_tasks ADD COLUMN trace_id TEXT")
            if "reason_code" not in columns:
                connection.execute("ALTER TABLE review_tasks ADD COLUMN reason_code TEXT")
            if "project_id" not in columns:
                connection.execute("ALTER TABLE review_tasks ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")

    def create(self, job_id: UUID, reason: str, trace_id: str | None = None, reason_code: str = "manual", project_id: str = "default") -> dict:
        if not reason.strip():
            raise ValueError("复核原因不能为空")
        review_id = uuid4()
        if not reason_code.strip():
            raise ValueError("复核原因码不能为空")
        codes = [code.strip() for code in reason_code.split(",") if code.strip()]
        unknown = set(codes) - self.ALLOWED_REASON_CODES
        if unknown:
            raise ValueError("不支持的复核原因码")
        reason_code = ",".join(dict.fromkeys(codes))
        project_id = project_id.strip()
        if not project_id or len(project_id) > 128:
            raise ValueError("project_id 无效")
        record = {"review_id": str(review_id), "job_id": str(job_id), "reason": reason.strip(), "reason_code": reason_code.strip(), "status": "open", "resolution": None, "created_at": self._now(), "resolved_at": None, "run_id": None, "history_revision": None, "trace_id": trace_id, "project_id": project_id}
        with self._connect() as connection:
            connection.execute("INSERT INTO review_tasks(review_id, job_id, reason, reason_code, status, resolution, created_at, resolved_at, run_id, history_revision, trace_id, project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(record.values()))
        return record

    def get(self, review_id: UUID) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT review_id, job_id, reason, reason_code, status, resolution, created_at, resolved_at, run_id, history_revision, trace_id, project_id FROM review_tasks WHERE review_id = ?", (str(review_id),)).fetchone()
        if not row:
            raise KeyError("复核任务不存在")
        return self._record(row)

    def list_recent(self, *, limit: int = 50, reason_code: str | None = None, project_id: str = "default") -> list[dict]:
        """List review metadata for the local review queue."""
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须介于 1 和 100")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT review_id, job_id, reason, reason_code, status, resolution, created_at, resolved_at, run_id, history_revision, trace_id, project_id "
                "FROM review_tasks WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (project_id, limit)
            ).fetchall()
        records = [self._record(row) for row in rows]
        if reason_code:
            records = [record for record in records if reason_code in record["reason_code"].split(",")]
        return records

    def list_for_job(self, job_id: UUID, *, project_id: str = "default") -> list[dict]:
        """读取指定分析任务在当前项目内的复核反馈记录。"""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT review_id, job_id, reason, reason_code, status, resolution, created_at, resolved_at, run_id, history_revision, trace_id, project_id "
                "FROM review_tasks WHERE job_id = ? AND project_id = ? ORDER BY created_at ASC, rowid ASC",
                (str(job_id), project_id),
            ).fetchall()
        return [self._record(row) for row in rows]

    def feedback_summary_for_job(self, job_id: UUID, *, project_id: str = "default") -> dict:
        """汇总同一分析任务的结构化人工反馈，供解释层直接使用。"""
        records = self.list_for_job(job_id, project_id=project_id)
        reason_codes = sorted({code for record in records for code in record["reason_code"].split(",")})
        return {
            "review_count": len(records),
            "open_count": sum(record["status"] == "open" for record in records),
            "resolved_count": sum(record["status"] == "resolved" for record in records),
            "dismissed_count": sum(record["status"] == "dismissed" for record in records),
            "reason_codes": reason_codes,
            "latest_resolution": next((record["resolution"] for record in reversed(records) if record["resolution"]), None),
            "latest_history_revision": next((record["history_revision"] for record in reversed(records) if record["history_revision"]), None),
        }

    def resolve(self, review_id: UUID, resolution: str, *, run_id: str, history_revision: int) -> dict:
        if not resolution.strip():
            raise ValueError("复核结论不能为空")
        if not run_id.strip() or history_revision < 1:
            raise ValueError("复核修订版本无效")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE review_tasks SET status = 'resolved', resolution = ?, resolved_at = ?, run_id = ?, history_revision = ? WHERE review_id = ? AND status = 'open'",
                (resolution.strip(), self._now(), run_id, history_revision, str(review_id)),
            )
        if not cursor.rowcount:
            raise KeyError("复核任务不存在或已处理")
        return self.get(review_id)

    def dismiss(self, review_id: UUID, resolution: str = "无需修改") -> dict:
        """Close a review while explicitly retaining the automatic result."""
        if not resolution.strip():
            raise ValueError("复核结论不能为空")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE review_tasks SET status = 'dismissed', resolution = ?, resolved_at = ? WHERE review_id = ? AND status = 'open'",
                (resolution.strip(), self._now(), str(review_id)),
            )
        if not cursor.rowcount:
            raise KeyError("复核任务不存在或已处理")
        return self.get(review_id)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _record(row: tuple) -> dict:
        record = dict(zip(("review_id", "job_id", "reason", "reason_code", "status", "resolution", "created_at", "resolved_at", "run_id", "history_revision", "trace_id", "project_id"), row, strict=True))
        record["reason_code"] = record["reason_code"] or "legacy"
        return record

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
