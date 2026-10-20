from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from uuid import uuid4

from app.services.analysis_job_service import AnalysisJobService
from app.services.asset_store import AssetStore
from app.services.audit_ledger import AuditLedger


class Runner:
    def run(self, image_path, source_label, source_kind):
        return {"type": "result", "run_id": "cache-run"}


class CacheRevocationTests(unittest.TestCase):
    def test_revoked_completed_result_is_not_reused(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = root / "plant.jpg"; path.write_bytes(b"image")
            assets = AssetStore(); asset = assets.register(path, "plant.jpg")
            ledger = AuditLedger(root / "ledger.sqlite3")
            service = AnalysisJobService(assets, Runner(), audit_event=ledger.event, cache_revoked=ledger.is_cache_revoked)
            first = service.submit(asset.asset_id, trace_id=str(uuid4()))
            deadline = time.monotonic() + 2
            while service.get(first).status in {"queued", "running"} and time.monotonic() < deadline: time.sleep(0.01)
            service.revoke_cache(first)
            second = service.submit(asset.asset_id)
            while service.get(second).status in {"queued", "running"} and time.monotonic() < deadline: time.sleep(0.01)
            self.assertNotEqual(first, second)
            self.assertFalse(service.get(second).cache_hit)
            self.assertTrue(any(item["event_type"] == "cache_revoked" for item in ledger.list_events()))

    def test_failed_retry_preserves_parent_reference(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = root / "plant.jpg"; path.write_bytes(b"image")
            assets = AssetStore(); asset = assets.register(path, "plant.jpg")
            service = AnalysisJobService(assets, Runner())
            job = service.submit(asset.asset_id)
            service._jobs[job].status = "failed"
            retry = service.retry_failed(job)
            self.assertEqual(service.get(retry).retry_of, str(job))


if __name__ == "__main__":
    unittest.main()
