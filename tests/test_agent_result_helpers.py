"""State-boundary tests for completed Agent analysis results."""

from types import SimpleNamespace
import unittest

from app.api.v2.agent import _completed_result, _revision_diff


class AgentResultHelperTests(unittest.TestCase):
    def test_completed_job_returns_original_result(self) -> None:
        result = {"type": "result", "total_pods": 12}
        job = SimpleNamespace(status="completed", result=result)
        self.assertEqual(_completed_result(job), result)

    def test_unfinished_job_cannot_expose_result(self) -> None:
        with self.assertRaises(ValueError):
            _completed_result(SimpleNamespace(status="running", result=None))

    def test_revision_diff_reports_only_changed_numeric_fields(self) -> None:
        diff = _revision_diff({
            "revision": 1,
            "original_result": {"total_pods": 10, "branch_count": 2},
            "result": {"total_pods": 12, "branch_count": 2},
        })
        self.assertTrue(diff["changed"])
        self.assertEqual(diff["changes"]["total_pods"]["delta"], 2)
        self.assertNotIn("branch_count", diff["changes"])


if __name__ == "__main__":
    unittest.main()
