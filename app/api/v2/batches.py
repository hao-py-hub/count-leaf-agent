"""Confirmed local batch-analysis API."""

import asyncio
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.responses import Response
from openpyxl import Workbook
import io
from pydantic import BaseModel, Field

from app.services.batch_analysis_service import BatchAnalysisService, MAX_BATCH_ASSETS
from app.services.policy_engine import PolicyEngine


router = APIRouter(prefix="/api/v2/batches", tags=["batches"])
_POLICY_ENGINE = PolicyEngine()


class BatchSubmitRequest(BaseModel):
    asset_ids: list[UUID] = Field(min_length=1, max_length=MAX_BATCH_ASSETS)
    project_id: str = Field(default="default", min_length=1, max_length=128)
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)


class BatchWriteRequest(BaseModel):
    confirm: bool = False
    trace_id: UUID = Field(default_factory=uuid4)
    project_id: str = Field(default="default", min_length=1, max_length=128)


@router.post("")
def submit_batch(payload: BatchSubmitRequest, request: Request) -> dict:
    payload.project_id = _normalized_project_id(payload.project_id)
    service: BatchAnalysisService = request.app.state.batch_analysis_service
    try:
        service.validate_assets(payload.asset_ids)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not payload.confirm:
        return {"status": "confirmation_required", "project_id": payload.project_id, "asset_count": len(payload.asset_ids), "message": "确认后才会创建批量分析任务。"}
    try:
        try:
            batch = service.submit_confirmed(payload.asset_ids, trace_id=str(payload.trace_id), project_id=payload.project_id)
        except TypeError as error:
            if "trace_id" not in str(error) and "project_id" not in str(error):
                raise
            batch = service.submit_confirmed(payload.asset_ids)
    except KeyError as error:
        raise HTTPException(404, "批次中存在不存在的图片资源") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    project_id = getattr(batch, "project_id", payload.project_id)
    request.app.state.audit_ledger.event(event_type="batch_submitted", source="rest", trace_id=str(payload.trace_id), resource_id=str(batch.batch_id), status="running", metadata={"job_count": len(batch.job_ids), "project_id": project_id}, project_id=project_id)
    return {"batch_id": str(batch.batch_id), "project_id": project_id, "status": "running", "job_ids": [str(job_id) for job_id in batch.job_ids]}


