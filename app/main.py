"""
CV Agent — Plant Phenotyping API Server
FastAPI backend for rapeseed plant analysis.
"""

import logging
import uuid
import re
import time
from pathlib import Path

import json as _json

from fastapi import FastAPI, File, Request, UploadFile, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response
from openpyxl import Workbook
import io
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v2.agent import router as agent_v2_router
from app.api.v2.assets import router as asset_v2_router
from app.api.v2.reports import router as agent_reports_router
from app.api.v2.knowledge import router as knowledge_v2_router
from app.api.v2.reviews import router as review_v2_router
from app.api.v2.batches import router as batch_v2_router
from app.api.v2.jobs import router as jobs_v2_router
from app.api.v2.quality import router as quality_v2_router
from app.api.v2.exports import router as exports_v2_router
from app.api.v2.compat import router as enterprise_compat_router
from app.bootstrap import build_enterprise_dependencies
from app.agent.workflow import AgentWorkflow
from app.agent.knowledge_qa import KnowledgeAnswerService
from app.agent.llm import DeepSeekChatClient
from app.agent.reporting import PhenotypeReportWriter
from app.agent.function_executor import FunctionToolExecutor
from app.config import (
    UPLOAD_DIR,
    RESULT_DIR,
    ONLINE_UPLOAD_DIR,
    HISTORY_DB_PATH,
    KNOWLEDGE_DB_PATH,
    AGENT_AUDIT_DB_PATH,
    AUDIT_LEDGER_DB_PATH,
    AGENT_CONVERSATION_DB_PATH,
    REVIEW_DB_PATH,
    DAILY_ANALYSIS_JOB_LIMIT,
    ANALYSIS_JOB_TIMEOUT_SECONDS,
    RAG_RETRIEVAL_MODE,
    CORS_ALLOWED_ORIGINS,
    MAX_IMAGE_UPLOAD_BYTES,
    MAX_IMAGE_PIXELS,
    JOB_EXECUTOR_MODE,
    HOST,
    PORT,
)
from app.history import HistoryStore, public_history_record, public_result
from app import cancel
from app.services.analysis_job_service import AnalysisJobService
from app.services.asset_store import AssetStore
from app.services.phenotype_analysis_service import PhenotypeAnalysisService
from app.rag.service import KnowledgeService
from app.services.agent_audit_service import AgentAuditService
from app.services.audit_ledger import AuditLedger
from app.services.agent_conversation_service import AgentConversationService
from app.services.review_service import ReviewService
from app.services.job_states import RESULT_READY_STATUSES
from app.services.review_resolution_service import ReviewResolutionService
from app.services.image_quality_service import ImageQualityService
from app.services.review_reasoning import review_reason_details
from app.services.domain_events import SqliteDomainEventPublisher
from app.services.job_dispatcher import CeleryAnalysisJobDispatcher
from app.services.batch_analysis_service import BatchAnalysisService
from app.services.report_job_service import ReportJobService
from app.services.phenotype_domain_projection import PhenotypeDomainProjection
from app.services.upload_limits import copy_upload_limited, verify_image_upload
from app.services.error_messages import ANALYSIS_FAILURE_MESSAGE, failure_diagnostic

app = FastAPI(title="CV Agent - Plant Phenotyping", version="0.1.0")
LOGGER = logging.getLogger(__name__)
history_store = HistoryStore(HISTORY_DB_PATH)
# SSE 连接异常或服务中断可能让旧任务遗留 processing 状态；启动时一次性收敛。
_stale_processing_count = history_store.fail_stale_processing()
if _stale_processing_count:
    LOGGER.warning("recovered_stale_processing_records count=%s", _stale_processing_count)
phenotype_analysis_service = PhenotypeAnalysisService(history_store)
enterprise_dependencies = build_enterprise_dependencies()
agent_asset_store = AssetStore(enterprise_dependencies.assets)
review_service = ReviewService(REVIEW_DB_PATH)
image_quality_service = ImageQualityService()
audit_ledger = AuditLedger(AUDIT_LEDGER_DB_PATH)


