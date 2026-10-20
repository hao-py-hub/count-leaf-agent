"""面向 Agent 编排层公开的业务工具注册表。"""

from collections.abc import Iterable
import hashlib
import json

from .models import ToolCapability, ToolName


class ToolRegistry:
    """在服务实现接线前描述可用工具。"""

    _CAPABILITIES: tuple[ToolCapability, ...] = (
        ToolCapability(
            name=ToolName.SUBMIT_ANALYSIS,
            description="Submit an authorized plant image for asynchronous analysis.",
            mutates_data=True,
        ),
        ToolCapability(
            name=ToolName.GET_ANALYSIS_RESULT,
            description="Read a completed phenotyping analysis result.",
        ),
        ToolCapability(
            name=ToolName.GET_ANALYSIS_EVIDENCE,
            description="Read authorized analysis evidence, provenance, and review feedback.",
        ),
        ToolCapability(
            name=ToolName.QUALITY_CHECK,
            description="Read quality flags for an analysis run.",
        ),
        ToolCapability(
            name=ToolName.SEARCH_HISTORY,
            description="Search authorized project analysis history.",
        ),
        ToolCapability(
            name=ToolName.AGGREGATE_METRICS,
            description="Aggregate metrics within an authorized project scope.",
        ),
        ToolCapability(
            name=ToolName.CREATE_REVIEW,
            description="Create a human review task for an analysis result.",
            mutates_data=True,
        ),
        ToolCapability(
            name=ToolName.GET_REVIEW,
            description="Read the status of a human review task.",
        ),
        ToolCapability(
            name=ToolName.RESOLVE_REVIEW,
            description="Resolve an assigned human review task.",
            mutates_data=True,
        ),
        ToolCapability(
            name=ToolName.RETRIEVE_KNOWLEDGE,
            description="Retrieve cited SOP and project knowledge.",
        ),
        ToolCapability(
            name=ToolName.CREATE_REPORT,
            description="Create an asynchronous report generation task.",
            mutates_data=True,
        ),
        ToolCapability(
            name=ToolName.GET_REPORT,
            description="Read an authorized report generation result.",
        ),
        ToolCapability(
            name=ToolName.SUBMIT_BATCH_ANALYSIS,
            description="Submit a confirmed, bounded set of authorized images for analysis.",
            mutates_data=True,
        ),
        ToolCapability(
            name=ToolName.GET_BATCH_PROGRESS,
            description="Read aggregate status for an authorized analysis batch.",
        ),
    )

    @classmethod
    def list_capabilities(cls) -> list[ToolCapability]:
        return list(cls._CAPABILITIES)

    @classmethod
    def tool_names(cls) -> Iterable[ToolName]:
        return (capability.name for capability in cls._CAPABILITIES)

    @classmethod
    def schema_version(cls) -> str:
        payload = json.dumps(cls.function_definitions(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def function_definitions(cls) -> list[dict]:
        """返回供企业 Agent 发现的 Function Calling 风格 Schema。"""
        schemas = {
            ToolName.SUBMIT_ANALYSIS: {"asset_id": {"type": "string", "format": "uuid"}},
            ToolName.GET_ANALYSIS_RESULT: {"analysis_job_id": {"type": "string", "format": "uuid"}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            ToolName.GET_ANALYSIS_EVIDENCE: {"analysis_job_id": {"type": "string", "format": "uuid"}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            ToolName.QUALITY_CHECK: {
                "asset_id": {"type": "string", "format": "uuid"},
                "analysis_job_id": {"type": "string", "format": "uuid"},
                "project_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            ToolName.SEARCH_HISTORY: {"query": {"type": "string", "maxLength": 120}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            ToolName.AGGREGATE_METRICS: {"history_query": {"type": "string", "maxLength": 120}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            ToolName.CREATE_REVIEW: {
                "analysis_job_id": {"type": "string", "format": "uuid"},
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            ToolName.GET_REVIEW: {"review_id": {"type": "string", "format": "uuid"}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            ToolName.RESOLVE_REVIEW: {
                "review_id": {"type": "string", "format": "uuid"},
                "resolution": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            ToolName.RETRIEVE_KNOWLEDGE: {"query": {"type": "string", "minLength": 1, "maxLength": 500}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            ToolName.CREATE_REPORT: {
                "analysis_job_id": {"type": "string", "format": "uuid"},
                "question": {"type": "string", "maxLength": 500},
            },
            ToolName.GET_REPORT: {"report_job_id": {"type": "string", "format": "uuid"}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
            ToolName.SUBMIT_BATCH_ANALYSIS: {"asset_ids": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "format": "uuid"}}},
            ToolName.GET_BATCH_PROGRESS: {"batch_id": {"type": "string", "format": "uuid"}, "project_id": {"type": "string", "minLength": 1, "maxLength": 128}},
        }
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": capability.name.value,
                    "description": capability.description,
                    "parameters": {
                        "type": "object",
                        "properties": schemas[capability.name],
                        "required": [
                            name for name in schemas[capability.name]
                            if not (capability.name is ToolName.CREATE_REPORT and name == "question")
                        ],
                        "additionalProperties": False,
                    },
                },
            }
            for capability in cls._CAPABILITIES
        ]
        quality = next(item for item in definitions if item["function"]["name"] == ToolName.QUALITY_CHECK.value)
        quality["function"]["parameters"]["required"] = []
        quality["function"]["parameters"]["oneOf"] = [
            {"required": ["asset_id"]},
            {"required": ["analysis_job_id"]},
        ]
        return definitions
