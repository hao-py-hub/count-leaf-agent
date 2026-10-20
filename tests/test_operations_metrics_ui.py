import unittest
from pathlib import Path


class OperationsMetricsUiTests(unittest.TestCase):
    def test_task_center_exposes_operations_metrics_without_accuracy_claim(self) -> None:
        page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="operationsMetricsBtn"', page)
        self.assertIn("/api/v2/agent/metrics?project_id=${projectId}", page)
        self.assertIn("不代表图像计数准确率", page)

    def test_task_center_exposes_project_scoped_domain_event_reconciliation(self) -> None:
        page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="domainEventsBtn"', page)
        self.assertIn("/api/v2/agent/domain-events?limit=20&project_id=${projectId}", page)
        self.assertIn("/api/v2/agent/domain-events/reconciliation?project_id=${projectId}", page)

    def test_task_center_exposes_project_scoped_domain_samples(self) -> None:
        page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="domainSamplesBtn"', page)
        self.assertIn("/api/v2/agent/domain-samples?limit=20&project_id=${projectId}", page)


if __name__ == "__main__":
    unittest.main()
