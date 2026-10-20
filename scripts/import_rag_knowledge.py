"""将本项目 Markdown 知识资料批量导入本地知识库。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import KNOWLEDGE_DB_PATH, RAG_RETRIEVAL_MODE
from app.rag.file_loader import load_knowledge_text
from app.rag.service import KnowledgeService

DEFAULT_KNOWLEDGE_DIRECTORY = PROJECT_ROOT / "docs" / "rag" / "knowledge"


def document_title(fallback_title: str, content: str) -> str:
    """优先使用 Markdown 一级标题，避免文件排序前缀进入引用标题。"""
    match = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    return match.group(1).strip() if match else fallback_title


def collect_documents(directory: Path) -> list[tuple[str, str, Path]]:
    """读取指定目录下可安全导入的 Markdown 文档。"""
    if not directory.is_dir():
        raise ValueError(f"知识资料目录不存在：{directory}")

    documents: list[tuple[str, str, Path]] = []
    for path in sorted(directory.glob("*.md")):
        fallback_title, content = load_knowledge_text(path.name, path.read_bytes())
        documents.append((document_title(fallback_title, content), content, path))
    if not documents:
        raise ValueError("知识资料目录中没有 Markdown 文件")
    return documents


def import_documents(directory: Path, database_path: Path, retrieval_mode: str) -> list[dict]:
    """执行批量导入并返回不含正文的结果摘要。"""
    service = KnowledgeService(database_path, retrieval_mode=retrieval_mode)
    results = []
    for title, content, path in collect_documents(directory):
        document_id, chunk_count = service.ingest(title=title, content=content)
        results.append(
            {
                "title": title,
                "document_id": document_id,
                "chunk_count": chunk_count,
                "source_filename": path.name,
            }
        )
    return results


def main() -> None:
    """要求显式确认后才对正式 SQLite 知识库写入资料。"""
    parser = argparse.ArgumentParser(description="批量导入项目 RAG Markdown 知识资料")
    parser.add_argument("--source", type=Path, default=DEFAULT_KNOWLEDGE_DIRECTORY)
    parser.add_argument("--database", type=Path, default=KNOWLEDGE_DB_PATH)
    parser.add_argument("--mode", choices=("keyword", "hybrid"), default=RAG_RETRIEVAL_MODE)
    parser.add_argument("--confirm", action="store_true", help="确认向指定知识库写入资料")
    arguments = parser.parse_args()

    documents = collect_documents(arguments.source)
    if not arguments.confirm:
        preview = {"status": "confirmation_required", "database": str(arguments.database), "document_count": len(documents), "titles": [title for title, _, _ in documents]}
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(f"导入汇总：状态=等待确认；资料数={len(documents)}；未写入知识库。下一步：核对标题后添加 --confirm。")
        return

    results = import_documents(arguments.source, arguments.database, arguments.mode)
    print(json.dumps({"status": "completed", "imported": results}, ensure_ascii=False, indent=2))
    print(f"导入汇总：状态=完成；已导入={len(results)} 篇；分块总数={sum(item['chunk_count'] for item in results)}；检索模式={arguments.mode}。")


if __name__ == "__main__":
    main()
