"""Tool authorization rules for the controlled multi-Agent workflow."""

from collections.abc import Mapping

from .models import AgentRole, ToolName


class ToolAuthorizationError(PermissionError):
    """Raised when an Agent role attempts to use an unapproved tool."""


ROLE_TOOL_ALLOWLIST: Mapping[AgentRole, frozenset[ToolName]] = {
    AgentRole.SUPERVISOR: frozenset(),
    AgentRole.KNOWLEDGE: frozenset({ToolName.RETRIEVE_KNOWLEDGE}),
    AgentRole.PHENOTYPING: frozenset(
        {
            ToolName.SUBMIT_ANALYSIS,
            ToolName.GET_ANALYSIS_RESULT,
            ToolName.QUALITY_CHECK,
            ToolName.SUBMIT_BATCH_ANALYSIS,
            ToolName.GET_BATCH_PROGRESS,
        }
    ),
    AgentRole.DATA_ANALYST: frozenset(
        {ToolName.SEARCH_HISTORY, ToolName.AGGREGATE_METRICS}
    ),
    AgentRole.REVIEW: frozenset(
        {
            ToolName.QUALITY_CHECK,
            ToolName.CREATE_REVIEW,
            ToolName.GET_REVIEW,
            ToolName.RESOLVE_REVIEW,
        }
    ),
    AgentRole.REPORT: frozenset({ToolName.CREATE_REPORT, ToolName.GET_REPORT}),
}


class ToolPolicy:
    """Single source of truth for per-role tool authorization."""

    @staticmethod
    def is_allowed(role: AgentRole, tool_name: ToolName) -> bool:
        return tool_name in ROLE_TOOL_ALLOWLIST[role]

    @classmethod
    def assert_allowed(cls, role: AgentRole, tool_name: ToolName) -> None:
        if not cls.is_allowed(role, tool_name):
            raise ToolAuthorizationError(
                f"Agent role '{role.value}' cannot call tool '{tool_name.value}'."
            )