def _analysis_dispatcher():
    """Select the durable Celery boundary only for the enterprise mode."""
    if JOB_EXECUTOR_MODE != "celery":
        return None
    from app.infrastructure.tasks.phenotype_tasks import execute_analysis_job

    return CeleryAnalysisJobDispatcher(execute_analysis_job)


def _review_reasons(quality: dict, result: dict) -> list[str]:
    return review_reason_details(quality, result)[0]


def _enqueue_low_quality_review(job) -> bool:
    app.state.phenotype_domain_projection.project_completed_analysis(job)
    quality = image_quality_service.inspect(agent_asset_store.get(job.asset_id).file_path)
    reasons, codes = review_reason_details(quality, job.result or {})
    app.state.phenotype_domain_projection.record_quality_findings(job, reasons, codes)
    if reasons:
        reason_code = ",".join(codes)
        review = review_service.create(job.job_id, "；".join(reasons), trace_id=job.trace_id, reason_code=reason_code, project_id=job.project_id)
        audit_ledger.event(
            event_type="review_created", source="review_trigger", trace_id=job.trace_id,
            resource_id=str(job.job_id), status="open",
            metadata={"review_id": review["review_id"], "reason_code": reason_code}, project_id=job.project_id,
        )
        return True
    return False


domain_event_publisher = SqliteDomainEventPublisher(AGENT_AUDIT_DB_PATH)
phenotype_domain_projection = PhenotypeDomainProjection(enterprise_dependencies.database.session_factory)
analysis_job_service = AnalysisJobService(
    agent_asset_store, phenotype_analysis_service,
    repository=enterprise_dependencies.analysis_jobs,
    daily_job_limit=DAILY_ANALYSIS_JOB_LIMIT,
    timeout_seconds=ANALYSIS_JOB_TIMEOUT_SECONDS,
    on_completed=_enqueue_low_quality_review,
    dispatcher=_analysis_dispatcher(),
    audit_event=audit_ledger.event,
    record_model_call=audit_ledger.model_call,
    cache_revoked=audit_ledger.is_cache_revoked,
    domain_event_publisher=domain_event_publisher,
)
knowledge_service = KnowledgeService(KNOWLEDGE_DB_PATH, retrieval_mode=RAG_RETRIEVAL_MODE)
agent_audit_service = AgentAuditService(AGENT_AUDIT_DB_PATH)
agent_conversation_service = AgentConversationService(AGENT_CONVERSATION_DB_PATH)
batch_analysis_service = BatchAnalysisService(
    analysis_job_service, repository=enterprise_dependencies.analysis_batches
)
report_job_service = ReportJobService(
    lambda job_id, question: _generate_report(job_id, question),
    audit_event=audit_ledger.event,
    domain_event_publisher=domain_event_publisher,
    database_path=REVIEW_DB_PATH.with_name("report_jobs.sqlite3"),
)


def _answer_knowledge(message: str, project_id: str = "default") -> tuple[str, list[str]]:
    answer = KnowledgeAnswerService(
        knowledge_service,
        DeepSeekChatClient(enterprise_dependencies.llm_settings),
    ).answer(message, project_id=project_id)
    return answer.answer, answer.citations


def _create_review(job_id, reason: str):
    from uuid import UUID

    job = analysis_job_service.get(job_id)
    if job.status not in RESULT_READY_STATUSES:
        raise ValueError("复核只能针对已完成的分析任务创建。")
    record = review_service.create(job_id, reason, project_id=job.project_id)
    app.state.audit_ledger.event(event_type="review_created", source="agent", resource_id=str(job_id), status="open", metadata={"review_id": record["review_id"], "reason_code": record["reason_code"]}, project_id=job.project_id)
    return UUID(record["review_id"])


def _create_review_with_trace(job_id, reason: str, trace_id):
    from uuid import UUID
    try:
        project_id = analysis_job_service.get(job_id).project_id
    except KeyError:
        project_id = "default"
    record = review_service.create(job_id, reason, trace_id=str(trace_id), project_id=project_id)
    app.state.audit_ledger.event(event_type="review_created", source="agent", trace_id=str(trace_id), resource_id=str(job_id), status="open", metadata={"review_id": record["review_id"], "reason_code": record["reason_code"]}, project_id=project_id)
    return UUID(record["review_id"])


