"""Grounding tests for Agent-generated phenotype reports."""

from __future__ import annotations

import unittest

from app.agent.reporting import PhenotypeReportWriter


class FakeClient:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_message = ""

    def complete(self, *, system_prompt: str, user_message: str) -> str:
        self.system_prompt = system_prompt
        self.user_message = user_message
        return "报告"


class PhenotypeReportWriterTests(unittest.TestCase):
    def test_result_is_the_only_fact_source_for_model_prompt(self) -> None:
        client = FakeClient()
        report = PhenotypeReportWriter(client).write(
            analysis_result={"pod_count": 12, "confidence": 0.91},
            user_question="结果怎么样？",
        )

        self.assertEqual(report, "报告")
        self.assertIn('"pod_count": 12', client.user_message)
        self.assertIn("Do not invent", client.system_prompt)

    def test_provenance_is_kept_in_evidence(self) -> None:
        client = FakeClient()
        PhenotypeReportWriter(client).write(
            analysis_result={"total_pods": 12, "provenance": {"algorithm_version": "stalk-v0.1"}},
        )
        self.assertIn('"algorithm_version": "stalk-v0.1"', client.user_message)


if __name__ == "__main__":
    unittest.main()
