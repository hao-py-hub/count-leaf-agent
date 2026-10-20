"""受控 Agent API，包括可回放的任务事件流。"""

import asyncio
import csv
import io
import json
from time import perf_counter
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.agent.models import (
    AgentCapabilityResponse,
    AgentRuntimeStatusResponse,
    AgentChatRequest,
    AgentChatResponse,
    AnalysisJobResponse,
    AgentMetricsResponse,
)
from app.agent.service import AgentService
from app.agent.registry import ToolRegistry
from app.agent.skills import SkillRegistry
from app.agent.function_executor import FunctionToolCall
from app.config import JOB_EXECUTOR_MODE
from app.services.phenotype_analysis_service import ALGORITHM_VERSION
from app.services.policy_engine import PolicyEngine
from app.history import public_result
from app.services.job_states import RESULT_READY_STATUSES, TERMINAL_ANALYSIS_STATUSES

router = APIRouter(prefix="/agent", tags=["agent"])
_agent_service = AgentService()
_POLICY_ENGINE = PolicyEngine()


@router.get("/capabilities", response_model=AgentCapabilityResponse)
def get_agent_capabilities() -> AgentCapabilityResponse:
    """列出 v0.1 Agent 能力节点及已批准的业务工具。"""
    return _agent_service.capabilities()


@router.get("/function-tools")
def get_function_tools() -> dict:
    """发现受限的 Function Calling Schema；执行仍由服务端控制。"""
    return {"schema_version": ToolRegistry.schema_version(), "tools": ToolRegistry.function_definitions()}


@router.get("/skills")
def get_agent_skills() -> dict:
    """发现已注册的领域 Skill；具体工具权限仍由服务端策略执行。"""
    return {"skills": [skill.as_dict() for skill in SkillRegistry.list_manifests()]}


@router.post("/function-tools/execute")
def execute_function_tool(call: FunctionToolCall, request: Request) -> dict:
    """仅为企业 Agent 调用方执行已校验的只读工具。"""
    started_at = perf_counter()
    try:
        result = request.app.state.function_tool_executor.execute(call)
        status = "completed"
    except PermissionError as exc:
        status = "forbidden"
        error = HTTPException(403, str(exc))
    except ValueError as exc:
        status = "invalid_arguments"
        error = HTTPException(422, str(exc))
    except KeyError as exc:
        status = "not_found"
        error = HTTPException(404, "受控资源不存在")
    duration_ms = round((perf_counter() - started_at) * 1000)
    request.app.state.agent_conversation_service.record_external_tool_call(
        conversation_id=str(call.conversation_id), trace_id=str(call.trace_id), tool_name=call.name.value,
        arguments=call.model_dump_json(), result_status=status,
        result_reference=_tool_result_reference(result) if status == "completed" else None,
        duration_ms=duration_ms,
    )
    request.app.state.audit_ledger.event(
        event_type="tool_call", source="function_call", trace_id=str(call.trace_id),
        resource_id=_tool_result_reference(result) if status == "completed" else None,
        status=status, metadata={"tool_name": call.name.value, "duration_ms": duration_ms},
        project_id=_function_call_project_id(call),
    )
    if status != "completed":
        raise error
    return {
        "name": call.name.value, "result": result,
        "conversation_id": str(call.conversation_id), "trace_id": str(call.trace_id),
    }


