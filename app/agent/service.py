"""Read-only Agent service facade used by versioned API routes."""

from .models import AgentCapabilityResponse, AgentRole, SkillCapability
from .registry import ToolRegistry
from .skills import SkillRegistry


class AgentService:
    """Provides stable Agent domain responses while orchestration is introduced."""

    def capabilities(self) -> AgentCapabilityResponse:
        return AgentCapabilityResponse(
            roles=list(AgentRole),
            tools=ToolRegistry.list_capabilities(),
            skills=[SkillCapability(**skill.as_dict()) for skill in SkillRegistry.list_manifests()],
        )
