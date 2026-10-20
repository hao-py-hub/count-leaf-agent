"""In-process asynchronous analysis job adapter for the v0.1 local deployment."""

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock, Timer
from time import perf_counter
from typing import Callable, Mapping, Protocol
from uuid import UUID, uuid4

from .asset_store import AssetStore
from .error_messages import (
    ANALYSIS_FAILURE_MESSAGE,
    ANALYSIS_TIMEOUT_MESSAGE,
    failure_diagnostic,
)
from .job_dispatcher import AnalysisJobDispatcher
from .ports import AnalysisJobRepository
from .policy_engine import PhenotypingPolicy, PolicyEngine
from .domain_events import DomainEvent, DomainEventPublisher
from app.config import (
    ARK_API_BASE, ANALYSIS_ALGORITHM_VERSION, ANALYSIS_PROMPT_VERSION, VLM_MODEL,
    VLM_INPUT_COST_PER_MILLION, VLM_OUTPUT_COST_PER_MILLION,
    STALK_TWIG_AREA_RATIO, STALK_TWIG_WIDTH_RATIO,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


LOGGER = logging.getLogger(__name__)


class AnalysisRunner(Protocol):
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict: ...


@dataclass
class AnalysisJob:
    job_id: UUID
    asset_id: UUID
    status: str
    created_at: str
    updated_at: str
    run_id: str | None = None
    error_message: str | None = None
    result: dict | None = None
    cache_key: str | None = None
    cache_hit: bool = False
    trace_id: str | None = None
    retry_of: str | None = None
    project_id: str = "default"

    def as_dict(self) -> dict:
        return {"job_id": str(self.job_id), "asset_id": str(self.asset_id), "project_id": self.project_id, "status": self.status, "created_at": self.created_at, "updated_at": self.updated_at, "run_id": self.run_id, "error_message": self.error_message, "result": self.result, "cache_hit": self.cache_hit, "trace_id": self.trace_id, "retry_of": self.retry_of}


class AnalysisJobService:
    """Local adapter; replace the executor with Celery without changing Agent tools."""

    def __init__(self, asset_store: AssetStore, runner: AnalysisRunner, max_workers: int = 2, repository: AnalysisJobRepository | None = None, daily_job_limit: int = 100, on_completed: Callable[[AnalysisJob], bool | None] | None = None, dispatcher: AnalysisJobDispatcher | None = None, timeout_seconds: int = 300, cache_context: Mapping[str, str] | None = None, audit_event: Callable[..., None] | None = None, record_model_call: Callable[..., None] | None = None, cache_revoked: Callable[[str], bool] | None = None, policy_engine: PolicyEngine | None = None, domain_event_publisher: DomainEventPublisher | None = None):
        self._asset_store = asset_store
        self._runner = runner
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="phenotype")
        self._jobs: dict[UUID, AnalysisJob] = {}
        self._futures: dict[UUID, Future] = {}
        self._timers: dict[UUID, Timer] = {}
        self._lock = RLock()
        self._repository = repository
        self._on_completed = on_completed
        self._audit_event = audit_event
        self._record_model_call = record_model_call
        self._cache_revoked = cache_revoked
        self._domain_event_publisher = domain_event_publisher
        self._dispatcher = dispatcher
        if dispatcher is not None and repository is None:
            raise ValueError("Celery dispatcher requires a persistent job repository")
        self._policy_engine = policy_engine or PolicyEngine(
            policy=PhenotypingPolicy(max_daily_analysis_jobs=daily_job_limit)
        )
        if daily_job_limit < 1:
            raise ValueError("每日分析任务上限必须至少为 1")
        if policy_engine is not None and daily_job_limit != policy_engine.policy.max_daily_analysis_jobs:
            raise ValueError("daily_job_limit 必须与策略版本中的预算一致")
        if timeout_seconds < 1:
            raise ValueError("分析任务超时秒数必须至少为 1")
        self._daily_job_limit = daily_job_limit
        self._timeout_seconds = timeout_seconds
        default_cache_context = {
            "algorithm": "stalk",
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "model_endpoint": ARK_API_BASE,
            "model_version": VLM_MODEL,
            "prompt_version": ANALYSIS_PROMPT_VERSION,
            "input_cost_per_million": str(VLM_INPUT_COST_PER_MILLION),
            "output_cost_per_million": str(VLM_OUTPUT_COST_PER_MILLION),
            "stalk_twig_width_ratio": str(STALK_TWIG_WIDTH_RATIO),
            "stalk_twig_area_ratio": str(STALK_TWIG_AREA_RATIO),
            "policy_version": self._policy_engine.policy.policy_version,
        }
        self._cache_context = {**default_cache_context, **dict(cache_context or {})}

    def submit(self, asset_id: UUID, trace_id: str | None = None, project_id: str = "default") -> UUID:
        project_id = self._policy_engine.require_project_id(project_id)
        asset = self._asset_store.get(asset_id)
        if getattr(asset, "project_id", "default") != project_id:
            raise KeyError("图片资源不存在")
        self.ensure_submission_capacity(1)
        cache_key = self._cache_key(asset_id, asset.file_path, project_id)
        with self._lock:
            in_flight = next((job for job in self._jobs.values() if job.cache_key == cache_key and job.status in {"queued", "running"}), None)
            if in_flight is not None:
                return in_flight.job_id
            cached = next((job for job in self._jobs.values() if job.cache_key == cache_key and job.status == "completed"), None)
        if cached is not None and self._cache_revoked and self._cache_revoked(cache_key):
            cached = None
        if cached is None and self._repository is not None:
            record = self._repository.get_completed_by_cache_key(cache_key)
            if record:
                cached = self._job_from_record(record, cache_hit=True)
        if cached is not None:
            if self._audit_event:
                self._audit_event(event_type="analysis_cache_hit", source="job_service", trace_id=trace_id, resource_id=str(asset_id), version=self._cache_context.get("algorithm_version"), status="completed", cache_hit=True, metadata={"source_run_id": cached.run_id}, project_id=project_id)
            return self._register_cached_job(cached, asset_id)
        now = _now()
        job = AnalysisJob(uuid4(), asset_id, "queued", now, now, cache_key=cache_key, trace_id=trace_id, project_id=project_id)
        if self._audit_event:
            self._audit_event(event_type="analysis_cache_miss", source="job_service", trace_id=trace_id, resource_id=str(asset_id), version=self._cache_context.get("algorithm_version"), status="queued", cache_hit=False, metadata={"job_id": str(job.job_id)}, project_id=project_id)
            self._audit_event(event_type="analysis_submitted", source="job_service", trace_id=trace_id, resource_id=str(asset_id), version=self._cache_context.get("algorithm_version"), status="queued", cache_hit=False, metadata={"job_id": str(job.job_id)}, project_id=project_id)
        self._publish_domain_event("AnalysisSubmitted", job, metadata={"job_id": str(job.job_id)})
        with self._lock:
            self._jobs[job.job_id] = job
        if self._repository:
            self._repository.create(
                job_id=str(job.job_id),
                asset_id=str(job.asset_id),
                created_at=datetime.fromisoformat(now),
                updated_at=datetime.fromisoformat(now),
                cache_key=cache_key,
                trace_id=trace_id,
                project_id=project_id,
            )
        if self._dispatcher is not None:
            try:
                self._dispatcher.dispatch(str(job.job_id))
            except Exception as exc:
                LOGGER.exception("analysis_job_dispatch_failed job_id=%s", job.job_id)
                self._mark_dispatch_failed(job, exc)
        else:
            timeout_timer = Timer(self._timeout_seconds, self._mark_timed_out, args=(job.job_id,))
            with self._lock:
                self._timers[job.job_id] = timeout_timer
            timeout_timer.start()
            future = self._executor.submit(self._execute, job.job_id)
            with self._lock:
                self._futures[job.job_id] = future
        return job.job_id

    def _cache_key(self, asset_id: UUID, file_path, project_id: str = "default") -> str:
        """以资源、图像内容和不可变分析版本生成缓存键。"""
        digest = hashlib.sha256()
        digest.update(str(asset_id).encode("utf-8"))
        digest.update(project_id.encode("utf-8"))
        digest.update(json.dumps(self._cache_context, ensure_ascii=True, sort_keys=True).encode("utf-8"))
        with open(file_path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"phenotype-v2:{digest.hexdigest()}"

    def _register_cached_job(self, cached: AnalysisJob, asset_id: UUID) -> UUID:
        now = _now()
        job = AnalysisJob(uuid4(), asset_id, "completed", now, now, cached.run_id, result=cached.result, cache_key=cached.cache_key, cache_hit=True, trace_id=cached.trace_id, project_id=cached.project_id)
        with self._lock:
            self._jobs[job.job_id] = job
        return job.job_id

    @staticmethod
    def _job_from_record(record: dict, *, cache_hit: bool = False) -> AnalysisJob:
        return AnalysisJob(
            job_id=UUID(str(record["job_id"])), asset_id=UUID(str(record["asset_id"])),
            status=str(record["status"]), created_at=record["created_at"].isoformat(),
            updated_at=record["updated_at"].isoformat(), run_id=record.get("run_id"),
            error_message=record.get("error"), result=record.get("result"),
            cache_key=record.get("cache_key"), cache_hit=cache_hit,
            trace_id=record.get("trace_id"), retry_of=record.get("retry_of"),
            project_id=record.get("project_id", "default"),
        )

    def execute_persisted(self, job_id: UUID) -> None:
        """Run a queued durable job from a separate worker process.

        The API process creates the immutable job record, while a Celery worker
        reconstructs this service and invokes this method using only the ID.
        """
        if self._repository is None:
            raise RuntimeError("持久化 Worker 需要任务仓储")
        started_at = datetime.now(timezone.utc)
        record = self._repository.claim_queued(str(job_id), started_at=started_at)
        if record is None:
            return
        job = AnalysisJob(
            job_id=UUID(str(record["job_id"])),
            asset_id=UUID(str(record["asset_id"])),
            status=str(record["status"]),
            created_at=record["created_at"].isoformat(),
            updated_at=record["updated_at"].isoformat(),
            run_id=record.get("run_id"),
            error_message=record.get("error"), trace_id=record.get("trace_id"),
            result=record.get("result"), cache_key=record.get("cache_key"),
            retry_of=record.get("retry_of"),
            project_id=record.get("project_id", "default"),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._execute(job.job_id, already_claimed=True)

    def ensure_asset_exists(self, asset_id: UUID, project_id: str = "default") -> None:
        """Validate the opaque asset identifier before a write is accepted."""
        asset = self._asset_store.get(asset_id)
        if getattr(asset, "project_id", "default") != project_id:
            raise KeyError("图片资源不存在")

    def ensure_submission_capacity(self, count: int) -> None:
        """Enforce the local daily budget before any task records are created."""
        if count < 1:
            raise ValueError("提交数量必须至少为 1")
        used = self.budget_status()["used"]
        policy_status = self._policy_engine.check_budget(used)
        if used + count > policy_status["limit"]:
            raise ValueError(f"今日分析任务预算不足：已使用 {used}/{policy_status['limit']}，本次需要 {count}")

    def policy_status(self) -> dict[str, object]:
        """Return the immutable policy identity used by this service."""
        status = self._policy_engine.check_budget(self.budget_status()["used"])
        status["policy_version"] = self._policy_engine.policy.policy_version
        return status

    def normalize_project_id(self, project_id: str | None) -> str:
        """Validate a project scope for dependent domain services."""
        return self._policy_engine.require_project_id(project_id)

    def _publish_domain_event(self, event_type: str, job: AnalysisJob, *, metadata: dict[str, object] | None = None) -> None:
        if self._domain_event_publisher is not None:
            self._domain_event_publisher.publish(DomainEvent(
                event_type=event_type, aggregate_type="AnalysisRun", aggregate_id=str(job.job_id),
                project_id=job.project_id, trace_id=job.trace_id, metadata=metadata or {},
            ))

    def budget_status(self) -> dict[str, int]:
        """Return non-sensitive local daily task-budget metadata."""
        if self._repository:
            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            used = self._repository.count_created_since(start)
        else:
            today = datetime.now(timezone.utc).date()
            with self._lock:
                used = sum(
                    datetime.fromisoformat(job.created_at).date() == today
                    for job in self._jobs.values()
                )
        return {"used": used, "limit": self._daily_job_limit, "remaining": max(0, self._daily_job_limit - used)}

    def get(self, job_id: UUID) -> AnalysisJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                if self._repository:
                    record = self._repository.get(str(job_id))
                    if record:
                        persisted_job = AnalysisJob(
                            job_id=UUID(str(record["job_id"])), asset_id=UUID(str(record["asset_id"])),
                            status=str(record["status"]), created_at=record["created_at"].isoformat(),
                            updated_at=record["updated_at"].isoformat(), run_id=record.get("run_id"),
                            error_message=record.get("error"), result=record.get("result"), cache_key=record.get("cache_key"),
                            trace_id=record.get("trace_id"), retry_of=record.get("retry_of"),
                            project_id=record.get("project_id", "default"),
                        )
                        if self._is_stale_persisted_job(persisted_job):
                            self._mark_persisted_job_timed_out(persisted_job)
                        return persisted_job
                raise KeyError("分析任务不存在")
            return AnalysisJob(**job.__dict__)

    def _is_stale_persisted_job(self, job: AnalysisJob) -> bool:
        if self._dispatcher is not None or job.status not in {"queued", "running"}:
            return False
        updated_at = datetime.fromisoformat(job.updated_at)
        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age_seconds >= self._timeout_seconds

    def _mark_persisted_job_timed_out(self, job: AnalysisJob) -> None:
        job.status = "failed"
        job.updated_at = _now()
        job.error_message = ANALYSIS_TIMEOUT_MESSAGE
        job.result = {"type": "error", "message": ANALYSIS_TIMEOUT_MESSAGE}
        self._repository.update(
            str(job.job_id),
            status=job.status,
            updated_at=datetime.fromisoformat(job.updated_at),
            completed_at=datetime.fromisoformat(job.updated_at),
            error=job.error_message,
            result=job.result,
        )
        LOGGER.warning("analysis_job_persisted_timeout job_id=%s", job.job_id)

    def list_recent(self, *, limit: int = 20) -> list[AnalysisJob]:
        """List task metadata for the local task center without exposing assets."""
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须介于 1 和 100")
        if self._repository:
            records = self._repository.list_recent(limit=limit)
            return [
                AnalysisJob(
                    job_id=UUID(str(record["job_id"])), asset_id=UUID(str(record["asset_id"])),
                    status=str(record["status"]), created_at=record["created_at"].isoformat(),
                    updated_at=record["updated_at"].isoformat(), run_id=record.get("run_id"),
                    error_message=record.get("error"), result=None, cache_key=record.get("cache_key"),
                    trace_id=record.get("trace_id"), retry_of=record.get("retry_of"),
                    project_id=record.get("project_id", "default"),
                )
                for record in records
            ]
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)[:limit]
            return [AnalysisJob(**{**job.__dict__, "result": None}) for job in jobs]

    def retry_failed(self, job_id: UUID) -> UUID:
        """Create a new task for a failed job while preserving the failed record."""
        previous = self.get(job_id)
        if previous.status != "failed":
            raise ValueError("只有失败的分析任务可以重试")
        retry_id = self.submit(previous.asset_id, trace_id=previous.trace_id, project_id=previous.project_id)
        with self._lock:
            if retry_id in self._jobs:
                self._jobs[retry_id].retry_of = str(previous.job_id)
        if self._repository:
            self._repository.update(str(retry_id), retry_of=str(previous.job_id))
        return retry_id

    def revoke_cache(self, job_id: UUID) -> None:
        job = self.get(job_id)
        if not job.cache_key:
            raise ValueError("任务没有可撤销的缓存")
        if self._audit_event:
            self._audit_event(event_type="cache_revoked", source="job_service", trace_id=job.trace_id, resource_id=str(job.job_id), version=self._cache_context.get("algorithm_version"), status="revoked", metadata={"cache_key": job.cache_key}, project_id=job.project_id)
        self._publish_domain_event("CacheRevoked", job, metadata={"cache_key": job.cache_key})

    def cancel_queued(self, job_id: UUID) -> AnalysisJob:
        """Cancel only an executor-queued task; running analysis remains immutable."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError("分析任务不存在")
            if job.status != "queued":
                raise ValueError("只有排队中的分析任务可以取消")
            future = self._futures.get(job_id)
            if future is None or not future.cancel():
                raise ValueError("任务已开始执行，不能取消")
            job.status = "cancelled"
            job.updated_at = _now()
            if self._repository:
                self._repository.update(
                    str(job_id), status=job.status,
                    updated_at=datetime.fromisoformat(job.updated_at),
                )
            cancelled_job = AnalysisJob(**job.__dict__)
        self._cancel_timeout_timer(job_id)
        return cancelled_job

    def _execute(self, job_id: UUID, *, already_claimed: bool = False) -> None:
        completed_job: AnalysisJob | None = None
        with self._lock:
            job = self._jobs[job_id]
            if job.status not in {"queued", "running"}:
                LOGGER.info("analysis_job_execution_skipped job_id=%s status=%s", job_id, job.status)
                return
            if not already_claimed:
                job.status = "running"
                job.updated_at = _now()
            if self._repository and not already_claimed:
                self._repository.update(str(job_id), status=job.status, updated_at=datetime.fromisoformat(job.updated_at), started_at=datetime.fromisoformat(job.updated_at))
        started = perf_counter()
        try:
            asset = self._asset_store.get(job.asset_id)
            result = self._runner.run(
                str(asset.file_path),
                asset.source_label,
                asset.source_kind,
            )
        except Exception as exc:
            LOGGER.error(
                "analysis_job_failed job_id=%s error_type=%s",
                job_id,
                failure_diagnostic(exc),
            )
            result = {"type": "error", "message": ANALYSIS_FAILURE_MESSAGE}
        if self._record_model_call:
            model_calls = result.get("model_calls")
            if not isinstance(model_calls, list):
                model_calls = [{
                    "source": "pipeline", "input_tokens": (result.get("model_usage") or {}).get("input_tokens"),
                    "output_tokens": (result.get("model_usage") or {}).get("output_tokens"),
                    "error_code": None if result.get("type") == "result" else "analysis_failed",
                }]
            for call in model_calls:
                if not isinstance(call, Mapping):
                    continue
                model_usage = {
                    "input_tokens": call.get("input_tokens"),
                    "output_tokens": call.get("output_tokens"),
                }
                self._record_model_call(
                    source=f"phenotype_analysis.{call.get('source', 'pipeline')}", trace_id=job.trace_id,
                    model_endpoint=self._cache_context.get("model_endpoint"),
                    model_version=self._cache_context.get("model_version"),
                    prompt_version=self._cache_context.get("prompt_version"),
                    input_tokens=model_usage["input_tokens"], output_tokens=model_usage["output_tokens"],
                    estimated_cost=self._estimate_model_cost(model_usage),
                    duration_ms=round((perf_counter() - started) * 1000),
                    result_reference=result.get("run_id"), error_code=call.get("error_code"), project_id=job.project_id,
                )
        result = self._attach_trace_provenance(result, job.trace_id)
        with self._lock:
            job = self._jobs[job_id]
            if job.status == "failed" and job.error_message == ANALYSIS_TIMEOUT_MESSAGE:
                LOGGER.info("analysis_job_result_ignored_after_timeout job_id=%s", job_id)
                return
            job.updated_at = _now()
            job.result = result
            if result.get("type") == "result":
                job.status = "completed"
                job.run_id = result.get("run_id")
                if self._repository:
                    self._repository.update(str(job_id), status=job.status, updated_at=datetime.fromisoformat(job.updated_at), completed_at=datetime.fromisoformat(job.updated_at), run_id=job.run_id, result=result)
                completed_job = AnalysisJob(**job.__dict__)
                if self._audit_event:
                    self._audit_event(event_type="analysis_completed", source="worker", trace_id=job.trace_id, resource_id=str(job.asset_id), version=self._cache_context.get("algorithm_version"), status=job.status, cache_hit=False, metadata={"job_id": str(job.job_id), "run_id": job.run_id}, project_id=job.project_id)
                self._publish_domain_event("AnalysisCompleted", job, metadata={"run_id": job.run_id})
            else:
                job.status = "failed"
                job.error_message = result.get("message", "分析失败")
                if self._repository:
                    self._repository.update(str(job_id), status=job.status, updated_at=datetime.fromisoformat(job.updated_at), completed_at=datetime.fromisoformat(job.updated_at), error=job.error_message, result=result)
                if self._audit_event:
                    self._audit_event(event_type="analysis_failed", source="worker", trace_id=job.trace_id, resource_id=str(job.asset_id), version=self._cache_context.get("algorithm_version"), status=job.status, cache_hit=False, metadata={"job_id": str(job.job_id), "error": job.error_message}, project_id=job.project_id)
                self._publish_domain_event("AnalysisFailed", job, metadata={"error": job.error_message})
        self._cancel_timeout_timer(job_id)
        if completed_job is not None and self._on_completed is not None:
            try:
                if self._on_completed(completed_job):
                    self.mark_review_required(job_id)
            except Exception:
                LOGGER.exception("analysis_job_completion_hook_failed job_id=%s", job_id)

    def mark_review_required(self, job_id: UUID) -> AnalysisJob:
        """Mark a completed result as awaiting human review without hiding it."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                job = self.get(job_id)
            if job.status != "completed":
                raise ValueError("只有已完成的分析任务可以进入复核状态")
            job.status = "review_required"
            job.updated_at = _now()
            if self._repository:
                self._repository.update(
                    str(job_id), status=job.status,
                    updated_at=datetime.fromisoformat(job.updated_at),
                )
            review_job = AnalysisJob(**job.__dict__)
        if self._audit_event:
            self._audit_event(
                event_type="analysis_review_required", source="review_trigger",
                trace_id=review_job.trace_id, resource_id=str(review_job.asset_id),
                version=self._cache_context.get("algorithm_version"), status=review_job.status,
                cache_hit=False, metadata={"job_id": str(review_job.job_id), "run_id": review_job.run_id},
            )
        self._publish_domain_event("ReviewCreated", review_job, metadata={"run_id": review_job.run_id})
        return review_job

    def _estimate_model_cost(self, model_usage: Mapping[str, object]) -> float | None:
        input_tokens = model_usage.get("input_tokens")
        output_tokens = model_usage.get("output_tokens")
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            return None
        input_rate = float(self._cache_context.get("input_cost_per_million", "0"))
        output_rate = float(self._cache_context.get("output_cost_per_million", "0"))
        if input_rate <= 0 and output_rate <= 0:
            return None
        return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 8)

    def _mark_timed_out(self, job_id: UUID) -> None:
        with self._lock:
            self._timers.pop(job_id, None)
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"queued", "running"}:
                return
            job.status = "failed"
            job.updated_at = _now()
            job.error_message = ANALYSIS_TIMEOUT_MESSAGE
            job.result = {"type": "error", "message": ANALYSIS_TIMEOUT_MESSAGE}
            if self._repository:
                self._repository.update(
                    str(job_id),
                    status=job.status,
                    updated_at=datetime.fromisoformat(job.updated_at),
                    completed_at=datetime.fromisoformat(job.updated_at),
                    error=job.error_message,
                    result=job.result,
                )
        LOGGER.warning(
            "analysis_job_timed_out job_id=%s timeout_seconds=%s",
            job_id,
            self._timeout_seconds,
        )

    def _cancel_timeout_timer(self, job_id: UUID) -> None:
        with self._lock:
            timeout_timer = self._timers.pop(job_id, None)
        if timeout_timer is not None:
            timeout_timer.cancel()

    @staticmethod
    def _attach_trace_provenance(result: dict, trace_id: str | None) -> dict:
        """将任务追踪标识写入结果 provenance，避免改变业务计数。"""
        if not trace_id or not isinstance(result, dict):
            return result
        enriched = dict(result)
        provenance = dict(enriched.get("provenance") or {})
        provenance["trace_id"] = trace_id
        enriched["provenance"] = provenance
        return enriched

    def _mark_dispatch_failed(self, job: AnalysisJob, exc: Exception) -> None:
        job.status = "failed"
        job.updated_at = _now()
        job.error_message = "分析任务分发失败"
        job.result = {"type": "error", "message": job.error_message}
        if self._repository:
            self._repository.update(
                str(job.job_id),
                status=job.status,
                updated_at=datetime.fromisoformat(job.updated_at),
                completed_at=datetime.fromisoformat(job.updated_at),
                error=job.error_message,
                result=job.result,
            )