def _generate_report(job_id, question: str) -> str:
    job = analysis_job_service.get(job_id)
    if job.status not in RESULT_READY_STATUSES or not job.result:
        raise ValueError("报告只能基于已完成且具有原始结果的分析任务生成。")
    return PhenotypeReportWriter(DeepSeekChatClient(enterprise_dependencies.llm_settings)).write(
        analysis_result=job.result,
        user_question=question,
    )


def _submit_report_with_trace(job_id, question: str, trace_id) -> dict:
    try:
        project_id = analysis_job_service.get(job_id).project_id
    except KeyError:
        project_id = "default"
    try:
        report_job = report_job_service.submit(job_id, question, trace_id=str(trace_id), project_id=project_id)
    except TypeError as error:
        if "project_id" not in str(error):
            raise
        report_job = report_job_service.submit(job_id, question, trace_id=str(trace_id))
    app.state.audit_ledger.event(
        event_type="report_submitted", source="agent", trace_id=str(trace_id),
        resource_id=str(report_job.report_job_id), status=report_job.status,
        metadata={"analysis_job_id": str(job_id)}, project_id=project_id,
    )
    return {"report_job_id": str(report_job.report_job_id), "status": report_job.status}


def _check_quality(asset_id):
    return image_quality_service.inspect(agent_asset_store.get(asset_id).file_path)


def _check_job_quality(job_id, project_id="default"):
    job = analysis_job_service.get(job_id)
    if job.project_id != project_id:
        raise KeyError("分析任务不存在")
    return _check_quality(job.asset_id)


def _search_history(query: str, project_id: str = "default") -> dict:
    """按项目范围查询兼容历史记录。"""
    return history_store.list(limit=20, query=query, project_id=project_id)


def _summarize_history(query: str, project_id: str = "default") -> dict:
    """按项目范围汇总兼容历史记录。"""
    return history_store.summarize(query=query, project_id=project_id)


def _summarize_history_for_session(request) -> dict:
    """在单个会话内缓存非权威的本地项目聚合摘要。"""
    query = request.history_query
    project_id = request.project_id
    history_version = history_store.summary_cache_version(project_id=project_id)
    conversation_id = str(request.conversation_id)
    cached = agent_conversation_service.get_session_summary(
        conversation_id=conversation_id, query=query, history_version=f"{project_id}:{history_version}",
    )
    if cached is not None:
        return cached
    summary = history_store.summarize(query=query, project_id=project_id)
    return agent_conversation_service.cache_session_summary(
        conversation_id=conversation_id, query=query, history_version=f"{project_id}:{history_version}",
        summary=summary, reference_ids=summary.get("reference_ids", []),
    )


def _get_agent_result(job_id, project_id="default"):
    job = analysis_job_service.get(job_id)
    if job.status not in RESULT_READY_STATUSES or not isinstance(job.result, dict):
        raise ValueError("分析结果尚未完成")
    if job.project_id != project_id:
        raise KeyError("分析任务不存在")
    return job.result


def _get_analysis_evidence(job_id, project_id="default"):
    """返回受控工具可读取的真实结果、版本和复核反馈证据。"""
    job = analysis_job_service.get(job_id)
    if job.project_id != project_id:
        raise KeyError("分析任务不存在")
    result = public_result(job.result) if isinstance(job.result, dict) else None
    return {
        "job_id": str(job_id), "project_id": project_id, "status": job.status,
        "trace_id": job.trace_id, "result": result,
        "provenance": result.get("provenance") if isinstance(result, dict) else None,
        "review_feedback": review_service.list_for_job(job_id, project_id=project_id),
    }


def _get_review(review_id, project_id="default"):
    record = review_service.get(review_id)
    if record["project_id"] != project_id:
        raise KeyError("复核任务不存在")
    return record


def _resolve_review(review_id, resolution: str, revision_payload: dict) -> dict:
    return _resolve_review_with_trace(review_id, resolution, revision_payload, None)


