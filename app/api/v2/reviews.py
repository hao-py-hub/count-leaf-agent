"""Teacher-facing human review task endpoints."""

from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.review_resolution_service import ReviewResolutionService
from app.services.job_states import RESULT_READY_STATUSES
from app.services.domain_events import DomainEvent
from app.services.policy_engine import PolicyEngine

router = APIRouter(prefix="/api/v2/reviews", tags=["reviews"])
_POLICY_ENGINE = PolicyEngine()


class CreateReviewRequest(BaseModel):
    job_id: UUID
    reason: str = Field(min_length=1, max_length=500)
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)
    reason_code: str = Field(default="manual", min_length=1, max_length=64)


class ResolveReviewRequest(BaseModel):
    resolution: str = Field(min_length=1, max_length=2_000)
    revision_payload: dict
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)
    project_id: str = Field(default="default", min_length=1, max_length=128)


class DismissReviewRequest(BaseModel):
    resolution: str = Field(default="无需修改", min_length=1, max_length=2_000)
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)
    project_id: str = Field(default="default", min_length=1, max_length=128)


def _require_completed_job(request: Request, job_id: UUID):
    try:
        job = request.app.state.analysis_job_service.get(job_id)
    except KeyError as error:
        raise HTTPException(404, "关联的分析任务不存在") from error
    if job.status not in RESULT_READY_STATUSES:
        raise HTTPException(409, "复核只能针对已完成的分析任务创建")
    return job


def _publish_review_resolved(request: Request, review_id: UUID, trace_id: UUID, record: dict) -> None:
    try:
        job = request.app.state.analysis_job_service.get(UUID(record["job_id"]))
        project_id = str(getattr(job, "project_id", None) or "default")
    except KeyError:
        project_id = "default"
    request.app.state.domain_event_publisher.publish(DomainEvent(
        "ReviewResolved", "ReviewCase", str(review_id), project_id,
        str(trace_id), metadata={"status": record["status"], "run_id": record.get("run_id")},
    ))


def _require_review_project(record: dict, project_id: str) -> None:
    project_id = _normalized_project_id(project_id)
    if record.get("project_id", "default") != project_id:
        raise HTTPException(404, "复核任务不存在")


@router.post("")
def create_review(payload: CreateReviewRequest, request: Request) -> dict:
    job = _require_completed_job(request, payload.job_id)
    if not payload.confirm:
        return {
            "status": "confirmation_required",
            "job_id": str(payload.job_id),
            "message": "确认后才会创建人工复核任务。",
        }
    service = request.app.state.review_service
    try:
        project_id = str(getattr(job, "project_id", None) or "default")
        record = service.create(payload.job_id, payload.reason, trace_id=str(payload.trace_id), reason_code=payload.reason_code, project_id=project_id)
        request.app.state.audit_ledger.event(
            event_type="review_created", source="review_api", trace_id=str(payload.trace_id),
            resource_id=str(payload.job_id), status="open",
            metadata={"review_id": record["review_id"]}, project_id=project_id,
        )
        request.app.state.domain_event_publisher.publish(DomainEvent(
            "ReviewCreated", "ReviewCase", record["review_id"],
            project_id, str(payload.trace_id),
            metadata={"job_id": str(payload.job_id), "reason_code": record["reason_code"]},
        ))
        return record
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except TypeError as error:
        if "trace_id" not in str(error):
            raise
        return service.create(payload.job_id, payload.reason)


@router.get("")
def list_reviews(request: Request, limit: int = 50, reason_code: str | None = None, project_id: str = "default") -> dict:
    project_id = _normalized_project_id(project_id)
    try:
        return {"items": request.app.state.review_service.list_recent(limit=limit, reason_code=reason_code, project_id=project_id)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/{review_id}")
def get_review(review_id: UUID, request: Request, project_id: str = "default") -> dict:
    try:
        record = request.app.state.review_service.get(review_id)
        _require_review_project(record, project_id)
        return record
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@router.put("/{review_id}/resolve")
def resolve_review(review_id: UUID, payload: ResolveReviewRequest, request: Request) -> dict:
    payload.project_id = _normalized_project_id(payload.project_id)
    try:
        _require_review_project(request.app.state.review_service.get(review_id), payload.project_id)
    except KeyError as error:
        raise HTTPException(404, "复核任务不存在") from error
    if not payload.confirm:
        return {
            "status": "confirmation_required",
            "review_id": str(review_id),
            "message": "确认后才会写入人工修订并结案复核任务。",
        }
    try:
        record = ReviewResolutionService(
            review_service=request.app.state.review_service,
            analysis_job_service=request.app.state.analysis_job_service,
            history_store=request.app.state.history_store,
            write_result_snapshot=request.app.state.write_result_snapshot,
            project_manual_revision=request.app.state.phenotype_domain_projection.project_manual_revision,
        ).resolve(review_id, payload.resolution, payload.revision_payload, expected_project_id=payload.project_id)
        request.app.state.audit_ledger.event(
            event_type="review_resolved", source="review_api", trace_id=str(payload.trace_id),
            resource_id=str(review_id), status=record["status"],
            metadata={"run_id": record["run_id"], "history_revision": record["history_revision"]}, project_id=payload.project_id,
        )
        _publish_review_resolved(request, review_id, payload.trace_id, record)
        return record
    except KeyError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.put("/{review_id}/dismiss")
def dismiss_review(review_id: UUID, request: Request, payload: DismissReviewRequest | None = None) -> dict:
    if payload is None or not payload.confirm:
        return {"status": "confirmation_required", "review_id": str(review_id), "message": "确认后才会保留自动结果并结案复核任务。"}
    try:
        payload.project_id = _normalized_project_id(payload.project_id)
        _require_review_project(request.app.state.review_service.get(review_id), payload.project_id)
        record = request.app.state.review_service.dismiss(review_id, payload.resolution)
        request.app.state.audit_ledger.event(
            event_type="review_dismissed", source="review_api", trace_id=str(payload.trace_id),
            resource_id=str(review_id), status=record["status"], metadata={"resolution": record["resolution"]}, project_id=payload.project_id,
        )
        _publish_review_resolved(request, review_id, payload.trace_id, record)
        return record
    except KeyError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _normalized_project_id(project_id: str | None) -> str:
    """统一人工复核接口的项目标识校验与规范化。"""
    try:
        return _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
