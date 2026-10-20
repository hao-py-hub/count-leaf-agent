"""Controlled OpenAI-compatible language-model integration for agents."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import httpx


class LlmConfigurationError(RuntimeError):
    """Raised when a requested language-model integration is incomplete."""


@dataclass(frozen=True)
class LlmSettings:
    """Runtime settings for the enterprise Agent language model."""

    enabled: bool
    provider: str
    model: str
    api_base: str
    api_key: str | None
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "LlmSettings":
        return cls(
            enabled=os.getenv("LLM_ENABLED", "false").strip().lower() == "true",
            provider=os.getenv("LLM_PROVIDER", "deepseek").strip().lower(),
            model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
            api_base=os.getenv("LLM_API_BASE", "https://api.deepseek.com").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY") or None,
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if self.provider != "deepseek":
            raise LlmConfigurationError(f"Unsupported LLM_PROVIDER: {self.provider}")
        if not self.model or not self.api_base or not self.api_key:
            raise LlmConfigurationError("LLM_MODEL, LLM_API_BASE, and LLM_API_KEY are required when LLM_ENABLED=true.")


class DeepSeekChatClient:
    """Small, explicit client for the DeepSeek OpenAI-compatible chat API."""

    def __init__(self, settings: LlmSettings, client: httpx.Client | None = None) -> None:
        settings.validate()
        self._settings = settings
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)

    def complete(self, *, system_prompt: str, user_message: str) -> str:
        """Return one text completion from an approved system/user prompt pair."""
        if not self._settings.enabled:
            raise LlmConfigurationError("LLM is disabled. Set LLM_ENABLED=true to permit model requests.")

        response = self._client.post(
            f"{self._settings.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self._settings.api_key}"},
            json={
                "model": self._settings.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        try:
            return str(payload["choices"][0]["message"]["content"]).strip()
        except (IndexError, KeyError, TypeError) as error:
            raise RuntimeError("DeepSeek returned an invalid chat-completion response.") from error
