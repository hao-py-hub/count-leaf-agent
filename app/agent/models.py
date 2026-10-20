"""Typed contracts shared by Agent routing, tools, and APIs."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    KNOWLEDGE = "knowledge"
    PHENOTYPING = "phenotyping"
    DATA_ANALYST = "data_analyst"
    REVIEW = "review"
    REPORT = "report"


class ToolName(StrEnum):
    SUBMIT_ANALYSIS = "submit_analysis"
    GET_ANALYSIS_RESULT = "get_analysis_result"
    GET_ANALYSIS_EVIDENCE = "get_analysis_evidence"
    QUALITY_CHECK = "quality_check"
    SEARCH_HISTORY = "search_history"
    AGGREGATE_METRICS = "aggregate_metrics"
    CREATE_REVIEW = "create_review"
    GET_REVIEW = "get_review"
    RESOLVE_REVIEW = "resolve_review"
    RETRIEVE_KNOWLEDGE = "retrieve_knowledge"
    CREATE_REPORT = "create_report"
    GET_REPORT = "get_report"
    SUBMIT_BATCH_ANALYSIS = "submit_batch_analysis"
    GET_BATCH_PROGRESS = "get_batch_progress"


class AgentIntent(StrEnum):
    PHENOTYPING = "phenotyping"
    KNOWLEDGE_QA = "knowledge_qa"
    REVIEW = "review"
    REPORT = "report"
    QUALITY_CHECK = "quality_check"
    DATA_ANALYSIS = "data_analysis"
    BATCH_ANALYSIS = "batch_analysis"
    UNSUPPORTED = "unsupported"


class AgentResponseStatus(StrEnum):
    NEEDS_ASSET = "needs_asset"
    CONFIRMATION_REQUIRED = "confirmation_required"
    SUBMITTED = "submitted"
    RESULT_RETRIEVED = "result_retrieved"
    ANSWERED = "answered"
    REVIEW_CREATED = "review_created"
    REVIEW_RETRIEVED = "review_retrieved"
    REVIEW_RESOLVED = "review_resolved"
    REVIEW_UNAVAILABLE = "review_unavailable"
    REPORT_SUBMITTED = "report_submitted"
    REPORT_GENERATED = "report_generated"
    REPORT_RETRIEVED = "report_retrieved"
    BATCH_SUBMITTED = "batch_submitted"
    BATCH_RETRIEVED = "batch_retrieved"
    REPORT_UNAVAILABLE = "report_unavailable"
    QUALITY_REPORTED = "quality_reported"
    QUALITY_WARNING = "quality_warning"
    DATA_RETRIEVED = "data_retrieved"
    DATA_SUMMARIZED = "data_summarized"
    NEEDS_JOB = "needs_job"
    INTEGRATION_PENDING = "integration_pending"
    UNSUPPORTED = "unsupported"


class ToolCapability(BaseModel):
    """A safe, user-facing description of a registered business tool."""

    name: ToolName
    description: str
    mutates_data: bool = False


class SkillCapability(BaseModel):
    """稳定的 Skill 发现 Schema，不授予 Skill 额外权限。"""

    name: str
    version: str
    role: AgentRole
    tools: list[ToolName] = Field(default_factory=list)
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    confirmation_points: list[str] = Field(default_factory=list)
    budget_limit: int | None = None


class AgentCapabilityResponse(BaseModel):
    """Response model for the read-only Agent capability endpoint."""

    version: str = "v0.1"
    framework: str = "controlled-multi-agent"
    roles: list[AgentRole] = Field(default_factory=list)
    tools: list[ToolCapability] = Field(default_factory=list)
    skills: list[SkillCapability] = Field(default_factory=list)


class AgentRuntimeStatusResponse(BaseModel):
    """Non-sensitive runtime capability state for the local workbench."""

    llm_enabled: bool
    llm_configured: bool
    provider: str
    model: str
    daily_analysis_jobs_used: int
    daily_analysis_jobs_limit: int
    daily_analysis_jobs_remaining: int
    app_version: str = "0.1.0"
    algorithm: str = "stalk"
    algorithm_version: str = "stalk-v0.1"
    executor_mode: str = "in_process"


class AgentCacheMetrics(BaseModel):
    hit_count: int
    miss_count: int
    request_count: int
    hit_rate: float | None = None
    estimated_model_calls_saved: int
    estimated_cost_saved: float


class AgentAnalysisMetrics(BaseModel):
    completed_count: int
    review_created_count: int
    review_rate: float | None = None
    review_reason_counts: dict[str, int] = Field(default_factory=dict)


class AgentBatchMetrics(BaseModel):
    submitted_count: int
    cancelled_count: int
    retried_count: int


class AgentOperationMetrics(BaseModel):
    report_submitted_count: int
    report_completed_count: int
    report_failed_count: int
    history_exported_count: int


class AgentWriteOperationMetrics(BaseModel):
    executed_count: int
    unconfirmed_executed_count: int
    unconfirmed_execution_rate: float | None = None


class AgentKnowledgeMetrics(BaseModel):
    answer_count: int
    cited_answer_count: int
    citation_coverage: float | None = None


class AgentModelCallMetrics(BaseModel):
    count: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    cache_hit_count: int


class AgentBudgetMetrics(BaseModel):
    rejection_count: int


class AgentMcpMetrics(BaseModel):
    call_count: int
    forbidden_count: int
    forbidden_rate: float


class AgentEventReconciliationMetrics(BaseModel):
    project_id: str
    in_sync: bool
    mismatched_event_type_count: int


class AgentMetricsResponse(BaseModel):
    trace_id: str | None = None
    project_id: str = "default"
    since: str | None = None
    until: str | None = None
    cache: AgentCacheMetrics
    analysis: AgentAnalysisMetrics
    batch: AgentBatchMetrics
    operations: AgentOperationMetrics
    write_operations: AgentWriteOperationMetrics
    knowledge: AgentKnowledgeMetrics
    model_calls: AgentModelCallMetrics
    budget: AgentBudgetMetrics
    mcp: AgentMcpMetrics
    event_reconciliation: AgentEventReconciliationMetrics
    event_counts: dict[str, int]


class AgentChatRequest(BaseModel):
    """Validated input for the first controlled Agent workflow."""

    message: str = Field(min_length=1, max_length=2_000)
    project_id: str = Field(default="default", min_length=1, max_length=128)
    conversation_id: UUID = Field(default_factory=uuid4)
    trace_id: UUID = Field(default_factory=uuid4)
    asset_id: UUID | None = None
    asset_ids: list[UUID] = Field(default_factory=list, max_length=20)
    analysis_job_id: UUID | None = None
    batch_id: UUID | None = None
    review_id: UUID | None = None
    report_job_id: UUID | None = None
    confirm_quality_warning: bool = False
    confirm_submission: bool = False
    confirm_batch_submission: bool = False
    confirm_review: bool = False
    confirm_review_resolution: bool = False
    review_resolution: str | None = Field(default=None, min_length=1, max_length=2_000)
    revision_payload: dict | None = None
    confirm_report: bool = False
    history_query: str = Field(default="", max_length=120)
    data_mode: str = Field(default="history", pattern="^(history|summary)$")
    intent: AgentIntent | None = None


class AgentChatResponse(BaseModel):
    """A workflow response that never fabricates an analysis result."""

    intent: AgentIntent
    status: AgentResponseStatus
    message: str
    trace_id: UUID | None = None
    conversation_id: UUID | None = None
    requested_tool: ToolName | None = None
    skill_name: str | None = None
    skill_version: str | None = None
    job_id: UUID | None = None
    review_id: UUID | None = None
    report_job_id: UUID | None = None
    batch: dict | None = None
    quality: dict | None = None
    data: dict | None = None
    citations: list[str] = Field(default_factory=list)


class AgentAssetResponse(BaseModel):
    asset_id: UUID
    source_label: str
    project_id: str = "default"


class AnalysisJobResponse(BaseModel):
    job_id: UUID
    asset_id: UUID
    project_id: str = "default"
    status: str
    created_at: str
    updated_at: str
    run_id: str | None = None
    error_message: str | None = None
    cache_hit: bool = False
    trace_id: str | None = None
    retry_of: str | None = None
