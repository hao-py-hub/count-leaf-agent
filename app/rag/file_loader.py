"""Safe text-file loading for local knowledge ingestion."""

from __future__ import annotations

from pathlib import Path


SUPPORTED_KNOWLEDGE_EXTENSIONS = {".txt", ".md"}
MAX_KNOWLEDGE_FILE_BYTES = 2 * 1024 * 1024


def load_knowledge_text(filename: str, content: bytes) -> tuple[str, str]:
    """Validate and decode a teacher-uploaded UTF-8 text or Markdown file."""
    path = Path(filename or "")
    if path.suffix.lower() not in SUPPORTED_KNOWLEDGE_EXTENSIONS:
        raise ValueError("当前只支持 .txt 和 .md 知识资料文件")
    if not content:
        raise ValueError("知识资料文件不能为空")
    if len(content) > MAX_KNOWLEDGE_FILE_BYTES:
        raise ValueError("知识资料文件不能超过 2 MB")
    try:
        text = content.decode("utf-8-sig").strip()
    except UnicodeDecodeError as error:
        raise ValueError("知识资料文件必须使用 UTF-8 编码") from error
    if not text:
        raise ValueError("知识资料文件不能为空")
    return path.stem or "未命名资料", text
