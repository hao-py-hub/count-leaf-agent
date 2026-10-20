"""Reusable service adapter around the existing VLM and CV analysis pipeline."""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

from app.config import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_PROMPT_VERSION,
    JOB_EXECUTOR_MODE,
    RESULT_DIR,
    VLM_MODEL,
)
from app.history import HistoryStore
from app.services.error_messages import ANALYSIS_FAILURE_MESSAGE, failure_diagnostic

AnalysisEventCallback = Callable[[dict], None]
ALGORITHM_VERSION = ANALYSIS_ALGORITHM_VERSION
LOGGER = logging.getLogger(__name__)


def _pipeline_stream(*args, **kwargs):
    """Load the CV pipeline only when an analysis task actually runs."""
    from app.pipeline.analyzer import analyze_plant_image_stream

    return analyze_plant_image_stream(*args, **kwargs)


def _make_run_id(source_name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(source_name).stem).strip("._-")
    return f"{stem[:80] or 'image'}__{int(time.time() * 1000)}__{uuid.uuid4().hex[:8]}"


class PhenotypeAnalysisService:
    """Persists the existing stream pipeline for HTTP and Agent task callers."""

    def __init__(self, history_store: HistoryStore):
        self._history_store = history_store

    def stream(self, image_path: str, source_label: str, source_kind: str, run_id: str | None = None) -> Iterator[dict]:
        resolved_run_id = run_id or _make_run_id(source_label)
        steps: list[dict] = []
        terminal_event_seen = False
        self._history_store.start(resolved_run_id, source_label, image_path, source_kind)
        try:
            for event in _pipeline_stream(image_path, method="stalk", run_id=resolved_run_id, cache_key=resolved_run_id, source_label=source_label):
                if event.get("type") == "step":
                    steps.append(event)
                elif event.get("type") == "result":
                    event = self._add_provenance(event)
                    event = self._history_store.complete(resolved_run_id, event, steps)
                    self._write_result_snapshot(resolved_run_id, event)
                    terminal_event_seen = True
                elif event.get("type") == "error":
                    self._history_store.fail(resolved_run_id, event.get("message", "分析失败"), steps)
                    terminal_event_seen = True
                yield event
            if not terminal_event_seen:
                message = "分析流程结束但没有返回结果"
                self._history_store.fail(resolved_run_id, message, steps)
                yield {"type": "error", "run_id": resolved_run_id, "message": message}
        except Exception as exc:
            LOGGER.error(
                "analysis_pipeline_failed run_id=%s error_type=%s",
                resolved_run_id,
                failure_diagnostic(exc),
            )
            self._history_store.fail(resolved_run_id, ANALYSIS_FAILURE_MESSAGE, steps)
            yield {"type": "error", "run_id": resolved_run_id, "message": ANALYSIS_FAILURE_MESSAGE}

    def run(self, image_path: str, source_label: str, source_kind: str, on_event: AnalysisEventCallback | None = None, run_id: str | None = None) -> dict:
        final_event: dict | None = None
        for event in self.stream(image_path, source_label, source_kind, run_id=run_id):
            if on_event is not None:
                on_event(event)
            if event.get("type") in {"result", "error"}:
                final_event = event
        return final_event or {"type": "error", "message": "分析未返回最终事件"}

    @staticmethod
    def _write_result_snapshot(run_id: str, result: dict) -> None:
        result_dir = RESULT_DIR / run_id
        result_dir.mkdir(parents=True, exist_ok=True)
        target = result_dir / "result.json"
        temporary = result_dir / "result.json.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(target)

    @staticmethod
    def _add_provenance(result: dict) -> dict:
        """Attach non-secret reproducibility metadata without changing counts."""
        enriched = dict(result)
        enriched["provenance"] = {
            "algorithm": "stalk",
            "algorithm_version": ALGORITHM_VERSION,
            "vlm_model": VLM_MODEL,
            "prompt_version": ANALYSIS_PROMPT_VERSION,
            "executor_mode": JOB_EXECUTOR_MODE,
        }
        return enriched
