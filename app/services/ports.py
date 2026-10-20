"""Application-layer persistence and execution boundaries.

Concrete infrastructure must depend on these protocols; HTTP routes and
agent workflows only interact with the application services built on them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


class AssetRepository(Protocol):
    """Stores uploaded assets and their immutable metadata."""

    def create(self, *, asset_id: str, file_path: Path, content_type: str, filename: str) -> dict[str, Any]:
        """Persist an uploaded asset and return its public record."""

    def get(self, asset_id: str) -> dict[str, Any] | None:
        """Return an asset record when it exists."""


class AnalysisJobRepository(Protocol):
    """Stores state transitions for phenotype analysis jobs."""

    def create(self, *, job_id: str, asset_id: str, created_at: datetime, updated_at: datetime | None = None, cache_key: str | None = None, trace_id: str | None = None) -> dict[str, Any]:
        """Persist a queued job and return its record."""

    def get(self, job_id: str) -> dict[str, Any] | None:
        """Return a job record when it exists."""

    def get_completed_by_cache_key(self, cache_key: str) -> dict[str, Any] | None:
        """Return a reusable completed result for an immutable cache key."""

    def list_recent(self, *, limit: int) -> list[dict[str, Any]]:
        """Return recent job records in descending creation order."""

    def count_created_since(self, since: datetime) -> int:
        """Count jobs created at or after a UTC budget boundary."""

    def claim_queued(self, job_id: str, *, started_at: datetime) -> dict[str, Any] | None:
        """Atomically claim one queued job for execution, if still available."""

    def update(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        """Atomically persist a job state change and return the record."""


class AnalysisBatchRepository(Protocol):
    """Persists batch-to-job associations without duplicating job results."""

    def create(self, *, batch_id: str, job_ids: list[str], created_at: datetime, trace_id: str | None = None) -> None:
        """Store a batch and its child tasks."""

    def get(self, batch_id: str) -> dict[str, Any] | None:
        """Return a batch association record when it exists."""

    def list_recent(self, *, limit: int, project_id: str = "default") -> list[dict[str, Any]]:
        """返回指定项目最近的批次关联记录。"""