def _resolve_review_with_trace(review_id, resolution: str, revision_payload: dict, trace_id, project_id="default") -> dict:
    record = ReviewResolutionService(
        review_service=review_service,
        analysis_job_service=analysis_job_service,
        history_store=history_store,
        write_result_snapshot=app.state.write_result_snapshot,
        project_manual_revision=app.state.phenotype_domain_projection.project_manual_revision,
    ).resolve(review_id, resolution, revision_payload, expected_project_id=project_id)
    app.state.audit_ledger.event(
        event_type="review_resolved", source="agent", trace_id=str(trace_id) if trace_id else None,
        resource_id=str(review_id), status=record["status"],
        metadata={"run_id": record["run_id"], "history_revision": record["history_revision"]}, project_id=record.get("project_id", "default"),
    )
    return record


def _get_report(report_job_id, project_id="default"):
    report = report_job_service.get(report_job_id)
    if report.project_id != project_id:
        raise KeyError("报告任务不存在")
    return {
        "report_job_id": str(report.report_job_id),
        "analysis_job_id": str(report.analysis_job_id),
        "status": report.status,
        "report": report.report,
        "error_message": report.error_message,
    }


def _submit_batch_analysis(asset_ids):
    batch = batch_analysis_service.submit_confirmed(asset_ids)
    return {
        "batch_id": str(batch.batch_id),
        "status": "running",
        "job_ids": [str(job_id) for job_id in batch.job_ids],
    }


def _submit_batch_analysis_with_trace(asset_ids, trace_id, project_id="default"):
    batch = batch_analysis_service.submit_confirmed(asset_ids, trace_id=str(trace_id), project_id=project_id)
    return {"batch_id": str(batch.batch_id), "project_id": batch.project_id, "status": "running", "job_ids": [str(job_id) for job_id in batch.job_ids]}


def _get_batch_progress(batch_id, project_id="default"):
    status = batch_analysis_service.get_status(batch_id)
    if status["project_id"] != project_id:
        raise KeyError("批量任务不存在")
    return status


