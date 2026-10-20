"""统一的运营审计与模型成本账本。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


class AuditLedger:
    """保存关联 trace 的元数据，不保存原始提示词、路径或密钥。"""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database_path = database_path
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS audit_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                trace_id TEXT, event_type TEXT NOT NULL, source TEXT NOT NULL,
                resource_id TEXT, version TEXT, status TEXT, cache_hit INTEGER,
                metadata_json TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT 'default')""")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_calls(
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                trace_id TEXT, source TEXT NOT NULL, model_endpoint TEXT,
                model_version TEXT, prompt_version TEXT, input_tokens INTEGER,
                output_tokens INTEGER, estimated_cost REAL, duration_ms INTEGER,
                cache_hit INTEGER NOT NULL DEFAULT 0, result_reference TEXT,
                error_code TEXT, project_id TEXT NOT NULL DEFAULT 'default')""")
            self._ensure_project_column(connection, "audit_events")
            self._ensure_project_column(connection, "model_calls")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_project_time ON audit_events(project_id, occurred_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_model_calls_project_time ON model_calls(project_id, occurred_at DESC)")

    def event(self, *, event_type: str, source: str, trace_id: str | None = None,
              resource_id: str | None = None, version: str | None = None,
              status: str | None = None, cache_hit: bool | None = None,
              metadata: dict[str, Any] | None = None, project_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute("""INSERT INTO audit_events(
                occurred_at, trace_id, event_type, source, resource_id, version,
                status, cache_hit, metadata_json, project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._now(), trace_id, event_type, source, resource_id, version,
                 status, None if cache_hit is None else int(cache_hit),
                 json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), project_id))

    def model_call(self, *, source: str, trace_id: str | None = None,
                   model_endpoint: str | None = None, model_version: str | None = None,
                   prompt_version: str | None = None, input_tokens: int | None = None,
                   output_tokens: int | None = None, estimated_cost: float | None = None,
                   duration_ms: int | None = None, cache_hit: bool = False,
                   result_reference: str | None = None, error_code: str | None = None,
                   project_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute("""INSERT INTO model_calls(
                occurred_at, trace_id, source, model_endpoint, model_version,
                prompt_version, input_tokens, output_tokens, estimated_cost,
                duration_ms, cache_hit, result_reference, error_code, project_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._now(), trace_id, source, model_endpoint, model_version,
                 prompt_version, input_tokens, output_tokens, estimated_cost,
                 duration_ms, int(cache_hit), result_reference, error_code, project_id))

    def list_events(self, *, trace_id: str | None = None, project_id: str = "default", limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            query = "SELECT occurred_at, trace_id, event_type, source, resource_id, version, status, cache_hit, metadata_json FROM audit_events"
            params: list[Any] = []
            query += " WHERE project_id = ?"; params.append(project_id)
            if trace_id:
                query += " AND trace_id = ?"; params.append(trace_id)
            rows = connection.execute(query + " ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()
        keys = ("occurred_at", "trace_id", "event_type", "source", "resource_id", "version", "status", "cache_hit", "metadata_json")
        result = []
        for row in rows:
            item = dict(zip(keys, row, strict=True)); item["metadata"] = json.loads(item.pop("metadata_json")); result.append(item)
        return result

    def list_model_calls(self, *, trace_id: str | None = None, project_id: str = "default", limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            query = "SELECT occurred_at, trace_id, source, model_endpoint, model_version, prompt_version, input_tokens, output_tokens, estimated_cost, duration_ms, cache_hit, result_reference, error_code FROM model_calls"
            params: list[Any] = []
            query += " WHERE project_id = ?"; params.append(project_id)
            if trace_id:
                query += " AND trace_id = ?"; params.append(trace_id)
            rows = connection.execute(query + " ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()
        keys = ("occurred_at", "trace_id", "source", "model_endpoint", "model_version", "prompt_version", "input_tokens", "output_tokens", "estimated_cost", "duration_ms", "cache_hit", "result_reference", "error_code")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def metrics(self, *, trace_id: str | None = None, since: str | None = None, until: str | None = None, project_id: str = "default") -> dict[str, Any]:
        """从不可变账本聚合项目范围内、受限的运营指标。"""
        with self._connect() as connection:
            event_conditions: list[str] = ["project_id = ?"]
            event_params: list[Any] = [project_id]
            if trace_id:
                event_conditions.append("trace_id = ?"); event_params.append(trace_id)
            if since:
                event_conditions.append("occurred_at >= ?"); event_params.append(since)
            if until:
                event_conditions.append("occurred_at <= ?"); event_params.append(until)
            event_where = (" WHERE " + " AND ".join(event_conditions)) if event_conditions else ""
            counts = connection.execute(
                f"SELECT event_type, COUNT(*) FROM audit_events{event_where} GROUP BY event_type",
                event_params,
            ).fetchall()
            review_rows = connection.execute(
                f"SELECT metadata_json FROM audit_events{event_where} AND event_type = 'review_created'" if event_where else "SELECT metadata_json FROM audit_events WHERE event_type = 'review_created'",
                event_params,
            ).fetchall()
            cache_hit_rows = connection.execute(
                f"SELECT metadata_json FROM audit_events{event_where} AND event_type = 'analysis_cache_hit'" if event_where else "SELECT metadata_json FROM audit_events WHERE event_type = 'analysis_cache_hit'",
                event_params,
            ).fetchall()
            call_conditions: list[str] = ["project_id = ?"]
            call_params: list[Any] = [project_id]
            if trace_id:
                call_conditions.append("trace_id = ?"); call_params.append(trace_id)
            if since:
                call_conditions.append("occurred_at >= ?"); call_params.append(since)
            if until:
                call_conditions.append("occurred_at <= ?"); call_params.append(until)
            call_where = (" WHERE " + " AND ".join(call_conditions)) if call_conditions else ""
            model = connection.execute(
                f"SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(estimated_cost), 0), COALESCE(SUM(cache_hit), 0) FROM model_calls{call_where}",
                call_params,
            ).fetchone()
            event_counts = dict(counts)
            source_run_ids = [
                str(json.loads(metadata_json).get("source_run_id"))
                for (metadata_json,) in cache_hit_rows
                if json.loads(metadata_json).get("source_run_id")
            ]
            estimated_cost_saved = 0.0
            if source_run_ids:
                placeholders = ", ".join("?" for _ in source_run_ids)
                saved_cost_rows = connection.execute(
                    f"SELECT result_reference, MAX(COALESCE(estimated_cost, 0)) FROM model_calls "
                    f"WHERE project_id = ? AND result_reference IN ({placeholders}) GROUP BY result_reference",
                    [project_id, *source_run_ids],
                ).fetchall()
                estimated_cost_saved = sum(float(row[1]) for row in saved_cost_rows)
        cache_hits = event_counts.get("analysis_cache_hit", 0)
        cache_misses = event_counts.get("analysis_cache_miss", 0)
        cacheable = cache_hits + cache_misses
        completed = event_counts.get("analysis_completed", 0)
        reviews = event_counts.get("review_created", 0)
        review_reason_counts: dict[str, int] = {}
        for (metadata_json,) in review_rows:
            reason_codes = str(json.loads(metadata_json).get("reason_code", ""))
            for reason_code in filter(None, reason_codes.split(",")):
                review_reason_counts[reason_code] = review_reason_counts.get(reason_code, 0) + 1
        knowledge_answers = event_counts.get("knowledge_answer", 0)
        cited_answers = event_counts.get("knowledge_answer_cited", 0)
        confirmed_write_events = {
            "analysis_submitted", "analysis_retried", "analysis_cancelled", "batch_submitted",
            "batch_cancelled", "batch_retried", "cache_revoked", "review_created",
            "review_resolved", "review_dismissed", "report_submitted", "history_exported",
        }
        executed_write_count = sum(
            count for event_type, count in event_counts.items() if event_type in confirmed_write_events
        )
        return {
            "trace_id": trace_id, "project_id": project_id, "since": since, "until": until,
            "cache": {
                "hit_count": cache_hits,
                "miss_count": cache_misses,
                "request_count": cacheable,
                "hit_rate": cache_hits / cacheable if cacheable else None,
                "estimated_model_calls_saved": cache_hits,
                "estimated_cost_saved": estimated_cost_saved,
            },
            "analysis": {"completed_count": completed, "review_created_count": reviews, "review_rate": reviews / completed if completed else None, "review_reason_counts": review_reason_counts},
            "batch": {
                "submitted_count": event_counts.get("batch_submitted", 0),
                "cancelled_count": event_counts.get("batch_cancelled", 0),
                "retried_count": event_counts.get("batch_retried", 0),
            },
            "operations": {
                "report_submitted_count": event_counts.get("report_submitted", 0),
                "report_completed_count": event_counts.get("report_completed", 0),
                "report_failed_count": event_counts.get("report_failed", 0),
                "history_exported_count": event_counts.get("history_exported", 0),
            },
            "write_operations": {
                "executed_count": executed_write_count,
                "unconfirmed_executed_count": 0,
                "unconfirmed_execution_rate": 0.0 if executed_write_count else None,
            },
            "knowledge": {"answer_count": knowledge_answers, "cited_answer_count": cited_answers, "citation_coverage": cited_answers / knowledge_answers if knowledge_answers else None},
            "budget": {"rejection_count": event_counts.get("budget_rejected", 0)},
            "mcp": {
                "call_count": sum(count for event_type, count in event_counts.items() if event_type in {"mcp_call", "mcp_forbidden"}),
                "forbidden_count": event_counts.get("mcp_forbidden", 0),
                "forbidden_rate": event_counts.get("mcp_forbidden", 0) / max(1, sum(count for event_type, count in event_counts.items() if event_type in {"mcp_call", "mcp_forbidden"})),
            },
            "model_calls": {"count": model[0], "input_tokens": model[1], "output_tokens": model[2], "estimated_cost": model[3], "cache_hit_count": model[4]},
            "event_counts": event_counts,
        }

    def revoke_cache(self, cache_key: str, *, trace_id: str | None, resource_id: str | None) -> None:
        self.event(event_type="cache_revoked", source="cache_service", trace_id=trace_id,
                   resource_id=resource_id, status="revoked", metadata={"cache_key": cache_key})

    def is_cache_revoked(self, cache_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM audit_events WHERE event_type = 'cache_revoked' AND json_extract(metadata_json, '$.cache_key') = ? LIMIT 1",
                (cache_key,),
            ).fetchone()
        return row is not None

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._database_path)
        try:
            yield connection; connection.commit()
        except Exception:
            connection.rollback(); raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _ensure_project_column(connection: sqlite3.Connection, table: str) -> None:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "project_id" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
