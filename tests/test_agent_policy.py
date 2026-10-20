import unittest

from app.agent.models import AgentRole, ToolName
from app.agent.policy import ToolAuthorizationError, ToolPolicy
from app.agent.registry import ToolRegistry


class ToolPolicyTests(unittest.TestCase):
    def test_phenotyping_agent_can_submit_analysis(self):
        self.assertTrue(
            ToolPolicy.is_allowed(AgentRole.PHENOTYPING, ToolName.SUBMIT_ANALYSIS)
        )

    def test_knowledge_agent_cannot_submit_analysis(self):
        with self.assertRaises(ToolAuthorizationError):
            ToolPolicy.assert_allowed(
                AgentRole.KNOWLEDGE,
                ToolName.SUBMIT_ANALYSIS,
            )

    def test_registered_tools_match_policy_tool_enum(self):
        self.assertEqual(set(ToolRegistry.tool_names()), set(ToolName))


if __name__ == "__main__":
    unittest.main()
