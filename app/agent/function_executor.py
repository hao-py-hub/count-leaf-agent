"""企业 Function Calling 客户端的受限执行适配器。"""

from __future__ import annotations

from typing import Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .models import ToolName


MCP_READ_ONLY_TOOLS = frozenset(
    {
        ToolName.GET_ANALYSIS_RESULT,
        ToolName.QUALITY_CHECK,
        ToolName.SEARCH_HISTORY,
        ToolName.AGGREGATE_METRICS,
        ToolName.GET_REVIEW,
        ToolName.RETRIEVE_KNOWLEDGE,
    }
)
READ_ONLY_TOOLS = MCP_READ_ONLY_TOOLS | frozenset({ToolName.GET_ANALYSIS_EVIDENCE, ToolName.GET_BATCH_PROGRESS, ToolName.GET_REPORT})


class FunctionToolCall(BaseModel):
    """已校验的企业工具调用信封，不提供任意代码执行面。"""

    name: ToolName
    arguments: dict[str, object] = Field(default_factory=dict)
    conversation_id: UUID = Field(default_factory=uuid4)
    trace_id: UUID = Field(default_factory=uuid4)


class FunctionToolExecutor:
    """仅通过服务回调执行已列入白名单的只读业务工具。"""

    def __init__(
        self,
        *,
        get_analysis_result: Callable[[UUID, str], dict],
        check_quality: Callable[[UUID], dict],
        check_job_quality: Callable[[UUID, str], dict],
        search_history: Callable[[str, str], dict],
        summarize_history: Callable[[str, str], dict],
        get_review: Callable[[UUID, str], dict],
        retrieve_knowledge: Callable[[str, str], list[dict]],
        get_batch_progress: Callable[[UUID, str], dict],
        get_report: Callable[[UUID, str], dict],
        get_analysis_evidence: Callable[[UUID, str], dict] | None = None,
    ) -> None:
        self._handlers = {
            ToolName.GET_ANALYSIS_RESULT: get_analysis_result,
            ToolName.GET_ANALYSIS_EVIDENCE: get_analysis_evidence,
            ToolName.QUALITY_CHECK: check_quality,
            ToolName.SEARCH_HISTORY: search_history,
            ToolName.AGGREGATE_METRICS: summarize_history,
            ToolName.GET_REVIEW: get_review,
            ToolName.RETRIEVE_KNOWLEDGE: retrieve_knowledge,
            ToolName.GET_BATCH_PROGRESS: get_batch_progress,
            ToolName.GET_REPORT: get_report,
        }
        self._check_job_quality = check_job_quality

    def execute(self, call: FunctionToolCall) -> dict:
        """分发经过严格校验的只读业务操作。"""
        if call.name not in READ_ONLY_TOOLS:
            raise PermissionError("该工具会产生写入或需要显式确认，不能通过企业执行端点调用")
        handler = self._handlers[call.name]
        if handler is None:
            raise PermissionError("该只读工具尚未在当前运行环境启用")
        arguments = call.arguments
        if call.name is ToolName.QUALITY_CHECK:
            return self._execute_quality_check(arguments)
        if call.name in {ToolName.GET_ANALYSIS_RESULT, ToolName.GET_ANALYSIS_EVIDENCE, ToolName.GET_REVIEW, ToolName.GET_BATCH_PROGRESS, ToolName.GET_REPORT}:
            key = {
                ToolName.GET_ANALYSIS_RESULT: "analysis_job_id",
                ToolName.GET_ANALYSIS_EVIDENCE: "analysis_job_id",
                ToolName.QUALITY_CHECK: "asset_id",
                ToolName.GET_REVIEW: "review_id",
                ToolName.GET_BATCH_PROGRESS: "batch_id",
                ToolName.GET_REPORT: "report_job_id",
            }[call.name]
            resource_id, project_id = self._scoped_uuid(arguments, key)
            return handler(resource_id, project_id)
        if call.name is ToolName.SEARCH_HISTORY:
            query, project_id = self._scoped_query(arguments, "query", 120)
            return handler(query, project_id)
        if call.name is ToolName.AGGREGATE_METRICS:
            query, project_id = self._scoped_query(arguments, "history_query", 120)
            return handler(query, project_id)
        query, project_id = self._scoped_query(arguments, "query", 500, required=True)
        return {"hits": handler(query, project_id)}

    def _execute_quality_check(self, arguments: dict[str, object]) -> dict:
        self._reject_unknown(arguments, {"asset_id", "analysis_job_id", "project_id"})
        asset_id = arguments.get("asset_id")
        job_id = arguments.get("analysis_job_id")
        if isinstance(asset_id, str) and job_id is None:
            return self._handlers[ToolName.QUALITY_CHECK](self._required_uuid({"asset_id": asset_id}, "asset_id"))
        if isinstance(job_id, str) and asset_id is None:
            job_id, project_id = self._scoped_uuid(arguments, "analysis_job_id")
            return self._check_job_quality(job_id, project_id)
        raise ValueError("quality_check 必须且只能提供 asset_id 或 analysis_job_id")

    @staticmethod
    def _required_uuid(arguments: dict[str, object], key: str) -> UUID:
        FunctionToolExecutor._reject_unknown(arguments, {key})
        value = arguments.get(key)
        if not isinstance(value, str):
            raise ValueError(f"{key} 必须是 UUID 字符串")
        try:
            return UUID(value)
        except ValueError as error:
            raise ValueError(f"{key} 必须是有效 UUID") from error

    @staticmethod
    def _required_string(arguments: dict[str, object], key: str, maximum: int) -> str:
        FunctionToolExecutor._reject_unknown(arguments, {key})
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{key} 必须是长度不超过 {maximum} 的非空字符串")
        return value.strip()

    @staticmethod
    def _optional_string(arguments: dict[str, object], key: str, maximum: int) -> str:
        FunctionToolExecutor._reject_unknown(arguments, {key})
        value = arguments.get(key, "")
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{key} 必须是长度不超过 {maximum} 的字符串")
        return value.strip()

    @classmethod
    def _scoped_query(cls, arguments: dict[str, object], key: str, maximum: int, *, required: bool = False) -> tuple[str, str]:
        cls._reject_unknown(arguments, {key, "project_id"})
        query = cls._required_string({key: arguments.get(key)}, key, maximum) if required else cls._optional_string({key: arguments.get(key, "")}, key, maximum)
        project_id = arguments.get("project_id", "default")
        if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 128:
            raise ValueError("project_id 必须是长度不超过 128 的非空字符串")
        return query, project_id.strip()

    @classmethod
    def _scoped_uuid(cls, arguments: dict[str, object], key: str) -> tuple[UUID, str]:
        cls._reject_unknown(arguments, {key, "project_id"})
        resource_id = cls._required_uuid({key: arguments.get(key)}, key)
        project_id = arguments.get("project_id", "default")
        if not isinstance(project_id, str) or not project_id.strip() or len(project_id) > 128:
            raise ValueError("project_id 必须是长度不超过 128 的非空字符串")
        return resource_id, project_id.strip()

    @staticmethod
    def _reject_unknown(arguments: dict[str, object], allowed: set[str]) -> None:
        unexpected = set(arguments) - allowed
        if unexpected:
            raise ValueError("工具参数不允许包含额外字段")