def _tool_result_reference(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    for field_name in ("job_id", "review_id", "report_job_id", "run_id", "document_id", "batch_id"):
        value = result.get(field_name)
        if value:
            return str(value)
    return None


def _function_call_project_id(call: FunctionToolCall) -> str:
    """从已经通过 Schema 校验的只读调用中提取项目范围。"""
    project_id = call.arguments.get("project_id", "default")
    return project_id.strip() if isinstance(project_id, str) and project_id.strip() else "default"


@router.get("/runtime", response_model=AgentRuntimeStatusResponse)
def get_agent_runtime_status(request: Request) -> AgentRuntimeStatusResponse:
    """返回不暴露模型凭据的非敏感能力状态。"""
    settings = request.app.state.enterprise_dependencies.llm_settings
    budget = request.app.state.analysis_job_service.budget_status()
    return AgentRuntimeStatusResponse(
        llm_enabled=settings.enabled,
        llm_configured=bool(settings.api_key and settings.model),
        provider=settings.provider,
        model=settings.model,
        daily_analysis_jobs_used=budget["used"],
        daily_analysis_jobs_limit=budget["limit"],
        daily_analysis_jobs_remaining=budget["remaining"],
        algorithm_version=ALGORITHM_VERSION,
        executor_mode=JOB_EXECUTOR_MODE,
    )


@router.post("/chat", response_model=AgentChatResponse)
def chat_with_agent(request: AgentChatRequest, http_request: Request) -> AgentChatResponse:
    """通过受限的 LangGraph 工作流路由 v0.1 Agent 请求。"""
    started_at = perf_counter()
    response = http_request.app.state.agent_workflow.invoke(request).model_copy(
        update={"conversation_id": request.conversation_id}
    )
    http_request.app.state.agent_audit_service.record(request, response)
    try:
        http_request.app.state.agent_conversation_service.record(
            request, response, duration_ms=round((perf_counter() - started_at) * 1000)
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if response.intent.value == "knowledge_qa" and response.status.value == "answered":
        http_request.app.state.audit_ledger.event(
            event_type="knowledge_answer",
            source="agent", trace_id=str(request.trace_id), status=response.status.value,
            metadata={"citation_count": len(response.citations)},
        )
        if response.citations:
            http_request.app.state.audit_ledger.event(
                event_type="knowledge_answer_cited", source="agent", trace_id=str(request.trace_id),
                status=response.status.value, metadata={"citation_count": len(response.citations)},
            )
    if response.status.value == "integration_pending" and "预算" in response.message:
        http_request.app.state.audit_ledger.event(
            event_type="budget_rejected", source="agent", trace_id=str(request.trace_id),
            status=response.status.value, metadata={"intent": response.intent.value},
        )
    return response


@router.get("/conversations/{conversation_id}")
def get_agent_conversation(conversation_id: UUID, request: Request, project_id: str = "default") -> dict:
    """回放保护隐私的会话结果与工具引用。"""
    try:
        _require_conversation_project(request.app.state.agent_conversation_service, conversation_id, project_id)
        events = request.app.state.agent_conversation_service.list_events(str(conversation_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"conversation_id": str(conversation_id), "events": events}


@router.get("/traces/{trace_id}")
def get_agent_trace(trace_id: UUID, request: Request, project_id: str = "default") -> dict:
    """按 trace_id 回放脱敏的 Agent 对话与工具调用链路。"""
    try:
        project_id = _normalized_project_id(project_id)
        audit_events = request.app.state.agent_audit_service.list_events(trace_id=str(trace_id), limit=200, project_id=project_id)
        replay = request.app.state.agent_conversation_service.list_trace_events(str(trace_id), limit=200, project_id=project_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not audit_events and not replay["events"] and not replay["tool_calls"]:
        raise HTTPException(404, "追踪记录不存在")
    return {"trace_id": str(trace_id), "audit_events": audit_events, **replay}


@router.get("/conversations/{conversation_id}/tool-calls")
def get_agent_tool_calls(conversation_id: UUID, request: Request, project_id: str = "default") -> dict:
    """读取可回放会话中已脱敏的工具调用审计记录。"""
    try:
        _require_conversation_project(request.app.state.agent_conversation_service, conversation_id, project_id)
        items = request.app.state.agent_conversation_service.list_tool_calls(str(conversation_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"conversation_id": str(conversation_id), "tool_calls": items}


@router.get("/conversations/{conversation_id}/events")
async def stream_agent_conversation_events(
    conversation_id: UUID, request: Request, follow: bool = True, project_id: str = "default"
) -> StreamingResponse:
    """流式读取追加到单个 Agent 会话的隐私保护事件。"""
    conversation_service = request.app.state.agent_conversation_service
    try:
        _require_conversation_project(conversation_service, conversation_id, project_id)
        conversation_service.list_events(str(conversation_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    async def events():
        delivered_count = 0
        while True:
            try:
                items = conversation_service.list_events(str(conversation_id))
            except KeyError:
                yield "event: error\ndata: {\"message\": \"会话不存在\"}\n\n"
                return
            for item in items[delivered_count:]:
                yield f"event: conversation\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
            delivered_count = len(items)
            if not follow:
                return
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/audit")
def list_agent_audit(request: Request, limit: int = 50, project_id: str = "default") -> dict:
    """读取最近的隐私感知 Agent 审计事件。"""
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit 必须介于 1 和 200")
    project_id = _normalized_project_id(project_id)
    return {
        "events": request.app.state.agent_audit_service.list_events(limit=limit, project_id=project_id),
        "tool_calls": request.app.state.agent_conversation_service.list_recent_tool_calls(limit=limit, project_id=project_id),
    }


@router.get("/domain-events")
def list_domain_events(request: Request, project_id: str = "default", trace_id: UUID | None = None, limit: int = 50) -> dict:
    """读取项目范围的领域事件，不暴露事件写入权限。"""
    if not 1 <= limit <= 200:
        raise HTTPException(400, "limit 必须介于 1 和 200")
    normalized_project = project_id.strip()
    if not normalized_project or len(normalized_project) > 128:
        raise HTTPException(400, "project_id 无效")
    events = request.app.state.domain_event_publisher.list_events(
        project_id=normalized_project, trace_id=str(trace_id) if trace_id else None, limit=limit,
    )
    return {"project_id": normalized_project, "trace_id": str(trace_id) if trace_id else None, "events": events}


@router.get("/domain-samples")
def list_domain_samples(request: Request, project_id: str = "default", limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise HTTPException(400, "limit 必须介于 1 和 100")
    normalized_project = project_id.strip()
    if not normalized_project or len(normalized_project) > 128:
        raise HTTPException(400, "project_id 无效")
    return {"project_id": normalized_project, "items": request.app.state.phenotype_domain_projection.list_samples(normalized_project, limit)}


@router.get("/domain-events/reconciliation")
def reconcile_domain_events(request: Request, project_id: str = "default", trace_id: UUID | None = None) -> dict:
    normalized_project = project_id.strip()
    if not normalized_project or len(normalized_project) > 128:
        raise HTTPException(400, "project_id 无效")
    return request.app.state.domain_event_publisher.reconcile_audit_events(
        project_id=normalized_project, trace_id=str(trace_id) if trace_id else None,
    )


@router.get("/metrics", response_model=AgentMetricsResponse)
def get_agent_metrics(request: Request, trace_id: UUID | None = None, since: datetime | None = None, until: datetime | None = None, project_id: str = "default") -> AgentMetricsResponse:
    """从审计账本返回 trace 范围或聚合的运营指标。"""
    if since and until and since > until:
        raise HTTPException(400, "since 不能晚于 until")
    normalized_project = project_id.strip()
    if not normalized_project or len(normalized_project) > 128:
        raise HTTPException(400, "project_id 无效")
    metrics = request.app.state.audit_ledger.metrics(
        trace_id=str(trace_id) if trace_id else None,
        since=since.isoformat(timespec="seconds") if since else None,
        until=until.isoformat(timespec="seconds") if until else None,
        project_id=normalized_project,
    )
    reconciliation = request.app.state.domain_event_publisher.reconcile_audit_events(
        project_id=normalized_project, trace_id=str(trace_id) if trace_id else None,
    )
    metrics["event_reconciliation"] = {
        "project_id": normalized_project,
        "in_sync": reconciliation["in_sync"],
        "mismatched_event_type_count": sum(
            item["difference"] != 0 for item in reconciliation["comparisons"].values()
        ),
    }
    return AgentMetricsResponse(**metrics)


@router.get("/audit/export")
def export_agent_tool_audit(request: Request, limit: int = 200) -> Response:
    """仅导出已脱敏的工具调用元数据供本地合规复核。"""
    try:
        items = request.app.state.agent_conversation_service.list_recent_tool_calls(limit=limit)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=(
            "occurred_at", "conversation_id", "trace_id", "call_origin", "tool_name",
            "result_status", "result_reference", "duration_ms", "input_sha256",
        ),
    )
    writer.writeheader()
    writer.writerows(items)
    return Response(
        content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=agent-tool-audit.csv"},
    )


@router.get("/jobs", response_model=list[AnalysisJobResponse])
def list_analysis_jobs(request: Request, limit: int = 20, project_id: str = "default") -> list[AnalysisJobResponse]:
    """列出当前项目范围内最近的任务元数据。"""
    project_id = _normalized_project_id(project_id)
    try:
        jobs = request.app.state.analysis_job_service.list_recent(limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [
        AnalysisJobResponse(**job.as_dict())
        for job in jobs
        if getattr(job, "project_id", "default") == project_id
    ]


@router.get("/jobs/{job_id}", response_model=AnalysisJobResponse)
def get_analysis_job(job_id: UUID, request: Request, project_id: str = "default") -> AnalysisJobResponse:
    """读取 Agent 提交的分析任务状态。"""
    try:
        job = request.app.state.analysis_job_service.get(job_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    _require_job_project(job, project_id)
    return AnalysisJobResponse(**job.as_dict())


@router.post("/jobs/{job_id}/retry", response_model=None)
def retry_analysis_job(job_id: UUID, request: Request, confirm: bool = False, trace_id: UUID | None = None, project_id: str = "default") -> AnalysisJobResponse | dict:
    """显式重试失败任务，并创建新的不可变任务记录。"""
    if not confirm:
        return {"status": "confirmation_required", "job_id": str(job_id), "message": "确认后才会重试失败的分析任务。"}
    try:
        _require_job_project(request.app.state.analysis_job_service.get(job_id), project_id)
        retry_id = request.app.state.analysis_job_service.retry_failed(job_id)
        job = request.app.state.analysis_job_service.get(retry_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    request.app.state.audit_ledger.event(event_type="analysis_retried", source="agent", trace_id=str(trace_id) if trace_id else job.trace_id, resource_id=str(retry_id), status=job.status, metadata={"retry_of": str(job_id)}, project_id=getattr(job, "project_id", "default"))
    return AnalysisJobResponse(**job.as_dict())


@router.post("/jobs/{job_id}/cancel", response_model=None)
def cancel_analysis_job(job_id: UUID, request: Request, confirm: bool = False, trace_id: UUID | None = None, project_id: str = "default") -> AnalysisJobResponse | dict:
    """取消仍在队列中的任务，不中断正在执行的分析。"""
    if not confirm:
        return {"status": "confirmation_required", "job_id": str(job_id), "message": "确认后才会取消排队中的分析任务。"}
    try:
        _require_job_project(request.app.state.analysis_job_service.get(job_id), project_id)
        job = request.app.state.analysis_job_service.cancel_queued(job_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    request.app.state.audit_ledger.event(event_type="analysis_cancelled", source="agent", trace_id=str(trace_id) if trace_id else job.trace_id, resource_id=str(job_id), status=job.status, metadata={}, project_id=getattr(job, "project_id", "default"))
    return AnalysisJobResponse(**job.as_dict())


@router.get("/jobs/{job_id}/events")
async def stream_analysis_job_events(job_id: UUID, request: Request, project_id: str = "default") -> StreamingResponse:
    """流式读取一项已授权本地分析任务的状态变化。"""
    try:
        _require_job_project(request.app.state.analysis_job_service.get(job_id), project_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

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


def _completed_result(job) -> dict:
    if job.status not in RESULT_READY_STATUSES:
        raise ValueError("分析任务尚未完成")
    if not isinstance(job.result, dict):
        raise ValueError("分析任务没有可用结果")
    return public_result(job.result)


@router.get("/jobs/{job_id}/result")
def get_analysis_result(job_id: UUID, request: Request, project_id: str = "default") -> dict:
    """仅在 Agent 任务完成后返回真实算法结果。"""
    try:
        job = request.app.state.analysis_job_service.get(job_id)
        _require_job_project(job, project_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        result = _completed_result(job)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"job_id": str(job_id), "result": public_result(result)}


@router.get("/jobs/{job_id}/evidence")
def get_analysis_evidence(job_id: UUID, request: Request, project_id: str = "default") -> dict:
    """聚合真实分析产物、质量反馈和版本信息供 Agent 解释使用。"""
    project_id = _normalized_project_id(project_id)
    try:
        job = request.app.state.analysis_job_service.get(job_id)
    except KeyError as error:
        raise HTTPException(404, "分析任务不存在") from error
    _require_job_project(job, project_id)
    result = public_result(job.result) if isinstance(job.result, dict) else None
    feedback = request.app.state.review_service.list_for_job(job_id, project_id=project_id)
    history_record = request.app.state.history_store.get(job.run_id, project_id=project_id) if getattr(job, "run_id", None) else None
    return {
        "job_id": str(job_id), "project_id": project_id, "status": job.status,
        "trace_id": getattr(job, "trace_id", None), "result": result,
        "provenance": result.get("provenance") if isinstance(result, dict) else None,
        "review_feedback": feedback,
        "feedback_summary": request.app.state.review_service.feedback_summary_for_job(job_id, project_id=project_id),
        "revision_diff": _revision_diff(history_record),
    }


def _revision_diff(history_record: dict | None) -> dict | None:
    """计算自动版本与当前人工版本的可解释汇总差异。"""
    if not history_record or not isinstance(history_record.get("original_result"), dict) or not isinstance(history_record.get("result"), dict):
        return None
    original, current = history_record["original_result"], history_record["result"]
    fields = ("total_pods", "branch_count", "plant_count")
    changes = {
        field: {"automatic": original.get(field), "current": current.get(field), "delta": current.get(field, 0) - original.get(field, 0)}
        for field in fields
        if isinstance(original.get(field), (int, float)) and isinstance(current.get(field), (int, float)) and current.get(field) != original.get(field)
    }
    return {"revision": history_record.get("revision", 0), "changed": bool(changes), "changes": changes}


def _require_job_project(job, project_id: str) -> None:
    """确认任务归属当前项目，避免通过任务 ID 跨项目读取或写入。"""
    project_id = _normalized_project_id(project_id)
    if getattr(job, "project_id", "default") != project_id:
        raise HTTPException(404, "分析任务不存在")


def _normalized_project_id(project_id: str | None) -> str:
    """统一 Agent 任务入口的项目标识校验与规范化。"""
    try:
        return _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def _require_conversation_project(service, conversation_id: UUID, project_id: str) -> None:
    """验证会话归属，避免通过会话标识跨项目回放。"""
    if service.get_project_id(str(conversation_id)) != _normalized_project_id(project_id):
        raise HTTPException(404, "会话不存在")
