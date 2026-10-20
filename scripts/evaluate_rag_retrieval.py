"""Offline retrieval baseline for local RAG modes without network or LLM calls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.service import KnowledgeService


@dataclass(frozen=True)
class RetrievalGoldenCase:
    query: str
    expected_title: str


_DOCUMENTS = (
    ("角果采样规范", "角果数量应在油菜成熟期采集并记录。"),
    ("主枝表型说明", "主分枝的角果数量可用于植株表型分析。"),
    ("株高测量规范", "植株高度应使用标尺进行测量。"),
)

_CASES = (
    RetrievalGoldenCase("荚果数量", "角果采样规范"),
    RetrievalGoldenCase("主枝角果", "主枝表型说明"),
    RetrievalGoldenCase("植株高度", "株高测量规范"),
)


def evaluate(mode: str = "keyword") -> dict:
    """Measure title-hit recall at fixed ranks for the bundled local cases."""
    with TemporaryDirectory() as temporary_directory:
        service = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3", retrieval_mode=mode)
        for title, content in _DOCUMENTS:
            service.ingest(title=title, content=content)

        outcomes = []
        for case in _CASES:
            titles = [hit.title for hit in service.search(case.query, limit=5)]
            outcomes.append(
                {
                    "query": case.query,
                    "expected_title": case.expected_title,
                    "retrieved_titles": titles,
                    "top_1": bool(titles and titles[0] == case.expected_title),
                    "top_3": case.expected_title in titles[:3],
                    "top_5": case.expected_title in titles[:5],
                }
            )

    total = len(outcomes)
    return {
        "scope": "offline local-rag golden cases",
        "retrieval_mode": mode,
        "sample_count": total,
        "top_1_recall": sum(item["top_1"] for item in outcomes) / total,
        "top_3_recall": sum(item["top_3"] for item in outcomes) / total,
        "top_5_recall": sum(item["top_5"] for item in outcomes) / total,
        "cases": outcomes,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate("keyword"), ensure_ascii=False, indent=2))
    print(json.dumps(evaluate("hybrid"), ensure_ascii=False, indent=2))
