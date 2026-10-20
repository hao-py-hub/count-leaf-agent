"""复核结案的共享编排服务，统一版本化修订规则。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from .job_states import RESULT_READY_STATUSES


class ReviewResolutionService:
    """在确认后的人工修订写入历史新版本，并结案对应复核任务。"""

    def __init__(
        self,
        *,
        review_service: Any,
        analysis_job_service: Any,
        history_store: Any,
        write_result_snapshot: Callable[[str, dict], None],
        project_manual_revision: Callable[[Any, dict], None] | None = None,
    ) -> None:
        self._review_service = review_service
        self._analysis_job_service = analysis_job_service
        self._history_store = history_store
        self._write_result_snapshot = write_result_snapshot
        self._project_manual_revision = project_manual_revision

    def resolve(self, review_id: UUID, resolution: str, revision_payload: dict, *, expected_project_id: str | None = None) -> dict:
        """写入人工修订的新版本后，原子地标记开放复核任务为已结案。"""
        review = self._review_service.get(review_id)
        if expected_project_id is not None and review.get("project_id", "default") != expected_project_id:
            raise KeyError("复核任务不存在")
        if review["status"] != "open":
            raise KeyError("复核任务不存在或已处理")
        job = self._analysis_job_service.get(UUID(review["job_id"]))
        if getattr(job, "project_id", "default") != review.get("project_id", "default"):
            raise KeyError("关联分析任务不存在")
        if job.status not in RESULT_READY_STATUSES or not job.run_id:
            raise ValueError("关联任务未完成或缺少可修订的分析结果")
        updated_result = self._history_store.update(job.run_id, revision_payload)
        self._write_result_snapshot(job.run_id, updated_result)
        if self._project_manual_revision is not None:
            self._project_manual_revision(job, updated_result)
        revision = int(updated_result.get("manual_revision", 0))
        return self._review_service.resolve(
            review_id,
            resolution,
            run_id=job.run_id,
            history_revision=revision,
        )
