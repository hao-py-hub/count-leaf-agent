"""Versioned domain policies for project-scoped phenotyping workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PhenotypingPolicy:
    """Immutable policy snapshot applied to one project request."""

    policy_version: str = "phenotyping-policy-v1"
    max_daily_analysis_jobs: int = 100
    require_quality_confirmation: bool = True
    auto_review_on_model_conflict: bool = True

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version 不能为空")
        if self.max_daily_analysis_jobs < 1:
            raise ValueError("max_daily_analysis_jobs 必须为正数")


class PolicyEngine:
    """Evaluate business rules without granting or changing tool permissions."""

    def __init__(self, policy: PhenotypingPolicy | None = None) -> None:
        self._policy = policy or PhenotypingPolicy()

    @property
    def policy(self) -> PhenotypingPolicy:
        return self._policy

    def require_project_id(self, project_id: str | None) -> str:
        normalized = (project_id or "").strip()
        if not normalized:
            raise ValueError("project_id 不能为空")
        if len(normalized) > 128:
            raise ValueError("project_id 超出长度限制")
        return normalized

    def check_budget(self, used_jobs: int) -> dict[str, Any]:
        if used_jobs < 0:
            raise ValueError("used_jobs 不能为负数")
        remaining = max(self._policy.max_daily_analysis_jobs - used_jobs, 0)
        return {
            "policy_version": self._policy.policy_version,
            "limit": self._policy.max_daily_analysis_jobs,
            "used": used_jobs,
            "remaining": remaining,
            "allowed": remaining > 0,
        }

    def review_required(self, *, quality_warning: bool, model_conflict: bool) -> bool:
        return (
            (quality_warning and self._policy.require_quality_confirmation)
            or (model_conflict and self._policy.auto_review_on_model_conflict)
        )

