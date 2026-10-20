"""Teacher-managed local knowledge base endpoints."""

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.rag.models import RetrievalHit
from app.rag.file_loader import load_knowledge_text
from app.agent.knowledge_qa import KnowledgeAnswerService
from app.agent.llm import DeepSeekChatClient, LlmConfigurationError, LlmSettings
from app.config import MAX_KNOWLEDGE_UPLOAD_BYTES
from app.services.upload_limits import read_upload_limited
from app.services.policy_engine import PolicyEngine

router = APIRouter(prefix="/api/v2/knowledge", tags=["knowledge"])
_POLICY_ENGINE = PolicyEngine()


class KnowledgeIngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50_000)
    project_id: str = Field(default="default", min_length=1, max_length=128)


class KnowledgeIngestResponse(BaseModel):
    document_id: str
    chunk_count: int


class KnowledgeSearchResponse(BaseModel):
    hits: list[RetrievalHit]


class KnowledgeStatusResponse(BaseModel):
    backend: str
    retrieval_mode: str
    document_count: int
    chunk_count: int
    chunk_size: int
    chunk_overlap: int
    knowledge_version: int
    query_cache_hits: int
    query_cache_misses: int
    query_cache_entries: int


class KnowledgeAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    project_id: str = Field(default="default", min_length=1, max_length=128)


class KnowledgeAnswerResponse(BaseModel):
    answer: str
    citations: list[str]


@router.post("/documents", response_model=KnowledgeIngestResponse)
def ingest_document(payload: KnowledgeIngestRequest, request: Request) -> KnowledgeIngestResponse:
    try:
        payload.project_id = _POLICY_ENGINE.require_project_id(payload.project_id)
        document_id, chunk_count = request.app.state.knowledge_service.ingest(**payload.model_dump())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return KnowledgeIngestResponse(document_id=document_id, chunk_count=chunk_count)


@router.post("/documents/upload", response_model=KnowledgeIngestResponse)
async def upload_document(request: Request, file: UploadFile = File(...), project_id: str = "default") -> KnowledgeIngestResponse:
    """Upload a UTF-8 text or Markdown knowledge document for chunked ingestion."""
    try:
        project_id = _POLICY_ENGINE.require_project_id(project_id)
        title, content = load_knowledge_text(
            file.filename or "", read_upload_limited(file.file, MAX_KNOWLEDGE_UPLOAD_BYTES)
        )
        document_id, chunk_count = request.app.state.knowledge_service.ingest(title=title, content=content, project_id=project_id)
    except ValueError as error:
        status_code = 413 if "大小上限" in str(error) else 400
        raise HTTPException(status_code, str(error)) from error
    return KnowledgeIngestResponse(document_id=document_id, chunk_count=chunk_count)


@router.get("/search", response_model=KnowledgeSearchResponse)
def search_knowledge(query: str, request: Request, limit: int = 5, project_id: str = "default") -> KnowledgeSearchResponse:
    if not query.strip():
        raise HTTPException(400, "查询不能为空")
    if not 1 <= limit <= 10:
        raise HTTPException(400, "limit 必须介于 1 和 10")
    try:
        project_id = _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return KnowledgeSearchResponse(hits=request.app.state.knowledge_service.search(query, limit=limit, project_id=project_id))


@router.get("/status", response_model=KnowledgeStatusResponse)
def get_knowledge_status(request: Request, project_id: str = "default") -> KnowledgeStatusResponse:
    """Return local retrieval readiness without disclosing knowledge contents."""
    try:
        project_id = _POLICY_ENGINE.require_project_id(project_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return KnowledgeStatusResponse(**request.app.state.knowledge_service.status(project_id=project_id))


@router.post("/answer", response_model=KnowledgeAnswerResponse)
def answer_from_knowledge(payload: KnowledgeAnswerRequest, request: Request) -> KnowledgeAnswerResponse:
    settings = LlmSettings.from_environment()
    if not settings.enabled:
        raise HTTPException(409, "LLM 问答未启用。请将 LLM_ENABLED 设为 true。")
    try:
        payload.project_id = _POLICY_ENGINE.require_project_id(payload.project_id)
        answer = KnowledgeAnswerService(
            request.app.state.knowledge_service,
            DeepSeekChatClient(settings),
        ).answer(payload.question, project_id=payload.project_id)
    except LlmConfigurationError as error:
        raise HTTPException(503, str(error)) from error
    except Exception as error:
        raise HTTPException(502, "知识库问答生成失败") from error
    return KnowledgeAnswerResponse(answer=answer.answer, citations=answer.citations)
