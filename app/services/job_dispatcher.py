"""Execution boundary for asynchronous analysis jobs."""

from __future__ import annotations

from typing import Protocol


class AnalysisJobDispatcher(Protocol):
    """Schedules an already-persisted job for background processing."""

    def dispatch(self, job_id: str) -> None:
        """Schedule the job without exposing executor-specific details."""


class CeleryAnalysisJobDispatcher:
    """Celery adapter used only when ``JOB_EXECUTOR_MODE=celery``."""

    def __init__(self, task: object) -> None:
        self._task = task

    def dispatch(self, job_id: str) -> None:
        delay = getattr(self._task, "delay", None)
        if not callable(delay):
            raise RuntimeError("Configured Celery task does not provide delay().")
        delay(job_id)
