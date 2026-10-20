from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger


class FunctionAuditLedgerTests(unittest.TestCase):
    def test_function_call_is_written_to_unified_ledger(self) -> None:
        original = app.state.audit_ledger
        try:
            with TemporaryDirectory() as temporary_directory:
                ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                app.state.audit_ledger = ledger
                trace_id = uuid4()
                response = TestClient(app).post("/api/v2/agent/function-tools/execute", json={
                    "name": "search_history", "arguments": {"query": "private-query"}, "trace_id": str(trace_id),
                })
                self.assertEqual(response.status_code, 200)
                events = ledger.list_events(trace_id=str(trace_id))
                self.assertEqual(events[0]["source"], "function_call")
                self.assertNotIn("private-query", str(events[0]))
        finally:
            app.state.audit_ledger = original


if __name__ == "__main__":
    unittest.main()
