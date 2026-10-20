"""Privacy-aware audit trail for bounded Agent invocations."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import sqlite3

from app.agent.models import AgentChatRequest, AgentChatResponse


class AgentAuditService:
    """Persists minimal operational metadata without retaining message content."""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database_path = database_path
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS agent_audit_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    message_sha256 TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_tool TEXT,
                    asset_id TEXT,
                    job_id TEXT,
                    report_job_id TEXT,
                    citation_count INTEGER NOT NULL,
                    skill_name TEXT,
                    skill_version TEXT,
                    project_id TEXT NOT NULL DEFAULT 'default'
                )"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_audit_events)")}
            if "trace_id" not in columns:
                connection.execute("ALTER TABLE agent_audit_events ADD COLUMN trace_id TEXT")
            if "skill_name" not in columns:
                connection.execute("ALTER TABLE agent_audit_events ADD COLUMN skill_name TEXT")
            if "skill_version" not in columns:
                connection.execute("ALTER TABLE agent_audit_events ADD COLUMN skill_version TEXT")
            if "report_job_id" not in columns:
                connection.execute("ALTER TABLE agent_audit_events ADD COLUMN report_job_id TEXT")
            if "project_id" not in columns:
                connection.execute("ALTER TABLE agent_audit_events ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")

    def record(self, request: AgentChatRequest, response: AgentChatResponse) -> None:
        """Record operational metadata for a completed Agent workflow invocation."""
        message_hash = hashlib.sha256(request.message.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_audit_events(
                    occurred_at, trace_id, message_sha256, intent, status, requested_tool,
                    asset_id, job_id, report_job_id, citation_count, skill_name, skill_version, project_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    str(request.trace_id),
                    message_hash,
                    response.intent.value,
                    response.status.value,
                    response.requested_tool.value if response.requested_tool else None,
                    str(request.asset_id) if request.asset_id else None,
                    str(response.job_id) if response.job_id else None,
                    str(response.report_job_id) if response.report_job_id else None,
                    len(response.citations),
                    response.skill_name,
                    response.skill_version,
                    request.project_id,
                ),
            )

    def list_events(self, *, limit: int = 50, trace_id: str | None = None, project_id: str | None = None) -> list[dict]:
        """Return recent audit events, intentionally excluding user message content."""
        with self._connect() as connection:
            if trace_id and project_id:
                rows = connection.execute(
                    """SELECT occurred_at, trace_id, message_sha256, intent, status, requested_tool,
                       asset_id, job_id, report_job_id, citation_count, skill_name, skill_version
                       FROM agent_audit_events WHERE trace_id = ? AND project_id = ? ORDER BY id DESC LIMIT ?""",
                    (trace_id, project_id, limit),
                ).fetchall()
            elif trace_id:
                rows = connection.execute(
                    """SELECT occurred_at, trace_id, message_sha256, intent, status, requested_tool,
                       asset_id, job_id, report_job_id, citation_count, skill_name, skill_version
                       FROM agent_audit_events WHERE trace_id = ? ORDER BY id DESC LIMIT ?""",
                    (trace_id, limit),
                ).fetchall()
            elif project_id:
                rows = connection.execute(
                    """SELECT occurred_at, trace_id, message_sha256, intent, status, requested_tool,
                       asset_id, job_id, report_job_id, citation_count, skill_name, skill_version
                       FROM agent_audit_events WHERE project_id = ? ORDER BY id DESC LIMIT ?""",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT occurred_at, trace_id, message_sha256, intent, status, requested_tool,
                       asset_id, job_id, report_job_id, citation_count, skill_name, skill_version
                       FROM agent_audit_events ORDER BY id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        keys = ("occurred_at", "trace_id", "message_sha256", "intent", "status", "requested_tool", "asset_id", "job_id", "report_job_id", "citation_count", "skill_name", "skill_version")
        return [dict(zip(keys, row, strict=True)) for row in rows]

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
