"""Contracts for enterprise Function Calling tool discovery."""

import unittest

from fastapi.testclient import TestClient

from app.agent.registry import ToolRegistry
from app.main import app


class AgentFunctionToolTests(unittest.TestCase):
    def test_schemas_are_restricted_to_registered_tools(self):
        definitions = ToolRegistry.function_definitions()
        names = {item["function"]["name"] for item in definitions}
        self.assertEqual(names, {name.value for name in ToolRegistry.tool_names()})
        for definition in definitions:
            self.assertFalse(definition["function"]["parameters"]["additionalProperties"])
        report = next(item for item in definitions if item["function"]["name"] == "create_report")
        self.assertNotIn("question", report["function"]["parameters"]["required"])
        get_report = next(item for item in definitions if item["function"]["name"] == "get_report")
        self.assertEqual(set(get_report["function"]["parameters"]["properties"]), {"report_job_id", "project_id"})
        quality = next(item for item in definitions if item["function"]["name"] == "quality_check")
        self.assertEqual(quality["function"]["parameters"]["oneOf"], [{"required": ["asset_id"]}, {"required": ["analysis_job_id"]}])

    def test_discovery_endpoint_returns_function_definitions(self):
        response = TestClient(app).get("/api/v2/agent/function-tools")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["tools"])
        self.assertEqual(response.json()["tools"][0]["type"], "function")

    def test_capabilities_and_skill_discovery_return_versioned_manifests(self):
        capabilities = TestClient(app).get("/api/v2/agent/capabilities")
        self.assertEqual(capabilities.status_code, 200)
        skills = capabilities.json()["skills"]
        self.assertEqual({skill["name"] for skill in skills}, {
            "phenotype-analysis", "image-quality-review",
            "knowledge-grounded-answer", "project-statistics",
        })
        direct = TestClient(app).get("/api/v2/agent/skills")
        self.assertEqual(direct.status_code, 200)
        self.assertEqual(direct.json()["skills"], skills)

    def test_discovery_exposes_no_write_tools_to_mcp_surface(self):
        from app.mcp.server import list_tools

        names = {tool["name"] for tool in list_tools()}
        self.assertEqual(names, {
            "get_analysis_result", "quality_check", "search_history",
            "aggregate_metrics", "get_review", "retrieve_knowledge",
        })
        self.assertNotIn("submit_analysis", names)
        self.assertNotIn("resolve_review", names)


if __name__ == "__main__":
    unittest.main()