@router.get("")
def list_batches(request: Request, limit: int = 20, project_id: str = "default") -> dict:
    project_id = _normalized_project_id(project_id)
    try:
        return {"items": request.app.state.batch_analysis_service.list_recent(limit=limit, project_id=project_id)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/{batch_id}")
def get_batch_status(batch_id: UUID, request: Request, project_id: str = "default") -> dict:
    project_id = _normalized_project_id(project_id)
    try:
        result = request.app.state.batch_analysis_service.get_status(batch_id)
        if result.get("project_id", "default") != project_id:
            raise KeyError("批次不存在")
        return result
    except KeyError as error:
        raise HTTPException(404, str(error)) from error

@router.get("/{batch_id}/export.xlsx")
def export_batch_summary(batch_id: UUID, request: Request, project_id: str = "default") -> Response:
    project_id = _normalized_project_id(project_id)
    try:
        batch = request.app.state.batch_analysis_service.get_status(batch_id)
    except KeyError as error:
        raise HTTPException(404, "批次不存在") from error
    if batch.get("project_id", "default") != project_id:
        raise HTTPException(404, "批次不存在")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "batch_summary"
    sheet.append(["批次 ID", str(batch_id)])
    sheet.append(["状态", batch.get("status", "")])
    sheet.append(["任务总数", batch.get("total", 0)])
    sheet.append([])
    headers = ["植株数量", "分枝数量", "主花序角果", "主干 + 主枝", "分枝角果", "角果总数"]
    sheet.append(headers)
    totals = {"plant_count": 0, "branch_count": 0, "main_inflorescence_pod_count": 0, "main_stem_plus_main_pod_count": 0, "branch_pod_count": 0, "total_pods": 0}
    for job_id in batch.get("job_ids", []):
        try:
            job = request.app.state.analysis_job_service.get(UUID(str(job_id)))
            if job.status != "completed" or not job.result:
                continue
            result = job.result
            for key in totals:
                totals[key] += int(result.get(key) or 0)
        except (KeyError, ValueError):
            continue
    sheet.append([totals["plant_count"], totals["branch_count"], totals["main_inflorescence_pod_count"], totals["main_stem_plus_main_pod_count"], totals["branch_pod_count"], totals["total_pods"]])
    output = io.BytesIO()
    workbook.save(output)
    return Response(content=output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=batch-{batch_id}.xlsx"})


@router.get("/{batch_id}/events")
async def stream_batch_status(
    batch_id: UUID, request: Request, follow: bool = True, project_id: str = "default"
) -> StreamingResponse:
    """推送批量聚合进度，终态后自动关闭 SSE 连接。"""
    project_id = _normalized_project_id(project_id)
    service = request.app.state.batch_analysis_service
    try:
        initial = service.get_status(batch_id)
        if initial.get("project_id", "default") != project_id:
            raise KeyError("批次不存在")
    except KeyError as error:
        raise HTTPException(404, str(error)) from error

    async def events():
        previous_payload: dict | None = None
        while True:
            try:
                payload = service.get_status(batch_id)
            except KeyError:
                yield "event: error\ndata: {\"message\": \"批量任务不存在\"}\n\n"
                return
            if payload.get("project_id", "default") != project_id:
                yield "event: error\ndata: {\"message\": \"批量任务不存在\"}\n\n"
                return
            if payload != previous_payload:
                yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                previous_payload = payload
            if payload["status"] == "completed":
                yield f"event: terminal\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            if not follow:
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/{batch_id}/cancel")
def cancel_batch(batch_id: UUID, request: Request, payload: BatchWriteRequest | None = None) -> dict:
    """Explicitly cancel only the batch's still-queued child tasks."""
    if payload is None or not payload.confirm:
        return {"status": "confirmation_required", "batch_id": str(batch_id), "message": "确认后才会取消批次中仍在排队的任务。"}
    try:
        payload.project_id = _normalized_project_id(payload.project_id)
        current = request.app.state.batch_analysis_service.get_status(batch_id)
        if current.get("project_id", "default") != payload.project_id:
            raise KeyError("批次不存在")
        result = request.app.state.batch_analysis_service.cancel_queued(batch_id)
        request.app.state.audit_ledger.event(
            event_type="batch_cancelled", source="rest", trace_id=str(payload.trace_id),
            resource_id=str(batch_id), status="cancelled",
            metadata={"cancelled_count": result["cancelled_count"], "running_count": result["running_count"]}, project_id=payload.project_id,
        )
        return result
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/{batch_id}/retry-failed")
def retry_failed_batch_items(batch_id: UUID, request: Request, payload: BatchWriteRequest | None = None) -> dict:
    """Explicitly start a new batch containing only failed child tasks."""
    if payload is None or not payload.confirm:
        return {"status": "confirmation_required", "batch_id": str(batch_id), "message": "确认后才会重试批次中的失败任务。"}
    try:
        payload.project_id = _normalized_project_id(payload.project_id)
        current = request.app.state.batch_analysis_service.get_status(batch_id)
        if current.get("project_id", "default") != payload.project_id:
            raise KeyError("批次不存在")
        batch = request.app.state.batch_analysis_service.retry_failed(batch_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    request.app.state.audit_ledger.event(event_type="batch_retried", source="rest", trace_id=str(payload.trace_id), resource_id=str(batch.batch_id), status="running", metadata={"retry_of": str(batch_id), "job_count": len(batch.job_ids)}, project_id=payload.project_id)
    return {"batch_id": str(batch.batch_id), "status": "running", "job_ids": [str(job_id) for job_id in batch.job_ids]}


def _normalized_project_id(project_id: str | None) -> str:
    """统一批次接口的项目标识校验与规范化。"""
    try:
        return _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
