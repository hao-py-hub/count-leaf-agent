"""与企业 PRD 路径保持一致的受控兼容接口。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v2.batches import BatchSubmitRequest
from app.history import public_history_record
from app.services.job_states import RESULT_READY_STATUSES
from app.services.policy_engine import PolicyEngine


router = APIRouter(tags=["enterprise-compatibility"])
_POLICY_ENGINE = PolicyEngine()


class ReportCompatRequest(BaseModel):
    """PRD 报告入口的确认载荷。"""

    analysis_job_id: UUID
    question: str | None = Field(default=None, max_length=500)
    project_id: str = Field(default="default", min_length=1, max_length=128)
    confirm: bool = False


@router.post("/api/v2/jobs/batch")
def submit_batch_compat(payload: BatchSubmitRequest, request: Request) -> dict:
    """提供 PRD 约定的批量任务路径，复用标准批量服务和确认策略。"""
    service = request.app.state.batch_analysis_service
    payload.project_id = _normalized_project_id(payload.project_id)
    try:
        service.validate_assets(payload.asset_ids)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not payload.confirm:
        return {
            "status": "confirmation_required",
            "asset_count": len(payload.asset_ids),
            "message": "确认后才会创建批量分析任务。",
        }
    try:
        batch = service.submit_confirmed(payload.asset_ids, project_id=payload.project_id)
    except KeyError as error:
        raise HTTPException(404, "批次中存在不存在的图片资源") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {
        "batch_id": str(batch.batch_id),
        "status": "running",
        "job_ids": [str(job_id) for job_id in batch.job_ids],
    }


@router.get("/api/v2/history")
def list_history_compat(request: Request, limit: int = 100, offset: int = 0, q: str = "", project_id: str = "default") -> dict:
    """提供 PRD 约定的历史查询路径，返回去除本地路径的摘要。"""
    try:
        project_id = _normalized_project_id(project_id)
        result = request.app.state.history_store.list(limit=limit, offset=offset, query=q, project_id=project_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, "历史查询分页参数无效") from error
    return {
        "count": result["count"],
        "items": [public_history_record(item) for item in result["items"]],
        "scope": {"query": q, "limit": limit, "offset": offset},
    }


@router.get("/api/v2/history/{run_id}")
def get_history_compat(run_id: str, request: Request, project_id: str = "default") -> dict:
    """提供 PRD 约定的历史详情路径，隐藏原始本地路径。"""
    record = request.app.state.history_store.get(run_id)
    if record is None or record.get("project_id", "default") != _normalized_project_id(project_id):
        raise HTTPException(404, "历史记录不存在")
    return public_history_record(record)


@router.post("/api/v2/reports")
def create_report_compat(payload: ReportCompatRequest, request: Request) -> dict:
    """提供 PRD 报告任务入口，未确认时绝不创建任务。"""
    try:
        payload.project_id = _normalized_project_id(payload.project_id)
        job = request.app.state.analysis_job_service.get(payload.analysis_job_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error
    if job.status not in RESULT_READY_STATUSES or not isinstance(job.result, dict):
        raise HTTPException(409, "报告只能基于已完成且具有结果的分析任务生成")
    if getattr(job, "project_id", "default") != payload.project_id:
        raise HTTPException(404, "分析任务不存在")
    if not payload.confirm:
        return {
            "status": "confirmation_required",
            "analysis_job_id": str(payload.analysis_job_id),
            "message": "确认后才会创建报告任务。",
        }
    report_job = request.app.state.report_job_service.submit(
        payload.analysis_job_id, payload.question, project_id=str(getattr(job, "project_id", None) or "default")
    )
    return {
        "report_job_id": str(report_job.report_job_id),
        "analysis_job_id": str(payload.analysis_job_id),
        "status": report_job.status,
    }


@router.get("/api/v2/audit/events")
def list_audit_events_compat(request: Request, limit: int = 50, project_id: str = "default") -> dict:
    """提供 PRD 约定的审计查询路径，只返回脱敏事件和工具元数据。"""
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit 必须介于 1 和 200")
    project_id = _normalized_project_id(project_id)
    return {
        "events": request.app.state.agent_audit_service.list_events(limit=limit, project_id=project_id),
        "tool_calls": request.app.state.agent_conversation_service.list_recent_tool_calls(limit=limit, project_id=project_id),
    }


def _normalized_project_id(project_id: str | None) -> str:
    """统一兼容接口的项目标识校验与规范化。"""
    try:
        return _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
