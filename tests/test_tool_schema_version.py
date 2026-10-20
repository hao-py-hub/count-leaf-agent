import re
import unittest

from fastapi.testclient import TestClient

from app.agent.registry import ToolRegistry
from app.main import app
from app.mcp.server import McpServer
from app.agent.function_executor import FunctionToolExecutor


def build_executor():
    return FunctionToolExecutor(
        get_analysis_result=lambda value: {}, check_quality=lambda value: {}, check_job_quality=lambda value: {},
        search_history=lambda value: {}, summarize_history=lambda value: {}, get_review=lambda value: {},
        retrieve_knowledge=lambda value: [], get_batch_progress=lambda value: {}, get_report=lambda value: {},
    )


class ToolSchemaVersionTests(unittest.TestCase):
    def test_function_tool_schema_exposes_stable_version(self) -> None:
        with TestClient(app) as client:
            response = client.get("/api/v2/agent/function-tools")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["schema_version"], ToolRegistry.schema_version())
        self.assertRegex(response.json()["schema_version"], re.compile(r"^[0-9a-f]{16}$"))

    def test_mcp_initialize_exposes_same_schema_version(self) -> None:
        response = McpServer(build_executor()).handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(response["result"]["serverInfo"]["schemaVersion"], ToolRegistry.schema_version())

    def test_mcp_tools_list_exposes_same_schema_version(self) -> None:
        response = McpServer(build_executor()).handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(response["result"]["schemaVersion"], ToolRegistry.schema_version())
