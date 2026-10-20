"""Tests for local LLM settings; no external model request is made."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.agent.llm import LlmConfigurationError, LlmSettings


class LlmSettingsTests(unittest.TestCase):
    def test_disabled_llm_does_not_require_a_key(self) -> None:
        with patch.dict(os.environ, {"LLM_ENABLED": "false"}, clear=True):
            settings = LlmSettings.from_environment()
            settings.validate()

    def test_enabled_llm_requires_a_key(self) -> None:
        with patch.dict(os.environ, {"LLM_ENABLED": "true"}, clear=True):
            with self.assertRaises(LlmConfigurationError):
                LlmSettings.from_environment().validate()

    def test_enabled_deepseek_configuration_is_valid(self) -> None:
        environment = {
            "LLM_ENABLED": "true",
            "LLM_PROVIDER": "deepseek",
            "LLM_MODEL": "deepseek-chat",
            "LLM_API_BASE": "https://api.deepseek.com",
            "LLM_API_KEY": "test-key",
        }
        with patch.dict(os.environ, environment, clear=True):
            LlmSettings.from_environment().validate()


if __name__ == "__main__":
    unittest.main()
