"""使用项目真实资料和人工题集评测本地 RAG 检索。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.service import KnowledgeService
from scripts.import_rag_knowledge import DEFAULT_KNOWLEDGE_DIRECTORY, collect_documents

DEFAULT_CASES_PATH = PROJECT_ROOT / "docs" / "rag" / "evaluation-cases.json"


def evaluate(mode: str, *, knowledge_directory: Path = DEFAULT_KNOWLEDGE_DIRECTORY, cases_path: Path = DEFAULT_CASES_PATH) -> dict:
    """在临时数据库中评测资料召回，不修改正式知识库。"""
    dataset = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    with TemporaryDirectory() as temporary_directory:
        service = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3", retrieval_mode=mode)
        for title, content, _ in collect_documents(knowledge_directory):
            service.ingest(title=title, content=content)

        outcomes = []
        answerable_cases = []
        for case in cases:
            titles = [hit.title for hit in service.search(case["question"], limit=5)]
            expected_title = case["expected_document_title"]
            answerable = case["expected_answerable"]
            outcome = {
                "id": case["id"],
                "question": case["question"],
                "expected_document_title": expected_title,
                "retrieved_titles": titles,
                "top_1": bool(expected_title and titles and titles[0] == expected_title),
                "top_3": bool(expected_title and expected_title in titles[:3]),
                "top_5": bool(expected_title and expected_title in titles[:5]),
                "out_of_scope_retrieved": not answerable and bool(titles),
            }
            outcomes.append(outcome)
            if answerable:
                answerable_cases.append(outcome)

    total = len(answerable_cases)
    return {
        "scope": dataset["scope"],
        "dataset_name": dataset["dataset_name"],
        "dataset_version": dataset["dataset_version"],
        "retrieval_mode": mode,
        "answerable_sample_count": total,
        "out_of_scope_sample_count": len(outcomes) - total,
        "top_1_recall": sum(item["top_1"] for item in answerable_cases) / total,
        "top_3_recall": sum(item["top_3"] for item in answerable_cases) / total,
        "top_5_recall": sum(item["top_5"] for item in answerable_cases) / total,
        "out_of_scope_retrieval_count": sum(item["out_of_scope_retrieved"] for item in outcomes),
        "cases": outcomes,
    }


if __name__ == "__main__":
    keyword_report = evaluate("keyword")
    hybrid_report = evaluate("hybrid")
    print(json.dumps(keyword_report, ensure_ascii=False, indent=2))
    print(json.dumps(hybrid_report, ensure_ascii=False, indent=2))
    print(
        "\n评测汇总："
        f"题集={keyword_report['dataset_name']}；"
        f"可回答题={keyword_report['answerable_sample_count']}；"
        f"无关题={keyword_report['out_of_scope_sample_count']}；"
        f"keyword Top-1/3/5={keyword_report['top_1_recall']:.1%}/"
        f"{keyword_report['top_3_recall']:.1%}/{keyword_report['top_5_recall']:.1%}；"
        f"hybrid Top-1/3/5={hybrid_report['top_1_recall']:.1%}/"
        f"{hybrid_report['top_3_recall']:.1%}/{hybrid_report['top_5_recall']:.1%}；"
        f"无关题误召回：keyword={keyword_report['out_of_scope_retrieval_count']}，"
        f"hybrid={hybrid_report['out_of_scope_retrieval_count']}。"
    )
    for label, report in (("keyword", keyword_report), ("hybrid", hybrid_report)):
        top_1_misses = [item["id"] for item in report["cases"] if item["expected_document_title"] and not item["top_1"]]
        out_of_scope_hits = [item["id"] for item in report["cases"] if item["out_of_scope_retrieved"]]
        print(f"{label} 待检查题：Top-1 未命中={','.join(top_1_misses) or '无'}；无关题误召回={','.join(out_of_scope_hits) or '无'}。")
    print("下一步：核对待检查题；若 Top-1 仍偏低，再补充同义词或细化知识文档，不需要重复导入。")
