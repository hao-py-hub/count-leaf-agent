"""Skill manifest 注册与权限边界测试。"""

import unittest

from app.agent.models import AgentRole, ToolName
from app.agent.skills import SkillManifest, SkillRegistry


class SkillManifestTests(unittest.TestCase):
    def test_registry_contains_documented_skills(self):
        names = {skill.name for skill in SkillRegistry.list_manifests()}
        self.assertEqual(
            names,
            {
                "phenotype-analysis",
                "image-quality-review",
                "knowledge-grounded-answer",
                "project-statistics",
            },
        )

    def test_manifest_rejects_tools_outside_role_allowlist(self):
        with self.assertRaises(ValueError):
            SkillManifest(
                name="unsafe",
                version="v0.1",
                role=AgentRole.KNOWLEDGE,
                tools=frozenset({ToolName.SUBMIT_ANALYSIS}),
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object", "properties": {}},
            )

    def test_manifest_is_discoverable_as_json(self):
        payload = SkillRegistry.get("phenotype-analysis").as_dict()
        self.assertEqual(payload["version"], "v0.1")
        self.assertEqual(payload["tools"], ["get_analysis_result", "get_batch_progress", "submit_analysis", "submit_batch_analysis"])
        self.assertEqual(payload["budget_limit"], 1)
        self.assertIn("asset_id", payload["input_schema"]["properties"])
        self.assertIn("asset_ids", payload["input_schema"]["properties"])
        self.assertEqual(payload["output_schema"]["properties"]["job_id"]["format"], "uuid")

    def test_manifest_rejects_incomplete_schema(self):
        with self.assertRaisesRegex(ValueError, "输入 Schema"):
            SkillManifest(
                name="invalid-schema",
                version="v0.1",
                role=AgentRole.KNOWLEDGE,
                tools=frozenset({ToolName.RETRIEVE_KNOWLEDGE}),
                input_schema={},
                output_schema={"type": "object", "properties": {}},
            )


if __name__ == "__main__":
    unittest.main()
