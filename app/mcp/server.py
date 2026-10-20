"""面向本地企业客户端的只读 Model Context Protocol 服务。

服务刻意仅暴露与 Function Calling 端点相同的六项只读能力；分析提交、
人工修订、报告以及其他写操作仍只能经由需要显式确认的 REST 流程执行。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.agent.function_executor import FunctionToolCall, FunctionToolExecutor, MCP_READ_ONLY_TOOLS
from app.agent.registry import ToolRegistry

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "count-beaf-agent", "version": "0.1.0"}


def _tool_definition(name: str) -> dict[str, Any]:
    for item in ToolRegistry.function_definitions():
        function = item["function"]
        if function["name"] == name:
            input_schema = dict(function["parameters"])
            if name in {"search_history", "aggregate_metrics"}:
                input_schema["required"] = []
            return {
                "name": name,
                "description": function["description"],
                "inputSchema": input_schema,
            }
    raise KeyError(f"未注册的只读工具：{name}")


def list_tools() -> list[dict[str, Any]]:
    """返回由共享工具注册表生成的 MCP Schema。"""
    return [_tool_definition(str(name)) for name in sorted(MCP_READ_ONLY_TOOLS, key=str)]


class McpServer:
    """为受限 MCP 工具面提供最小化 stdio JSON-RPC 传输。"""

    def __init__(
        self,
        executor: FunctionToolExecutor,
        audit_tool_call: Callable[..., None] | None = None,
        audit_event: Callable[..., None] | None = None,
    ) -> None:
        self._executor = executor
        self._audit_tool_call = audit_tool_call
        self._audit_event = audit_event

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """处理一条 MCP JSON-RPC 消息，不写入业务数据。"""
        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return self._error(message_id, -32602, "params 必须是对象")

        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._result(message_id, {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {**SERVER_INFO, "schemaVersion": ToolRegistry.schema_version()},
            })
        if method == "tools/list":
            return self._result(message_id, {"schemaVersion": ToolRegistry.schema_version(), "tools": list_tools()})
        if method == "tools/call":
            return self._call_tool(message_id, params)
        return self._error(message_id, -32601, f"不支持的 MCP 方法：{method}")

    def _call_tool(self, message_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        call_trace_id = uuid4()
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return self._error(message_id, -32602, "tools/call 需要 name 和对象 arguments")
        if name not in {str(tool_name) for tool_name in MCP_READ_ONLY_TOOLS}:
            if self._audit_event is not None:
                self._audit_event(event_type="mcp_forbidden", source="mcp", trace_id=str(call_trace_id), status="forbidden", metadata={"tool_name": name}, project_id="default")
            return self._result(message_id, {**self._tool_error("该 MCP 服务不支持此工具"), "_meta": {"trace_id": str(call_trace_id)}})
        call = FunctionToolCall(
            name=name,
            arguments=arguments,
            conversation_id=uuid4(),
            trace_id=call_trace_id,
        )
        started_at = perf_counter()
        try:
            result = self._executor.execute(call)
        except (PermissionError, ValueError, ValidationError) as error:
            self._record_tool_call(call, "invalid_arguments", None, started_at)
            return self._result(message_id, self._tool_error(str(error)))
        except KeyError as error:
            self._record_tool_call(call, "not_found", None, started_at)
            return self._result(message_id, self._tool_error(str(error)))
        self._record_tool_call(call, "completed", self._result_reference(result), started_at)
        return self._result(message_id, {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
            "isError": False,
            "_meta": {"conversation_id": str(call.conversation_id), "trace_id": str(call.trace_id)},
        })

    def _record_tool_call(
        self,
        call: FunctionToolCall,
        status: str,
        result_reference: str | None,
        started_at: float,
    ) -> None:
        duration_ms = round((perf_counter() - started_at) * 1000)
        if self._audit_tool_call is not None:
            self._audit_tool_call(
                conversation_id=str(call.conversation_id), trace_id=str(call.trace_id),
                tool_name=call.name.value, arguments=call.model_dump_json(), result_status=status,
                result_reference=result_reference, duration_ms=duration_ms, call_origin="mcp",
            )
        if self._audit_event is not None:
            self._audit_event(
                event_type="mcp_call", source="mcp", trace_id=str(call.trace_id),
                resource_id=result_reference, status=status,
                metadata={"tool_name": call.name.value, "duration_ms": duration_ms},
                project_id=self._project_id(call),
            )

    @staticmethod
    def _result_reference(result: object) -> str | None:
        if not isinstance(result, dict):
            return None
        for field_name in ("job_id", "review_id", "run_id", "document_id"):
            if result.get(field_name):
                return str(result[field_name])
        return None

    @staticmethod
    def _project_id(call: FunctionToolCall) -> str:
        """提取已校验工具调用的项目范围，缺省时使用默认项目。"""
        project_id = call.arguments.get("project_id", "default")
        return project_id.strip() if isinstance(project_id, str) and project_id.strip() else "default"

    @staticmethod
    def _tool_error(message: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": message}], "isError": True}

    @staticmethod
    def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    @staticmethod
    def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def run_stdio(server_factory: Callable[[], McpServer]) -> None:
    """通过标准输入输出提供按行分隔的 MCP JSON-RPC 消息服务。"""
    server = server_factory()
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC 消息必须是对象")
            response = server.handle(message)
        except (ValueError, json.JSONDecodeError) as error:
            response = McpServer._error(None, -32700, str(error))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    """使用本地应用共享的只读执行器运行服务。"""
    from app.main import app

    run_stdio(lambda: McpServer(
        app.state.function_tool_executor,
        audit_tool_call=app.state.agent_conversation_service.record_external_tool_call,
        audit_event=app.state.audit_ledger.event,
    ))


if __name__ == "__main__":
    main()
