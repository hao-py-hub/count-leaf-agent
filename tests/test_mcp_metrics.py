from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.audit_ledger import AuditLedger


class McpMetricsTests(unittest.TestCase):
    def test_mcp_forbidden_rate_is_aggregated(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = AuditLedger(Path(directory) / "ledger.sqlite3")
            ledger.event(event_type="mcp_call", source="mcp")
            ledger.event(event_type="mcp_forbidden", source="mcp")
            metrics = ledger.metrics()["mcp"]
            self.assertEqual(metrics["call_count"], 2)
            self.assertEqual(metrics["forbidden_count"], 1)
            self.assertEqual(metrics["forbidden_rate"], 0.5)
