from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger


class MetricsRouteTests(unittest.TestCase):
    def test_metrics_route_returns_ledger_aggregation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            original = app.state.audit_ledger
            try:
                ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                ledger.event(event_type="analysis_cache_hit", source="job_service", project_id="metrics-project")
                app.state.audit_ledger = ledger
                with TestClient(app) as client:
                    response = client.get("/api/v2/agent/metrics?project_id=metrics-project")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("event_reconciliation", response.json())
                    self.assertEqual(response.json()["project_id"], "metrics-project")
                self.assertEqual(response.json()["cache"]["hit_count"], 1)
            finally:
                app.state.audit_ledger = original


if __name__ == "__main__":
    unittest.main()
