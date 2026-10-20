from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger


class CacheRevocationRouteTests(unittest.TestCase):
    def test_cache_revocation_requires_confirmation(self):
        original_service = app.state.analysis_job_service
        original_ledger = app.state.audit_ledger
        try:
            class Service:
                def revoke_cache(self, job_id): self.job_id = job_id
                def get(self, job_id): raise AssertionError
            app.state.analysis_job_service = Service()
            with TemporaryDirectory() as temporary_directory:
                app.state.audit_ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                with TestClient(app) as client:
                    response = client.post(f"/api/v2/jobs/{uuid4()}/cache-revocation")
                self.assertEqual(response.json()["status"], "confirmation_required")
        finally:
            app.state.analysis_job_service = original_service
            app.state.audit_ledger = original_ledger


if __name__ == "__main__":
    unittest.main()
