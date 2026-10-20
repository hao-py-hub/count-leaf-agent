"""Small domain-event boundary for the local enterprise workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4
import json
import sqlite3
from pathlib import Path


@dataclass(frozen=True)
class DomainEvent:
    event_type: str
    aggregate_type: str
    aggregate_id: str
    project_id: str
    trace_id: str | None
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))


class DomainEventPublisher(Protocol):
    def publish(self, event: DomainEvent) -> None: ...


class InMemoryDomainEventPublisher:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        self.events.append(event)


class SqliteDomainEventPublisher:
    """Durable local event projection with idempotent event IDs."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("""CREATE TABLE IF NOT EXISTS domain_events(
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL,
                project_id TEXT NOT NULL, trace_id TEXT, occurred_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL)""")
            connection.commit()
        finally:
            connection.close()

    def publish(self, event: DomainEvent) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("""INSERT OR IGNORE INTO domain_events(
                event_id,event_type,aggregate_type,aggregate_id,project_id,
                trace_id,occurred_at,metadata_json) VALUES (?,?,?,?,?,?,?,?)""",
                (event.event_id, event.event_type, event.aggregate_type,
                 event.aggregate_id, event.project_id, event.trace_id,
                 event.occurred_at, json.dumps(event.metadata, ensure_ascii=False, sort_keys=True)))
            connection.commit()
        finally:
            connection.close()

    def list_events(self, *, project_id: str | None = None, trace_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT event_id,event_type,aggregate_type,aggregate_id,project_id,trace_id,occurred_at,metadata_json FROM domain_events"
        params: list[Any] = []
        filters = []
        if project_id:
            filters.append("project_id = ?"); params.append(project_id)
        if trace_id:
            filters.append("trace_id = ?"); params.append(trace_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY occurred_at DESC LIMIT ?"; params.append(limit)
        connection = sqlite3.connect(self._database_path)
        try:
            rows = connection.execute(query, params).fetchall()
        finally:
            connection.close()
        keys = ("event_id", "event_type", "aggregate_type", "aggregate_id", "project_id", "trace_id", "occurred_at", "metadata_json")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def reconcile_audit_events(self, *, project_id: str = "default", trace_id: str | None = None) -> dict[str, Any]:
        """Compare lifecycle projection counts with the shared audit ledger."""
        mapping = {
            "AnalysisSubmitted": "analysis_submitted", "AnalysisCompleted": "analysis_completed",
            "AnalysisFailed": "analysis_failed", "ReviewCreated": ("analysis_review_required", "review_created"),
            "CacheRevoked": "cache_revoked", "ReviewResolved": ("review_resolved", "review_dismissed"),
            "ReportGenerated": "report_completed",
        }
        connection = sqlite3.connect(self._database_path)
        try:
            domain_params: list[Any] = [project_id]
            domain_query = "SELECT event_type, COUNT(*) FROM domain_events WHERE project_id = ?"
            if trace_id:
                domain_query += " AND trace_id = ?"; domain_params.append(trace_id)
            domain_query += " GROUP BY event_type"
            domain_counts = dict(connection.execute(domain_query, domain_params).fetchall())
            audit_params: list[Any] = []
            audit_event_types = [event_type for value in mapping.values() for event_type in (value if isinstance(value, tuple) else (value,))]
            audit_query = "SELECT event_type, COUNT(*) FROM audit_events WHERE event_type IN (" + ",".join("?" for _ in audit_event_types) + ")"
            audit_params.extend(audit_event_types)
            if trace_id:
                audit_query += " AND trace_id = ?"; audit_params.append(trace_id)
            audit_query += " GROUP BY event_type"
            try:
                audit_counts = dict(connection.execute(audit_query, audit_params).fetchall())
            except sqlite3.OperationalError as error:
                if "no such table: audit_events" not in str(error):
                    raise
                audit_counts = {}
        finally:
            connection.close()
        comparisons = {
            name: {
                "domain": int(domain_counts.get(name, 0)),
                "audit": sum(int(audit_counts.get(event_type, 0)) for event_type in (audit_names if isinstance(audit_names, tuple) else (audit_names,))),
            }
            for name, audit_names in mapping.items()
        }
        for value in comparisons.values():
            value["difference"] = value["domain"] - value["audit"]
        return {"project_id": project_id, "trace_id": trace_id, "in_sync": all(item["difference"] == 0 for item in comparisons.values()), "comparisons": comparisons}
