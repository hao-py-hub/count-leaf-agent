"""Regression test for the offline Agent golden-case quality baseline."""

import unittest

from fastapi.testclient import TestClient

from app.main import app
from scripts.evaluate_agent_quality import evaluate


class AgentQualityEvaluationTests(unittest.TestCase):
    def test_all_golden_cases_pass(self) -> None:
        report = evaluate()
        self.assertTrue(report["passed"])
        self.assertEqual(report["sample_count"], 9)
        self.assertEqual(report["routing_accuracy"], 1.0)
        self.assertEqual(report["tool_selection_accuracy"], 1.0)
        self.assertEqual(report["citation_required_case_count"], 1)
        self.assertEqual(report["citation_coverage"], 1.0)

    def test_operator_endpoint_reports_scope_and_sample_count(self) -> None:
        response = TestClient(app).get("/api/v2/quality/agent-evaluation")
        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["scope"], "offline bounded-agent golden cases")
        self.assertEqual(payload["sample_count"], 9)
        self.assertIn("tool_selection_accuracy", payload)


if __name__ == "__main__":
    unittest.main()
