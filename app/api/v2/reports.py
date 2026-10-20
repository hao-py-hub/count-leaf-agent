"""Evidence-grounded report endpoints for completed analysis jobs."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.llm import DeepSeekChatClient, LlmConfigurationError, LlmSettings
from app.agent.reporting import PhenotypeReportWriter
from app.services.job_states import RESULT_READY_STATUSES
from app.services.policy_engine import PolicyEngine


router = APIRouter(prefix="/api/v2/agent", tags=["agent-reports"])
_POLICY_ENGINE = PolicyEngine()


class ReportRequest(BaseModel):
    """用于引导已完成分析报告的可选问题。"""

    question: str | None = Field(default=None, max_length=500)
    project_id: str = Field(default="default", min_length=1, max_length=128)
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)


class ReportResponse(BaseModel):
    job_id: str
    report: str
    source_status: str
    provenance: dict[str, Any] | None = None


class ReportConfirmationResponse(BaseModel):
    status: str = "confirmation_required"
    job_id: str
    message: str


class ReportJobResponse(BaseModel):
    report_job_id: str
    analysis_job_id: str
    status: str
    report: str | None = None
    error_message: str | None = None
    trace_id: str | None = None
    project_id: str = "default"


def _to_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    value_dict = getattr(value, "__dict__", None)
    if isinstance(value_dict, dict):
        return value_dict
    raise TypeError("Unsupported analysis job record.")


def _get_job(request: Request, job_id: UUID) -> dict[str, Any] | None:
    service = request.app.state.analysis_job_service
    getter = getattr(service, "get", None) or getattr(service, "get_job", None)
    if not callable(getter):
        raise RuntimeError("Analysis job service does not provide a job lookup method.")
    try:
        job = getter(job_id)
    except KeyError:
        return None
    return _to_mapping(job) if job else None


def _require_report_project(job: Any, project_id: str) -> None:
    project_id = _normalized_project_id(project_id)
    actual_project_id = job.get("project_id") if isinstance(job, dict) else getattr(job, "project_id", None)
    if str(actual_project_id or "default") != project_id:
        raise HTTPException(404, "报告任务不存在")


@router.post("/jobs/{job_id}/report", response_model=ReportConfirmationResponse | ReportJobResponse)
def create_report(job_id: UUID, payload: ReportRequest, request: Request) -> ReportConfirmationResponse | ReportJobResponse:
    """解读已完成的算法结果；LLM 不参与角果计数。"""
    payload.project_id = _normalized_project_id(payload.project_id)
    job = _get_job(request, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job was not found.")
    _require_report_project(job, payload.project_id)
    if job.get("status") not in RESULT_READY_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A report can only be generated after analysis is completed.",
        )
    if not isinstance(job.get("result"), dict):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Analysis result is unavailable.")
    if not payload.confirm:
        return ReportConfirmationResponse(
            job_id=str(job_id),
            message="确认后才会基于当前算法结果生成报告。",
        )

    service = request.app.state.report_job_service
    try:
        report_job = service.submit(job_id, payload.question, trace_id=str(payload.trace_id), project_id=payload.project_id)
    except TypeError as error:
        if "trace_id" not in str(error):
            raise
        report_job = service.submit(job_id, payload.question)
    request.app.state.audit_ledger.event(
        event_type="report_submitted", source="rest", trace_id=str(payload.trace_id),
        resource_id=str(report_job.report_job_id), status=report_job.status,
        metadata={"analysis_job_id": str(job_id)}, project_id=report_job.project_id,
    )
    return ReportJobResponse(report_job_id=str(report_job.report_job_id), analysis_job_id=str(job_id), status=report_job.status, trace_id=report_job.trace_id, project_id=report_job.project_id)


@router.get("/reports/{report_job_id}", response_model=ReportJobResponse)
def get_report_job(report_job_id: UUID, request: Request, project_id: str = "default") -> ReportJobResponse:
    project_id = _normalized_project_id(project_id)
    try:
        job = request.app.state.report_job_service.get(report_job_id)
    except KeyError as error:
        raise HTTPException(404, "报告任务不存在") from error
    _require_report_project(job, project_id)
    return ReportJobResponse(report_job_id=str(job.report_job_id), analysis_job_id=str(job.analysis_job_id), status=job.status, report=job.report, error_message=job.error_message, trace_id=job.trace_id, project_id=str(getattr(job, "project_id", None) or "default"))


@router.get("/reports/{report_job_id}/events")
async def stream_report_job(report_job_id: UUID, request: Request, follow: bool = True, project_id: str = "default") -> StreamingResponse:
    """推送报告任务状态，任务终态后自动结束连接。"""
    project_id = _normalized_project_id(project_id)
    service = request.app.state.report_job_service
    try:
        initial_job = service.get(report_job_id)
    except KeyError as error:
        raise HTTPException(404, "报告任务不存在") from error
    _require_report_project(initial_job, project_id)

    async def events():
        previous_payload: dict | None = None
        while True:
            try:
                job = service.get(report_job_id)
            except KeyError:
                yield "event: error\ndata: {\"message\": \"报告任务不存在\"}\n\n"
                return
            _require_report_project(job, project_id)
            payload = {
                "report_job_id": str(job.report_job_id),
                "analysis_job_id": str(job.analysis_job_id),
                "status": job.status,
                "error_message": job.error_message,
                "project_id": str(getattr(job, "project_id", None) or "default"),
            }
            if payload != previous_payload:
                yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                previous_payload = payload
            if job.status in {"completed", "failed"}:
                yield f"event: terminal\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            if not follow:
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


def _normalized_project_id(project_id: str | None) -> str:
    """统一报告接口的项目标识校验与规范化。"""
    try:
        return _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
