from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger


class MetricsWindowRouteTests(unittest.TestCase):
    def test_rejects_invalid_time_window(self) -> None:
        original = app.state.audit_ledger
        try:
            with TemporaryDirectory() as temporary_directory:
                app.state.audit_ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                with TestClient(app) as client:
                    response = client.get("/api/v2/agent/metrics?since=2026-01-02T00:00:00Z&until=2026-01-01T00:00:00Z")
                self.assertEqual(response.status_code, 400)
                self.assertIn("since", response.json()["detail"])
        finally:
            app.state.audit_ledger = original

    def test_accepts_valid_time_window(self) -> None:
        original = app.state.audit_ledger
        try:
            with TemporaryDirectory() as temporary_directory:
                app.state.audit_ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                with TestClient(app) as client:
                    response = client.get("/api/v2/agent/metrics?since=2020-01-01T00:00:00Z&until=2030-01-01T00:00:00Z")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["since"], "2020-01-01T00:00:00+00:00")
        finally:
            app.state.audit_ledger = original

    def test_metrics_response_exposes_typed_openapi_schema(self) -> None:
        with TestClient(app) as client:
            schema = client.get("/openapi.json").json()
        operation = schema["paths"]["/api/v2/agent/metrics"]["get"]
        self.assertEqual(operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/AgentMetricsResponse")


if __name__ == "__main__":
    unittest.main()
