"""Grounded natural-language explanations for completed phenotype analysis."""

from __future__ import annotations

import json
from typing import Any, Protocol


class ChatCompletionClient(Protocol):
    """Minimal interface implemented by approved LLM providers."""

    def complete(self, *, system_prompt: str, user_message: str) -> str:
        """Return a single model completion."""


class PhenotypeReportWriter:
    """Writes reports from pipeline evidence without changing algorithm outputs."""

    _SYSTEM_PROMPT = """You are an agricultural phenotype-analysis assistant.
Use only the supplied analysis evidence. Do not invent measurements, counts,
confidence values, or causal conclusions. Clearly state when evidence is
missing. Respond in concise Chinese and preserve all numeric values exactly."""

    def __init__(self, client: ChatCompletionClient) -> None:
        self._client = client

    def write(self, *, analysis_result: dict[str, Any], user_question: str | None = None) -> str:
        """Generate an evidence-grounded interpretation of a completed job."""
        evidence = json.dumps(analysis_result, ensure_ascii=False, sort_keys=True, default=str)
        question = user_question or "请根据本次分析结果生成简明报告。"
        return self._client.complete(
            system_prompt=self._SYSTEM_PROMPT,
            user_message=f"用户问题：{question}\n\n分析证据（唯一事实来源）：\n{evidence}",
        )
