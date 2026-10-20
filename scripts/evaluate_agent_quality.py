"""Offline golden-case evaluation for the bounded phenotyping Agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.models import AgentChatRequest, AgentResponseStatus, ToolName
from app.agent.workflow import AgentWorkflow


@dataclass(frozen=True)
class GoldenCase:
    name: str
    request: AgentChatRequest
    expected_status: AgentResponseStatus
    expected_tool: ToolName | None
    requires_citations: bool = False


def evaluate() -> dict:
    """Evaluate routing, tool selection, and citation coverage without network access."""
    workflow = AgentWorkflow(
        submit_analysis=lambda asset_id: uuid4(),
        answer_knowledge=lambda message: ("资料支持的回答", ["golden-doc:chunk-1"]),
        check_quality=lambda asset_id: {"recommended_to_retake": False, "issues": []},
        search_history=lambda query: {"count": 2, "items": []},
        summarize_history=lambda query: {"sample_count": 2, "completed_count": 2, "failed_count": 0},
        create_review=lambda job_id, reason: uuid4(),
        generate_report=lambda job_id, question: "基于已完成算法结果的报告。",
    )
    cases = (
        GoldenCase(
            "phenotyping_requires_asset",
            AgentChatRequest(message="请分析这张油菜图片"),
            AgentResponseStatus.NEEDS_ASSET,
            ToolName.SUBMIT_ANALYSIS,
        ),
        GoldenCase(
            "phenotyping_submits_authorized_asset",
            AgentChatRequest(message="分析角果", asset_id=uuid4(), confirm_submission=True),
            AgentResponseStatus.SUBMITTED,
            ToolName.SUBMIT_ANALYSIS,
        ),
        GoldenCase(
            "knowledge_answer_has_citations",
            AgentChatRequest(message="油菜角果数量有什么用？"),
            AgentResponseStatus.ANSWERED,
            ToolName.RETRIEVE_KNOWLEDGE,
            requires_citations=True,
        ),
        GoldenCase(
            "quality_check_uses_asset_tool",
            AgentChatRequest(message="检查图片质量", asset_id=uuid4()),
            AgentResponseStatus.QUALITY_REPORTED,
            ToolName.QUALITY_CHECK,
        ),
        GoldenCase(
            "history_query_uses_read_only_tool",
            AgentChatRequest(message="查询历史记录", history_query="N0"),
            AgentResponseStatus.DATA_RETRIEVED,
            ToolName.SEARCH_HISTORY,
        ),
        GoldenCase(
            "statistics_uses_aggregate_tool",
            AgentChatRequest(message="统计历史记录"),
            AgentResponseStatus.DATA_SUMMARIZED,
            ToolName.AGGREGATE_METRICS,
        ),
        GoldenCase(
            "review_creates_explicit_task",
            AgentChatRequest(message="请人工复核", analysis_job_id=uuid4(), confirm_review=True),
            AgentResponseStatus.REVIEW_CREATED,
            ToolName.CREATE_REVIEW,
        ),
        GoldenCase(
            "report_uses_completed_job_reference",
            AgentChatRequest(message="生成报告", analysis_job_id=uuid4(), confirm_report=True),
            AgentResponseStatus.REPORT_GENERATED,
            ToolName.CREATE_REPORT,
        ),
        GoldenCase(
            "unsupported_request_calls_no_tool",
            AgentChatRequest(message="你好"),
            AgentResponseStatus.UNSUPPORTED,
            None,
        ),
    )

    outcomes = []
    for case in cases:
        response = workflow.invoke(case.request)
        status_ok = response.status is case.expected_status
        tool_ok = response.requested_tool is case.expected_tool
        citations_ok = not case.requires_citations or bool(response.citations)
        outcomes.append(
            {
                "name": case.name,
                "passed": status_ok and tool_ok and citations_ok,
                "expected_status": case.expected_status.value,
                "actual_status": response.status.value,
                "expected_tool": case.expected_tool.value if case.expected_tool else None,
                "actual_tool": response.requested_tool.value if response.requested_tool else None,
                "citation_count": len(response.citations),
            }
        )

    total = len(outcomes)
    citation_required = [item for item, case in zip(outcomes, cases, strict=True) if case.requires_citations]
    return {
        "scope": "offline bounded-agent golden cases",
        "sample_count": total,
        "routing_accuracy": sum(item["expected_status"] == item["actual_status"] for item in outcomes) / total,
        "tool_selection_accuracy": sum(item["expected_tool"] == item["actual_tool"] for item in outcomes) / total,
        "citation_required_case_count": len(citation_required),
        "citation_coverage": (
            sum(item["citation_count"] > 0 for item in citation_required) / len(citation_required)
            if citation_required else 1.0
        ),
        "passed": all(item["passed"] for item in outcomes),
        "cases": outcomes,
    }


if __name__ == "__main__":
    report = evaluate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    passed_count = sum(case["passed"] for case in report["cases"])
    print(
        f"执行结果汇总：总数={report['sample_count']}，通过={passed_count}，"
        f"失败={report['sample_count'] - passed_count}；下一步：检查失败样例后再更新黄金集。"
    )
