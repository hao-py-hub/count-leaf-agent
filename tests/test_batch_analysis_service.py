import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from app.services.analysis_job_service import AnalysisJobService
from app.services.asset_store import AssetStore
from app.services.batch_analysis_service import BatchAnalysisService
from app.infrastructure.persistence.database import create_database
from app.infrastructure.persistence.repositories import (
    SqlAlchemyAnalysisBatchRepository,
    SqlAlchemyAnalysisJobRepository,
    SqlAlchemyAssetRepository,
)


class Runner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        return {"type": "result", "run_id": source_label}


class BlockingRunner:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        self.started.set()
        self.release.wait(timeout=2)
        return {"type": "result", "run_id": source_label}


class FailingRunner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        raise RuntimeError("temporary failure")


class BatchAnalysisServiceTests(unittest.TestCase):
    def test_batch_rejects_duplicate_assets_before_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plant.jpg"
            path.write_bytes(b"image")
            store = AssetStore()
            asset = store.register(path, "plant.jpg")
            service = BatchAnalysisService(AnalysisJobService(store, Runner()))
            with self.assertRaisesRegex(ValueError, "重复"):
                service.submit_confirmed([asset.asset_id, asset.asset_id])

    def test_batch_reports_child_job_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore()
            ids = []
            for name in ("a.jpg", "b.jpg"):
                path = Path(directory) / name
                path.write_bytes(b"image")
                ids.append(store.register(path, name, project_id="trial-a").asset_id)
            service = BatchAnalysisService(AnalysisJobService(store, Runner()))
            batch = service.submit_confirmed(ids, project_id="trial-a")
            self.assertEqual(batch.project_id, "trial-a")
            deadline = time.monotonic() + 2
            status = service.get_status(batch.batch_id)
            while status["status"] != "completed" and time.monotonic() < deadline:
                time.sleep(0.01)
                status = service.get_status(batch.batch_id)
            self.assertEqual(status["total"], 2)
            self.assertEqual(status["counts"]["completed"], 2)

    def test_review_required_child_is_terminal_but_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plant.jpg"
            path.write_bytes(b"image")
            store = AssetStore()
            asset = store.register(path, "plant.jpg")
            jobs = AnalysisJobService(store, Runner(), on_completed=lambda _job: True)
            service = BatchAnalysisService(jobs)
            batch = service.submit_confirmed([asset.asset_id])
            deadline = time.monotonic() + 2
            status = service.get_status(batch.batch_id)
            while status["status"] != "completed" and time.monotonic() < deadline:
                time.sleep(0.01)
                status = service.get_status(batch.batch_id)
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["counts"]["review_required"], 1)
            self.assertEqual(status["counts"]["completed"], 0)

    def test_batch_progress_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = create_database(f"sqlite:///{(Path(directory) / 'agent.sqlite3').as_posix()}")
            try:
                assets = SqlAlchemyAssetRepository(database.session_factory)
                jobs = SqlAlchemyAnalysisJobRepository(database.session_factory)
                batches = SqlAlchemyAnalysisBatchRepository(database.session_factory)
                path = Path(directory) / "plant.jpg"
                path.write_bytes(b"image")
                first_store = AssetStore(assets)
                asset = first_store.register(path, "plant.jpg")
                first_jobs = AnalysisJobService(first_store, Runner(), repository=jobs)
                first_batches = BatchAnalysisService(first_jobs, repository=batches)
                batch = first_batches.submit_confirmed([asset.asset_id])
                deadline = time.monotonic() + 2
                status = first_batches.get_status(batch.batch_id)
                while status["status"] != "completed" and time.monotonic() < deadline:
                    time.sleep(0.01)
                    status = first_batches.get_status(batch.batch_id)

                restarted_store = AssetStore(assets)
                restarted_jobs = AnalysisJobService(restarted_store, Runner(), repository=jobs)
                restarted_batches = BatchAnalysisService(restarted_jobs, repository=batches)
                restored = restarted_batches.get_status(batch.batch_id)
                self.assertEqual(restored["batch_id"], str(batch.batch_id))
                self.assertEqual(restored["counts"]["completed"], 1)
            finally:
                database.engine.dispose()

    def test_batch_cancel_only_cancels_queued_children(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore()
            asset_ids = []
            for name in ("first.jpg", "second.jpg"):
                path = Path(directory) / name
                path.write_bytes(b"image")
                asset_ids.append(store.register(path, name).asset_id)
            runner = BlockingRunner()
            jobs = AnalysisJobService(store, runner, max_workers=1)
            service = BatchAnalysisService(jobs)
            batch = service.submit_confirmed(asset_ids)
            self.assertTrue(runner.started.wait(timeout=1))

            cancelled = service.cancel_queued(batch.batch_id)
            self.assertEqual(cancelled["cancelled_count"], 1)
            self.assertEqual(cancelled["running_count"], 1)
            self.assertIn("仍在运行", cancelled["message"])
            self.assertEqual(cancelled["status"]["counts"]["cancelled"], 1)
            runner.release.set()

    def test_retry_failed_creates_new_batch_without_repeating_completed_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failed.jpg"
            path.write_bytes(b"image")
            store = AssetStore()
            asset = store.register(path, "failed.jpg")
            service = BatchAnalysisService(AnalysisJobService(store, FailingRunner()))
            original = service.submit_confirmed([asset.asset_id])
            deadline = time.monotonic() + 2
            while service.get_status(original.batch_id)["counts"]["failed"] != 1 and time.monotonic() < deadline:
                time.sleep(0.01)

            retry = service.retry_failed(original.batch_id)
            self.assertNotEqual(retry.batch_id, original.batch_id)
            self.assertEqual(len(retry.job_ids), 1)
            self.assertEqual(service.get_status(original.batch_id)["counts"]["failed"], 1)

    def test_list_recent_rejects_unbounded_batch_reads(self):
        service = BatchAnalysisService.__new__(BatchAnalysisService)
        service._repository = None
        service._batches = {}
        with self.assertRaisesRegex(ValueError, "1 和 100"):
            service.list_recent(limit=101)

    def test_batch_budget_rejection_creates_no_partial_child_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore()
            asset_ids = []
            for name in ("a.jpg", "b.jpg"):
                path = Path(directory) / name
                path.write_bytes(b"image")
                asset_ids.append(store.register(path, name).asset_id)
            jobs = AnalysisJobService(store, Runner(), daily_job_limit=1)
            service = BatchAnalysisService(jobs)
            with self.assertRaisesRegex(ValueError, "预算不足"):
                service.submit_confirmed(asset_ids)
            self.assertEqual(jobs.list_recent(), [])


if __name__ == "__main__":
    unittest.main()
