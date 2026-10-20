"""Privacy-preserving persistence for replayable Agent conversations."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from collections.abc import Iterator
from pathlib import Path
import json
import sqlite3

from app.agent.models import AgentChatRequest, AgentChatResponse


class AgentConversationService:
    """Stores Agent outcomes and tool metadata without retaining raw user messages."""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database_path = database_path
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS agent_conversations(
                    conversation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT 'default'
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS agent_conversation_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, trace_id TEXT NOT NULL,
                    message_sha256 TEXT NOT NULL, intent TEXT NOT NULL, status TEXT NOT NULL,
                    requested_tool TEXT, job_id TEXT, review_id TEXT, report_job_id TEXT,
                    response_message TEXT NOT NULL, citations_json TEXT NOT NULL,
                    skill_name TEXT, skill_version TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS agent_tool_calls(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, trace_id TEXT NOT NULL, tool_name TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL, result_status TEXT NOT NULL,
                    result_reference TEXT, duration_ms INTEGER NOT NULL,
                    call_origin TEXT NOT NULL DEFAULT 'function_call'
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS agent_session_summary_cache(
                    conversation_id TEXT NOT NULL, cache_key TEXT NOT NULL,
                    history_version TEXT NOT NULL, summary_json TEXT NOT NULL,
                    reference_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, cache_key)
                )"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_tool_calls)")}
            conversation_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_conversations)")}
            if "project_id" not in conversation_columns:
                connection.execute("ALTER TABLE agent_conversations ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
            if "call_origin" not in columns:
                connection.execute(
                    "ALTER TABLE agent_tool_calls ADD COLUMN call_origin TEXT NOT NULL DEFAULT 'function_call'"
                )
            event_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_conversation_events)")}
            if "skill_name" not in event_columns:
                connection.execute("ALTER TABLE agent_conversation_events ADD COLUMN skill_name TEXT")
            if "skill_version" not in event_columns:
                connection.execute("ALTER TABLE agent_conversation_events ADD COLUMN skill_version TEXT")
            if "report_job_id" not in event_columns:
                connection.execute("ALTER TABLE agent_conversation_events ADD COLUMN report_job_id TEXT")

    def record(self, request: AgentChatRequest, response: AgentChatResponse, *, duration_ms: int) -> None:
        now = self._now()
        conversation_id = str(request.conversation_id)
        with self._connect() as connection:
            existing = connection.execute("SELECT project_id FROM agent_conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
            if existing and existing[0] != request.project_id:
                raise ValueError("会话不能跨项目继续使用")
            connection.execute(
                """INSERT INTO agent_conversations(conversation_id, created_at, updated_at, project_id)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET updated_at = excluded.updated_at""",
                (conversation_id, now, now, request.project_id),
            )
            connection.execute(
                """INSERT INTO agent_conversation_events(
                    conversation_id, occurred_at, trace_id, message_sha256, intent, status,
                    requested_tool, job_id, review_id, report_job_id, response_message, citations_json,
                    skill_name, skill_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    conversation_id, now, str(request.trace_id), self._hash(request.message),
                    response.intent.value, response.status.value,
                    response.requested_tool.value if response.requested_tool else None,
                    str(response.job_id) if response.job_id else None,
                    str(response.review_id) if response.review_id else None,
                    str(response.report_job_id) if response.report_job_id else None,
                    response.message, json.dumps(response.citations, ensure_ascii=False),
                    response.skill_name,
                    response.skill_version,
                ),
            )
            if response.requested_tool:
                self._insert_tool_call(
                    connection, conversation_id=conversation_id, trace_id=str(request.trace_id),
                    tool_name=response.requested_tool.value, input_sha256=self._hash(request.model_dump_json()),
                    result_status=response.status.value,
                    result_reference=str(response.job_id or response.review_id or response.report_job_id or "") or None,
                    duration_ms=duration_ms, occurred_at=now,
                )

    def record_external_tool_call(
        self, *, conversation_id: str, trace_id: str, tool_name: str,
        arguments: str, result_status: str, result_reference: str | None, duration_ms: int,
        call_origin: str = "function_call",
    ) -> None:
        """Audit a direct enterprise read-only invocation without storing arguments."""
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_conversations(conversation_id, created_at, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET updated_at = excluded.updated_at""",
                (conversation_id, now, now),
            )
            self._insert_tool_call(
                connection, conversation_id=conversation_id, trace_id=trace_id, tool_name=tool_name,
                input_sha256=self._hash(arguments), result_status=result_status,
                result_reference=result_reference, duration_ms=duration_ms, occurred_at=now,
                call_origin=call_origin,
            )

    def list_events(self, conversation_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT occurred_at, trace_id, message_sha256, intent, status, requested_tool,
                   job_id, review_id, report_job_id, response_message, citations_json, skill_name, skill_version
                   FROM agent_conversation_events WHERE conversation_id = ? ORDER BY id ASC""",
                (conversation_id,),
            ).fetchall()
        if not rows:
            raise KeyError("会话不存在")
        keys = ("occurred_at", "trace_id", "message_sha256", "intent", "status", "requested_tool", "job_id", "review_id", "report_job_id", "response_message", "citations_json", "skill_name", "skill_version")
        events = []
        for row in rows:
            event = dict(zip(keys, row, strict=True))
            event["citations"] = json.loads(event.pop("citations_json"))
            events.append(event)
        return events

    def list_tool_calls(self, conversation_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT occurred_at, trace_id, tool_name, input_sha256, result_status,
                   result_reference, duration_ms, call_origin FROM agent_tool_calls
                   WHERE conversation_id = ? ORDER BY id ASC""",
                (conversation_id,),
            ).fetchall()
        if not rows:
            if not self._conversation_exists(conversation_id):
                raise KeyError("会话不存在")
            return []
        keys = ("occurred_at", "trace_id", "tool_name", "input_sha256", "result_status", "result_reference", "duration_ms", "call_origin")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def get_project_id(self, conversation_id: str) -> str:
        """读取会话所属项目，不存在时不泄露额外信息。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT project_id FROM agent_conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        if not row:
            raise KeyError("会话不存在")
        return str(row[0] or "default")

    def list_recent_tool_calls(self, *, limit: int = 50, project_id: str = "default") -> list[dict]:
        """List recent redacted direct-tool calls across local conversations."""
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须介于 1 和 200")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT calls.occurred_at, calls.conversation_id, calls.trace_id, calls.tool_name, calls.input_sha256,
                   calls.result_status, calls.result_reference, calls.duration_ms, calls.call_origin
                   FROM agent_tool_calls AS calls JOIN agent_conversations AS conversations
                   ON conversations.conversation_id = calls.conversation_id
                   WHERE conversations.project_id = ? ORDER BY calls.id DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        keys = ("occurred_at", "conversation_id", "trace_id", "tool_name", "input_sha256", "result_status", "result_reference", "duration_ms", "call_origin")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def get_session_summary(self, *, conversation_id: str, query: str, history_version: str) -> dict | None:
        """Read a cached, non-authoritative aggregate for one conversation."""
        cache_key = self._summary_cache_key(query)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT summary_json, reference_ids_json FROM agent_session_summary_cache "
                "WHERE conversation_id = ? AND cache_key = ? AND history_version = ?",
                (conversation_id, cache_key, history_version),
            ).fetchone()
        if not row:
            return None
        summary = json.loads(row[0])
        summary["reference_ids"] = json.loads(row[1])
        summary["cache_hit"] = True
        return summary

    def cache_session_summary(self, *, conversation_id: str, query: str, history_version: str,
                              summary: dict, reference_ids: list[str] | None = None) -> dict:
        """Store only a bounded aggregate and opaque references, never user content."""
        sanitized = {key: value for key, value in summary.items() if key in {
            "scope_query", "sample_count", "completed_count", "failed_count", "average_total_pods"
        }}
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO agent_session_summary_cache(
                    conversation_id, cache_key, history_version, summary_json, reference_ids_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, cache_key) DO UPDATE SET
                    history_version = excluded.history_version, summary_json = excluded.summary_json,
                    reference_ids_json = excluded.reference_ids_json, created_at = excluded.created_at""",
                (conversation_id, self._summary_cache_key(query), history_version,
                 json.dumps(sanitized, ensure_ascii=False, sort_keys=True),
                 json.dumps(reference_ids or [], ensure_ascii=False), self._now()),
            )
        return {**sanitized, "reference_ids": list(reference_ids or []), "cache_hit": False}

    def list_trace_events(self, trace_id: str, *, limit: int = 100, project_id: str = "default") -> dict[str, list[dict]]:
        """按追踪 ID 聚合对话事件和外部工具调用。"""
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须介于 1 和 200")
        with self._connect() as connection:
            event_rows = connection.execute(
                """SELECT events.occurred_at, events.conversation_id, events.trace_id, events.message_sha256, events.intent, events.status,
                   events.requested_tool, events.job_id, events.review_id, events.report_job_id, events.response_message, events.citations_json,
                   skill_name, skill_version FROM agent_conversation_events AS events
                   JOIN agent_conversations AS conversations ON conversations.conversation_id = events.conversation_id
                   WHERE events.trace_id = ? AND conversations.project_id = ? ORDER BY events.id ASC LIMIT ?""", (trace_id, project_id, limit)
            ).fetchall()
            tool_rows = connection.execute(
                """SELECT calls.occurred_at, calls.conversation_id, calls.trace_id, calls.tool_name, calls.input_sha256,
                   calls.result_status, calls.result_reference, calls.duration_ms, calls.call_origin FROM agent_tool_calls AS calls
                   JOIN agent_conversations AS conversations ON conversations.conversation_id = calls.conversation_id
                   WHERE calls.trace_id = ? AND conversations.project_id = ? ORDER BY calls.id ASC LIMIT ?""", (trace_id, project_id, limit)
            ).fetchall()
        event_keys = ("occurred_at", "conversation_id", "trace_id", "message_sha256", "intent", "status", "requested_tool", "job_id", "review_id", "report_job_id", "response_message", "citations_json", "skill_name", "skill_version")
        events = []
        for row in event_rows:
            event = dict(zip(event_keys, row, strict=True))
            event["citations"] = json.loads(event.pop("citations_json"))
            events.append(event)
        tool_keys = ("occurred_at", "conversation_id", "trace_id", "tool_name", "input_sha256", "result_status", "result_reference", "duration_ms", "call_origin")
        return {"trace_id": trace_id, "events": events, "tool_calls": [dict(zip(tool_keys, row, strict=True)) for row in tool_rows]}

    def _conversation_exists(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM agent_conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        return row is not None

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
    def _hash(message: str) -> str:
        return hashlib.sha256(message.encode("utf-8")).hexdigest()

    @classmethod
    def _summary_cache_key(cls, query: str) -> str:
        return cls._hash(" ".join(query.lower().split()))

    @staticmethod
    def _insert_tool_call(
        connection: sqlite3.Connection, *, conversation_id: str, trace_id: str, tool_name: str,
        input_sha256: str, result_status: str, result_reference: str | None,
        duration_ms: int, occurred_at: str, call_origin: str = "agent_workflow",
    ) -> None:
        connection.execute(
            """INSERT INTO agent_tool_calls(
                conversation_id, occurred_at, trace_id, tool_name, input_sha256,
                result_status, result_reference, duration_ms, call_origin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, occurred_at, trace_id, tool_name, input_sha256,
             result_status, result_reference, duration_ms, call_origin),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