def _retrieve_knowledge(query: str, project_id: str = "default") -> list[dict]:
    """按项目范围检索知识库并返回可引用的命中片段。"""
    return [hit.__dict__ for hit in knowledge_service.search(query, limit=5, project_id=project_id)]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Apply browser safety headers without changing API or asset payloads."""
    response = await call_next(request)
    content_type = response.headers.get("Content-Type", "")
    if content_type.startswith("application/json") and "charset=" not in content_type.lower():
        response.headers["Content-Type"] = f"{content_type}; charset=utf-8"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response
app.include_router(agent_v2_router, prefix="/api/v2")
app.include_router(asset_v2_router, prefix="/api/v2")
app.include_router(agent_reports_router)
app.include_router(knowledge_v2_router)
app.include_router(review_v2_router)
app.include_router(batch_v2_router)
app.include_router(jobs_v2_router)
app.include_router(quality_v2_router)
app.include_router(exports_v2_router)
app.include_router(enterprise_compat_router)
app.state.agent_asset_store = agent_asset_store
app.state.domain_event_publisher = domain_event_publisher
app.state.phenotype_domain_projection = phenotype_domain_projection
app.state.analysis_job_service = analysis_job_service
app.state.knowledge_service = knowledge_service
app.state.agent_audit_service = agent_audit_service
app.state.audit_ledger = audit_ledger
app.state.agent_conversation_service = agent_conversation_service
app.state.enterprise_dependencies = enterprise_dependencies
app.state.review_service = review_service
app.state.history_store = history_store
app.state.batch_analysis_service = batch_analysis_service
app.state.function_tool_executor = FunctionToolExecutor(
    get_analysis_result=_get_agent_result,
    get_analysis_evidence=_get_analysis_evidence,
    check_quality=_check_quality,
    check_job_quality=_check_job_quality,
    search_history=_search_history,
    summarize_history=_summarize_history_for_session,
    get_review=_get_review,
    retrieve_knowledge=_retrieve_knowledge,
    get_batch_progress=_get_batch_progress,
    get_report=_get_report,
)
app.state.agent_workflow = AgentWorkflow(
    submit_analysis=analysis_job_service.submit,
    submit_analysis_with_trace=lambda asset_id, trace_id, project_id="default": analysis_job_service.submit(asset_id, trace_id=str(trace_id), project_id=project_id),
    answer_knowledge=_answer_knowledge if enterprise_dependencies.llm_settings.enabled else None,
    create_review=_create_review,
    create_review_with_trace=_create_review_with_trace,
    generate_report=_generate_report if enterprise_dependencies.llm_settings.enabled else None,
    submit_report_with_trace=_submit_report_with_trace,
    check_quality=_check_quality,
    search_history=_search_history,
    summarize_history=_summarize_history,
    get_analysis_result=_get_agent_result,
    get_analysis_evidence=_get_analysis_evidence,
    get_review=_get_review,
    get_report=_get_report,
    resolve_review=_resolve_review,
    resolve_review_with_trace=_resolve_review_with_trace,
    check_job_quality=_check_job_quality,
    submit_batch_analysis=_submit_batch_analysis,
    submit_batch_analysis_with_trace=_submit_batch_analysis_with_trace,
    get_batch_progress=_get_batch_progress,
)
app.state.report_job_service = report_job_service


def analyze_plant_image_stream(*args, **kwargs):
    """Lazy compatibility proxy for the existing CV pipeline stream."""
    from app.pipeline.analyzer import analyze_plant_image_stream as pipeline_stream

    return pipeline_stream(*args, **kwargs)


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


def _safe_run_part(value: str, fallback: str = "image") -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return safe[:80] or fallback


def _make_run_id(source_name: str) -> str:
    stem = _safe_run_part(Path(source_name).stem)
    return f"{stem}__{int(time.time() * 1000)}__{uuid.uuid4().hex[:8]}"


def _result_directory(run_id: str) -> Path:
    """Resolve a result directory only when its opaque run identifier is safe."""
    if not run_id or _safe_run_part(run_id, fallback="") != run_id:
        raise HTTPException(404, "Result not found")
    target = (RESULT_DIR / run_id).resolve()
    try:
        target.relative_to(RESULT_DIR.resolve())
    except ValueError as error:
        raise HTTPException(404, "Result not found") from error
    return target


def _write_result_snapshot(run_id: str, result: dict) -> None:
    result_dir = _result_directory(run_id)
    result_dir.mkdir(parents=True, exist_ok=True)
    result_file = result_dir / "result.json"
    temp_file = result_dir / "result.json.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        _json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    temp_file.replace(result_file)


app.state.write_result_snapshot = _write_result_snapshot


def _analysis_stream(
    image_path: str,
    run_id: str,
    source_label: str,
    source_kind: str,
):
    steps = []
    terminal_event_seen = False
    history_store.start(run_id, source_label, image_path, source_kind)
    try:
        for event in analyze_plant_image_stream(
            image_path,
            method="stalk",
            run_id=run_id,
            cache_key=run_id,
            source_label=source_label,
        ):
            event_type = event.get("type")
            if event_type == "step":
                steps.append(event)
            elif event_type == "result":
                event = history_store.complete(run_id, event, steps)
                _write_result_snapshot(run_id, event)
                terminal_event_seen = True
            elif event_type == "error":
                history_store.fail(run_id, event.get("message", "分析失败"), steps)
                terminal_event_seen = True
            yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"

        if not terminal_event_seen:
            message = "分析流程结束但没有返回结果"
            history_store.fail(run_id, message, steps)
            yield f"data: {_json.dumps({'type': 'error', 'run_id': run_id, 'message': message}, ensure_ascii=False)}\n\n"
    except GeneratorExit:
        history_store.fail(run_id, "客户端中断分析", steps)
        raise
    except Exception as exc:
        LOGGER.error(
            "compatibility_analysis_failed run_id=%s error_type=%s",
            run_id,
            failure_diagnostic(exc),
        )
        history_store.fail(run_id, ANALYSIS_FAILURE_MESSAGE, steps)
        event = {"type": "error", "run_id": run_id, "message": ANALYSIS_FAILURE_MESSAGE}
        yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"


# Serve result images
app.mount("/results", NoCacheStaticFiles(directory=str(RESULT_DIR)), name="results")
# Serve online_uploads (for thumbnail preview in picker)
app.mount("/online_uploads", NoCacheStaticFiles(directory=str(ONLINE_UPLOAD_DIR)), name="online_uploads")

_IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}


@app.get("/api/online_images")
def list_online_images():
    """Recursively list all image files under online_uploads."""
    items = []
    for p in sorted(ONLINE_UPLOAD_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in _IMG_EXT:
            rel = p.relative_to(ONLINE_UPLOAD_DIR).as_posix()
            items.append({
                "path": rel,
                "name": p.name,
                "folder": p.parent.relative_to(ONLINE_UPLOAD_DIR).as_posix() or "/",
                "size": p.stat().st_size,
                "url": f"/online_uploads/{rel}",
            })
    return {"count": len(items), "items": items}


@app.post("/api/analyze_online")
async def analyze_online(payload: dict):
    """Analyze an image that already exists under online_uploads. payload: {"path": "relative/path.jpg"}"""
    rel = (payload or {}).get("path", "")
    method = (payload or {}).get("method", "stalk")
    if method != "stalk":
        raise HTTPException(400, "Only the stalk method is supported")
    if not rel:
        raise HTTPException(400, "Missing 'path'")
    target = (ONLINE_UPLOAD_DIR / rel).resolve()
    # Security: ensure within ONLINE_UPLOAD_DIR
    try:
        target.relative_to(ONLINE_UPLOAD_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not target.exists() or target.suffix.lower() not in _IMG_EXT:
        raise HTTPException(404, "Image not found")

    run_id = _make_run_id(Path(rel).name)

    return StreamingResponse(
        _analysis_stream(str(target), run_id, rel, "online"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "cv-agent"}


@app.get("/api/ready")
def readiness():
    """Return non-sensitive dependency readiness for local/enterprise probes."""
    checks = {"database": "ok", "executor": "ok", "llm": "disabled"}
    try:
        with enterprise_dependencies.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "error"
    if JOB_EXECUTOR_MODE not in {"in_process", "celery"}:
        checks["executor"] = "invalid"
    elif JOB_EXECUTOR_MODE == "celery":
        checks["executor"] = "configured"
    settings = enterprise_dependencies.llm_settings
    if settings.enabled:
        try:
            settings.validate()
            checks["llm"] = "configured"
        except Exception:
            checks["llm"] = "invalid"
    status = "ready" if all(value in {"ok", "disabled", "configured"} for value in checks.values()) else "not_ready"
    return {"status": status, "service": "cv-agent", "checks": checks}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), method: str = "stalk"):
    """
    Upload a plant image → SSE stream of pipeline steps.
    Each line: data: {"type":"step"|"result"|"error", ...}
    """
    ext = Path(file.filename or "unknown.jpg").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}:
        raise HTTPException(400, f"Unsupported file type: {ext}")
    if method != "stalk":
        raise HTTPException(400, "Only the stalk method is supported")

    file_id = uuid.uuid4().hex[:8]
    save_name = f"{file_id}{ext}"
    save_path = UPLOAD_DIR / save_name
    try:
        copy_upload_limited(file.file, save_path, MAX_IMAGE_UPLOAD_BYTES)
        verify_image_upload(save_path, MAX_IMAGE_PIXELS)
    except ValueError as error:
        status_code = 413 if "大小上限" in str(error) else 400
        raise HTTPException(status_code, str(error)) from error

    run_id = _make_run_id(file.filename or save_name)

    return StreamingResponse(
        _analysis_stream(
            str(save_path),
            run_id,
            file.filename or save_name,
            "upload",
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/api/history")
def list_history(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=120),
    project_id: str = Query("default", min_length=1, max_length=120),
):
    return history_store.list(limit=limit, offset=offset, query=q, project_id=project_id)


@app.post("/api/history/{run_id}/restore")
def restore_history_result(run_id: str):
    try:
        result = history_store.restore(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    _write_result_snapshot(run_id, result)
    return public_history_record(history_store.get(run_id))


@app.put("/api/history/{run_id}")
def update_history_result(run_id: str, payload: dict):
    try:
        result = history_store.update(run_id, payload or {})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _write_result_snapshot(run_id, result)
    return public_history_record(history_store.get(run_id))


@app.get("/api/history/{run_id}")
def get_history_result(run_id: str):
    record = history_store.get(run_id)
    if not record:
        raise HTTPException(404, "历史记录不存在")
    return public_history_record(record)

@app.get("/api/history/{run_id}/thumbnail")
def get_history_thumbnail(run_id: str):
    """Serve a durable thumbnail, falling back to the original source image."""
    record = history_store.get(run_id)
    if not record:
        raise HTTPException(404, "History record not found")
    candidates = [RESULT_DIR / run_id / "step_01.png"]
    source_path = record.get("source_path")
    if source_path:
        candidates.append(Path(source_path))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
    raise HTTPException(404, "Thumbnail not available")

@app.get("/api/history/{run_id}/export.xlsx")
def export_history_item_xlsx(run_id: str):
    record = history_store.get(run_id)
    if not record or not record.get("result"):
        raise HTTPException(404, "Completed history record not found")
    result = public_result(record["result"])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "result"
    plants = result.get("plants", []) or []
    numbered = sorted((p for p in plants if p.get("id") is not None), key=lambda p: int(p.get("id", 0)))
    headers = ["植株数量", "分枝数量", "主花序角果", "主干 + 主枝", "分枝角果", "角果总数"]
    headers.extend([f"{plant.get('id')}号角果数量" for plant in numbered])
    values = [result.get("plant_count", len(plants)), result.get("branch_count", 0), result.get("main_inflorescence_pod_count", 0), result.get("main_stem_plus_main_pod_count", 0), result.get("branch_pod_count", 0), result.get("total_pods", 0)]
    values.extend([plant.get("pod_count", 0) for plant in numbered])
    image_id = Path(record.get("source_label") or "").stem
    dji_id = re.match(r"^(DJI_\d{8}_\d{6}_\d{4})(?:_|$)", image_id)
    if dji_id:
        image_id = dji_id.group(1)
    sheet.append(["图片编号", image_id])
    sheet.append(headers)
    sheet.append(values)
    output = io.BytesIO()
    workbook.save(output)
    return Response(content=output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=phenotype-{run_id}.xlsx"})


class FolderSummaryRequest(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=500)
    project_id: str = "default"


@app.post("/api/history/folder-summary.xlsx")
def export_folder_summary(payload: FolderSummaryRequest) -> Response:
    if len(set(payload.run_ids)) != len(payload.run_ids):
        raise HTTPException(422, "Duplicate run IDs")
    fields = ("plant_count", "branch_count", "main_inflorescence_pod_count", "main_stem_plus_main_pod_count", "branch_pod_count", "total_pods")
    totals = {field: 0 for field in fields}
    for run_id in payload.run_ids:
        record = history_store.get(run_id, project_id=payload.project_id)
        if not record or record["status"] != "completed" or not record.get("result"):
            raise HTTPException(409, f"Run is not completed: {run_id}")
        for field in fields:
            totals[field] += int(record["result"].get(field) or 0)
    book = Workbook()
    sheet = book.active
    sheet.title = "汇总"
    sheet.append(["植株数量", "分枝数量", "主花序角果", "主干 + 主枝", "分枝角果", "角果总数"])
    sheet.append([totals[field] for field in fields])
    output = io.BytesIO()
    book.save(output)
    return Response(content=output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=folder-summary.xlsx"})


@app.get("/api/results/{run_id}")
def get_result(run_id: str):
    """Get the current persisted result, including any manual revisions."""
    record = history_store.get(run_id)
    if record and record.get("result"):
        return public_result(record["result"])

    result_dir = _result_directory(run_id)
    result_file = result_dir / "result.json"
    if not result_file.exists():
        raise HTTPException(404, "Result not found")
    with open(result_file, encoding="utf-8") as f:
        return public_result(_json.load(f))


# Serve frontend (must be after API routes)
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)





