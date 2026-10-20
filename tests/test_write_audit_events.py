from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger


class WriteAuditEventsTests(unittest.TestCase):
    def test_confirmed_history_export_writes_audit_event(self) -> None:
        original = app.state.audit_ledger
        try:
            with TemporaryDirectory() as directory:
                ledger = AuditLedger(Path(directory) / "ledger.sqlite3"); app.state.audit_ledger = ledger
                trace_id = uuid4()
                with TestClient(app) as client:
                    response = client.post("/api/v2/exports/history", json={"confirm": True, "trace_id": str(trace_id)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(ledger.list_events(trace_id=str(trace_id))[0]["event_type"], "history_exported")
        finally:
            app.state.audit_ledger = original


if __name__ == "__main__":
    unittest.main()
