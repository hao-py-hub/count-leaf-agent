"""Local, confirmed batch submission built on the single-image job service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .analysis_job_service import AnalysisJobService
from .ports import AnalysisBatchRepository


MAX_BATCH_ASSETS = 20


@dataclass(frozen=True)
class BatchAnalysis:
    batch_id: UUID
    job_ids: tuple[UUID, ...]
    trace_id: str | None = None
    project_id: str = "default"


class BatchAnalysisService:
    """Creates bounded batches without exposing executor internals to HTTP callers."""

    def __init__(self, job_service: AnalysisJobService, repository: AnalysisBatchRepository | None = None) -> None:
        self._job_service = job_service
        self._batches: dict[UUID, BatchAnalysis] = {}
        self._repository = repository

    @staticmethod
    def validate_assets(asset_ids: list[UUID]) -> None:
        if not asset_ids:
            raise ValueError("至少需要选择一张图片")
        if len(asset_ids) > MAX_BATCH_ASSETS:
            raise ValueError(f"单批最多支持 {MAX_BATCH_ASSETS} 张图片")
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("批次中不能包含重复图片")

    def submit_confirmed(self, asset_ids: list[UUID], trace_id: str | None = None, project_id: str = "default") -> BatchAnalysis:
        self.validate_assets(asset_ids)
        project_id = self._job_service.normalize_project_id(project_id)
        # 在创建任何子任务前校验全部提交资产的项目归属。
        for asset_id in asset_ids:
            self._job_service.ensure_asset_exists(asset_id, project_id=project_id)
        self._job_service.ensure_submission_capacity(len(asset_ids))
        jobs = tuple(self._job_service.submit(asset_id, trace_id=trace_id, project_id=project_id) for asset_id in asset_ids)
        batch = BatchAnalysis(batch_id=uuid4(), job_ids=jobs, trace_id=trace_id, project_id=project_id)
        self._batches[batch.batch_id] = batch
        if self._repository:
            self._repository.create(
                batch_id=str(batch.batch_id), job_ids=[str(job_id) for job_id in jobs],
                created_at=datetime.now(timezone.utc),
                trace_id=trace_id,
                project_id=project_id,
            )
        return batch

    def get_status(self, batch_id: UUID) -> dict:
        batch = self._get_batch(batch_id)
        statuses = [self._job_service.get(job_id).status for job_id in batch.job_ids]
        counts = {
            state: statuses.count(state)
            for state in ("queued", "running", "completed", "review_required", "failed", "cancelled")
        }
        terminal = (
            counts["completed"] + counts["review_required"] + counts["failed"] + counts["cancelled"]
            == len(statuses)
        )
        status = "completed" if terminal else "running"
        return {"batch_id": str(batch.batch_id), "project_id": batch.project_id, "status": status, "total": len(statuses), "counts": counts, "job_ids": [str(job_id) for job_id in batch.job_ids]}

    def cancel_queued(self, batch_id: UUID) -> dict:
        """Cancel only still-queued child tasks and preserve running analysis."""
        batch = self._get_batch(batch_id)
        cancelled = 0
        for job_id in batch.job_ids:
            try:
                self._job_service.cancel_queued(job_id)
                cancelled += 1
            except ValueError:
                continue
        status = self.get_status(batch_id)
        running_count = status["counts"]["running"]
        return {
            "batch_id": str(batch_id),
            "cancelled_count": cancelled,
            "running_count": running_count,
            "message": "仍在运行的任务会完成当前分析；排队任务已取消。" if running_count else "排队任务已取消。",
            "status": status,
        }

    def retry_failed(self, batch_id: UUID) -> BatchAnalysis:
        """Create a new batch only for failed children; completed work is never repeated."""
        batch = self._get_batch(batch_id)
        failed_asset_ids = [
            self._job_service.get(job_id).asset_id
            for job_id in batch.job_ids
            if self._job_service.get(job_id).status == "failed"
        ]
        if not failed_asset_ids:
            raise ValueError("该批次没有可重试的失败任务")
        return self.submit_confirmed(failed_asset_ids, trace_id=batch.trace_id, project_id=batch.project_id)

    def list_recent(self, *, limit: int = 20, project_id: str = "default") -> list[dict]:
        """列出项目范围内、重新计算状态摘要的本地批次。"""
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须介于 1 和 100")
        if not self._repository:
            batches = [batch for batch in self._batches.values() if batch.project_id == project_id][-limit:]
            return [self.get_status(batch.batch_id) for batch in reversed(batches)]
        records = self._repository.list_recent(limit=limit, project_id=project_id)
        return [self.get_status(UUID(record["batch_id"])) for record in records]

    def _get_batch(self, batch_id: UUID) -> BatchAnalysis:
        batch = self._batches.get(batch_id)
        if batch is None and self._repository:
            record = self._repository.get(str(batch_id))
            if record:
                batch = BatchAnalysis(
                    batch_id=UUID(record["batch_id"]),
                    job_ids=tuple(UUID(job_id) for job_id in record["job_ids"]),
                    trace_id=record.get("trace_id"),
                    project_id=record.get("project_id", "default"),
                )
        if batch is None:
            raise KeyError("批量任务不存在")
        return batch
