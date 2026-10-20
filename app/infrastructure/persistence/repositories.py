"""SQLAlchemy implementations of application persistence ports."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func, select, update

from app.infrastructure.persistence.models import (
    AgentAssetModel,
    AnalysisBatchJobModel,
    AnalysisBatchModel,
    AnalysisJobModel,
)


class SqlAlchemyAssetRepository:
    """Persists uploaded assets independently of the API process lifetime."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(self, *, asset_id: str, file_path: Path, content_type: str, filename: str, source_kind: str = "agent_upload", project_id: str = "default") -> dict[str, Any]:
        with self._session_factory.begin() as session:
            asset = AgentAssetModel(
                asset_id=asset_id,
                file_path=str(file_path),
                content_type=content_type,
                filename=filename,
                source_kind=source_kind,
                project_id=project_id,
            )
            session.add(asset)
        return self._to_record(asset)

    def get(self, asset_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            asset = session.get(AgentAssetModel, asset_id)
            return self._to_record(asset) if asset else None

    @staticmethod
    def _to_record(asset: AgentAssetModel) -> dict[str, Any]:
        return {
            "asset_id": asset.asset_id,
            "file_path": Path(asset.file_path),
            "content_type": asset.content_type,
            "filename": asset.filename,
            "source_kind": asset.source_kind,
            "project_id": asset.project_id,
            "created_at": asset.created_at,
        }


class SqlAlchemyAnalysisJobRepository:
    """Persists analysis job lifecycle transitions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(self, *, job_id: str, asset_id: str, created_at: datetime, updated_at: datetime | None = None, cache_key: str | None = None, trace_id: str | None = None, retry_of: str | None = None, project_id: str = "default") -> dict[str, Any]:
        with self._session_factory.begin() as session:
            job = AnalysisJobModel(
                job_id=job_id,
                asset_id=asset_id,
                project_id=project_id,
                status="queued",
                created_at=created_at,
                updated_at=updated_at or created_at,
                cache_key=cache_key,
                trace_id=trace_id,
                retry_of=retry_of,
            )
            session.add(job)
        return self._to_record(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            job = session.get(AnalysisJobModel, job_id)
            return self._to_record(job) if job else None

    def get_completed_by_cache_key(self, cache_key: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            statement = select(AnalysisJobModel).where(
                AnalysisJobModel.cache_key == cache_key,
                AnalysisJobModel.status == "completed",
            ).order_by(AnalysisJobModel.completed_at.desc())
            job = session.scalars(statement).first()
            return self._to_record(job) if job else None

    def list_recent(self, *, limit: int) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(AnalysisJobModel).order_by(AnalysisJobModel.created_at.desc()).limit(limit)
            return [self._to_record(job) for job in session.scalars(statement)]

    def count_created_since(self, since: datetime) -> int:
        with self._session_factory() as session:
            statement = select(func.count()).select_from(AnalysisJobModel).where(
                AnalysisJobModel.created_at >= since
            )
            return int(session.scalar(statement) or 0)

    def claim_queued(self, job_id: str, *, started_at: datetime) -> dict[str, Any] | None:
        """Claim a queued job once, even when the broker redelivers it."""
        with self._session_factory.begin() as session:
            statement = (
                update(AnalysisJobModel)
                .where(AnalysisJobModel.job_id == job_id, AnalysisJobModel.status == "queued")
                .values(status="running", updated_at=started_at, started_at=started_at)
            )
            if not session.execute(statement).rowcount:
                return None
            job = session.get(AnalysisJobModel, job_id)
            return self._to_record(job)

    def update(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._session_factory.begin() as session:
            job = session.get(AnalysisJobModel, job_id)
            if not job:
                return None
            for field_name, value in changes.items():
                setattr(job, field_name, value)
        return self._to_record(job)

    @staticmethod
    def _to_record(job: AnalysisJobModel) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "asset_id": job.asset_id,
            "project_id": job.project_id,
            "status": job.status,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "result": job.result,
            "error": job.error,
            "run_id": job.run_id,
            "cache_key": job.cache_key,
            "trace_id": job.trace_id,
            "retry_of": job.retry_of,
        }


class SqlAlchemyAnalysisBatchRepository:
    """Persists batch membership so progress survives API process restarts."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(self, *, batch_id: str, job_ids: list[str], created_at: datetime, trace_id: str | None = None, project_id: str = "default") -> None:
        with self._session_factory.begin() as session:
            session.add(AnalysisBatchModel(batch_id=batch_id, created_at=created_at, trace_id=trace_id, project_id=project_id))
            session.add_all(
                [AnalysisBatchJobModel(batch_id=batch_id, job_id=job_id) for job_id in job_ids]
            )

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            batch = session.get(AnalysisBatchModel, batch_id)
            if batch is None:
                return None
            statement = select(AnalysisBatchJobModel.job_id).where(
                AnalysisBatchJobModel.batch_id == batch_id
            )
            return {
                "batch_id": batch.batch_id,
                "created_at": batch.created_at,
                "trace_id": batch.trace_id,
                "project_id": batch.project_id,
                "job_ids": list(session.scalars(statement)),
            }

    def list_recent(self, *, limit: int, project_id: str = "default") -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(AnalysisBatchModel).where(
                AnalysisBatchModel.project_id == project_id
            ).order_by(
                AnalysisBatchModel.created_at.desc()
            ).limit(limit)
            batches = list(session.scalars(statement))
            return [self.get(batch.batch_id) for batch in batches]
