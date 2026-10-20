"""Standard confirmed analysis-job API for enterprise integrations."""

import asyncio
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.models import AnalysisJobResponse
from app.history import public_result
from app.services.policy_engine import PolicyEngine
from app.services.job_states import RESULT_READY_STATUSES, TERMINAL_ANALYSIS_STATUSES


router = APIRouter(prefix="/api/v2/jobs", tags=["jobs"])
_POLICY_ENGINE = PolicyEngine()


class SubmitAnalysisRequest(BaseModel):
    asset_id: UUID
    project_id: str = Field(default="default", min_length=1, max_length=128)
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)


class ConfirmWriteRequest(BaseModel):
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)
    project_id: str = Field(default="default", min_length=1, max_length=128)


@router.post("/analysis")
def submit_analysis(payload: SubmitAnalysisRequest, request: Request) -> dict:
    """Require an explicit confirmation before creating an immutable analysis task."""
    payload.project_id = _normalized_project_id(payload.project_id)
    if not payload.confirm:
        return {
            "status": "confirmation_required",
            "asset_id": str(payload.asset_id),
            "project_id": payload.project_id,
            "message": "确认后才会创建图像分析任务。",
        }
    try:
        service = request.app.state.analysis_job_service
        try:
            job_id = service.submit(payload.asset_id, trace_id=str(payload.trace_id), project_id=payload.project_id)
        except TypeError as error:
            if "trace_id" not in str(error):
                raise
            job_id = service.submit(payload.asset_id)
        job = request.app.state.analysis_job_service.get(job_id)
    except KeyError as error:
        raise HTTPException(404, "图片资源不存在") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return AnalysisJobResponse(**job.as_dict()).model_dump(mode="json")


@router.get("", response_model=list[AnalysisJobResponse])
def list_analysis_jobs(request: Request, limit: int = 20, project_id: str = "default") -> list[AnalysisJobResponse]:
    project_id = _normalized_project_id(project_id)
    try:
        jobs = [job for job in request.app.state.analysis_job_service.list_recent(limit=limit) if getattr(job, "project_id", "default") == project_id]
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return [AnalysisJobResponse(**job.as_dict()) for job in jobs]


@router.get("/{job_id}", response_model=AnalysisJobResponse)
def get_analysis_job(job_id: UUID, request: Request, project_id: str = "default") -> AnalysisJobResponse:
    project_id = _normalized_project_id(project_id)
    try:
        job = request.app.state.analysis_job_service.get(job_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error
    if job.project_id != project_id:
        raise HTTPException(404, "分析任务不存在")
    return AnalysisJobResponse(**job.as_dict())


@router.post("/{job_id}/retry", response_model=None)
def retry_analysis_job(job_id: UUID, request: Request, payload: ConfirmWriteRequest | None = None) -> AnalysisJobResponse | dict:
    if payload is None or not payload.confirm:
        return {"status": "confirmation_required", "job_id": str(job_id), "message": "确认后才会重试失败的分析任务。"}
    try:
        payload.project_id = _normalized_project_id(payload.project_id)
        _require_job_project(request.app.state.analysis_job_service.get(job_id), payload.project_id)
        retry_id = request.app.state.analysis_job_service.retry_failed(job_id)
        job = request.app.state.analysis_job_service.get(retry_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return AnalysisJobResponse(**job.as_dict())


@router.post("/{job_id}/cancel", response_model=None)
def cancel_analysis_job(job_id: UUID, request: Request, payload: ConfirmWriteRequest | None = None) -> AnalysisJobResponse | dict:
    if payload is None or not payload.confirm:
        return {"status": "confirmation_required", "job_id": str(job_id), "message": "确认后才会取消排队中的分析任务。"}
    try:
        payload.project_id = _normalized_project_id(payload.project_id)
        _require_job_project(request.app.state.analysis_job_service.get(job_id), payload.project_id)
        job = request.app.state.analysis_job_service.cancel_queued(job_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return AnalysisJobResponse(**job.as_dict())


@router.post("/{job_id}/cache-revocation")
def revoke_analysis_cache(job_id: UUID, request: Request, payload: ConfirmWriteRequest | None = None) -> dict:
    if payload is None or not payload.confirm:
        return {"status": "confirmation_required", "job_id": str(job_id), "message": "确认后才会撤销该结果的缓存复用资格。"}
    try:
        payload.project_id = _normalized_project_id(payload.project_id)
        _require_job_project(request.app.state.analysis_job_service.get(job_id), payload.project_id)
        request.app.state.analysis_job_service.revoke_cache(job_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"status": "revoked", "job_id": str(job_id)}


@router.get("/{job_id}/events")
async def stream_analysis_job_events(job_id: UUID, request: Request, project_id: str = "default") -> StreamingResponse:
    project_id = _normalized_project_id(project_id)
    try:
        _require_job_project(request.app.state.analysis_job_service.get(job_id), project_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error

    async def events():
        previous_payload: dict | None = None
        while True:
            try:
                job = request.app.state.analysis_job_service.get(job_id)
            except KeyError:
                yield "event: error\ndata: {\"message\": \"分析任务不存在\"}\n\n"
                return
            _require_job_project(job, project_id)
            payload = {
                "job_id": str(job.job_id),
                "status": job.status,
                "updated_at": job.updated_at,
                "run_id": job.run_id,
                "error_message": job.error_message,
            }
            if payload != previous_payload:
                yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                previous_payload = payload
            if job.status in TERMINAL_ANALYSIS_STATUSES:
                yield f"event: terminal\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/{job_id}/result")
def get_analysis_result(job_id: UUID, request: Request, project_id: str = "default") -> dict:
    project_id = _normalized_project_id(project_id)
    try:
        job = request.app.state.analysis_job_service.get(job_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error
    _require_job_project(job, project_id)
    if job.status not in RESULT_READY_STATUSES or not isinstance(job.result, dict):
        raise HTTPException(409, "分析任务尚未完成")
    return {"job_id": str(job_id), "result": public_result(job.result)}


def _require_job_project(job, project_id: str) -> None:
    """在读取或写入任务前验证项目归属。"""
    if getattr(job, "project_id", "default") != project_id:
        raise HTTPException(404, "分析任务不存在")


def _normalized_project_id(project_id: str | None) -> str:
    """统一任务接口的项目标识校验与规范化。"""
    try:
        return _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
