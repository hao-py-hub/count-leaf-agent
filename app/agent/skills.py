"""受控领域 Skill 的版本化声明与注册表。"""

from __future__ import annotations

from dataclasses import dataclass

from .models import AgentIntent, AgentRole, ToolName
from .policy import ROLE_TOOL_ALLOWLIST


@dataclass(frozen=True)
class SkillManifest:
    """描述一个只能编排业务工具的领域流程包。"""

    name: str
    version: str
    role: AgentRole
    tools: frozenset[ToolName]
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    confirmation_points: tuple[str, ...] = ()
    budget_limit: int | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ValueError("Skill 必须包含名称和版本")
        if self.budget_limit is not None and self.budget_limit < 0:
            raise ValueError("Skill 预算上限不能为负数")
        for schema_name, schema in (("输入", self.input_schema), ("输出", self.output_schema)):
            if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
                raise ValueError(f"Skill {schema_name} Schema 必须是 object 且包含 properties")
        unauthorized = self.tools - ROLE_TOOL_ALLOWLIST[self.role]
        if unauthorized:
            names = ", ".join(sorted(tool.value for tool in unauthorized))
            raise ValueError(f"Skill 工具超出角色白名单：{names}")

    def as_dict(self) -> dict:
        """返回可供 Agent 发现的稳定 JSON 结构。"""
        return {
            "name": self.name,
            "version": self.version,
            "role": self.role.value,
            "tools": sorted(tool.value for tool in self.tools),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "confirmation_points": list(self.confirmation_points),
            "budget_limit": self.budget_limit,
        }


class SkillRegistry:
    """首期领域 Skill 注册表；Skill 不替代 ToolPolicy。"""

    _SKILLS: tuple[SkillManifest, ...] = (
        SkillManifest(
            name="phenotype-analysis",
            version="v0.1",
            role=AgentRole.PHENOTYPING,
            tools=frozenset({ToolName.SUBMIT_ANALYSIS, ToolName.GET_ANALYSIS_RESULT, ToolName.SUBMIT_BATCH_ANALYSIS, ToolName.GET_BATCH_PROGRESS}),
            input_schema={
                "type": "object",
                "properties": {"asset_id": {"type": "string", "format": "uuid"}, "asset_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}}},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"job_id": {"type": "string", "format": "uuid"}, "batch_id": {"type": "string", "format": "uuid"}, "status": {"type": "string"}},
                "required": ["status"],
            },
            confirmation_points=("submit_analysis",),
            budget_limit=1,
        ),
        SkillManifest(
            name="image-quality-review",
            version="v0.1",
            role=AgentRole.REVIEW,
            tools=frozenset({ToolName.QUALITY_CHECK, ToolName.CREATE_REVIEW}),
            input_schema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string", "format": "uuid"},
                    "job_id": {"type": "string", "format": "uuid"},
                },
                "required": ["asset_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"recommended_to_retake": {"type": "boolean"}, "issues": {"type": "array"}},
                "required": ["recommended_to_retake", "issues"],
            },
            confirmation_points=("create_review",),
        ),
        SkillManifest(
            name="knowledge-grounded-answer",
            version="v0.1",
            role=AgentRole.KNOWLEDGE,
            tools=frozenset({ToolName.RETRIEVE_KNOWLEDGE}),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500}},
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}, "citations": {"type": "array"}},
                "required": ["answer", "citations"],
            },
        ),
        SkillManifest(
            name="project-statistics",
            version="v0.1",
            role=AgentRole.DATA_ANALYST,
            tools=frozenset({ToolName.SEARCH_HISTORY, ToolName.AGGREGATE_METRICS}),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string", "maxLength": 120}, "mode": {"enum": ["history", "summary"]}},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"sample_count": {"type": "integer"}, "items": {"type": "array"}},
            },
        ),
    )

    @classmethod
    def list_manifests(cls) -> list[SkillManifest]:
        """返回注册 Skill 的副本，避免调用方修改注册表。"""
        return list(cls._SKILLS)

    @classmethod
    def get(cls, name: str, version: str | None = None) -> SkillManifest:
        """按名称和可选版本获取 Skill，不存在时明确报错。"""
        for skill in cls._SKILLS:
            if skill.name == name and (version is None or skill.version == version):
                return skill
        raise KeyError(f"未注册 Skill：{name}" if version is None else f"未注册 Skill：{name}@{version}")

    @classmethod
    def for_intent(cls, intent: AgentIntent) -> SkillManifest | None:
        """将已路由的用户意图映射到对应 Skill；不支持的意图不写入 Skill。"""
        names = {
            AgentIntent.PHENOTYPING: "phenotype-analysis",
            AgentIntent.QUALITY_CHECK: "image-quality-review",
            AgentIntent.KNOWLEDGE_QA: "knowledge-grounded-answer",
            AgentIntent.DATA_ANALYSIS: "project-statistics",
        }
        name = names.get(intent)
        return cls.get(name) if name else None
