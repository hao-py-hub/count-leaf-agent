import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from uuid import uuid4

from app.services.analysis_job_service import AnalysisJobService
from app.services.policy_engine import PhenotypingPolicy, PolicyEngine
from app.services.error_messages import ANALYSIS_FAILURE_MESSAGE, ANALYSIS_TIMEOUT_MESSAGE
from app.services.asset_store import AssetStore
from app.infrastructure.persistence.database import create_database
from app.infrastructure.persistence.repositories import (
    SqlAlchemyAnalysisJobRepository,
    SqlAlchemyAssetRepository,
)


class FakeAnalysisRunner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        return {"type": "result", "run_id": "fake-run"}


class FailingAnalysisRunner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        raise RuntimeError("runner unavailable")


class BlockingAnalysisRunner:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.finished = Event()

    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        self.started.set()
        self.release.wait(timeout=2)
        self.finished.set()
        return {"type": "result", "run_id": source_label}


class RecordingDispatcher:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    def dispatch(self, job_id: str) -> None:
        self.job_ids.append(job_id)


class AnalysisJobServiceTests(unittest.TestCase):
    def test_trace_id_is_persisted_in_job_and_result_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            store = AssetStore()
            asset = store.register(image_path, "plant.jpg")
            service = AnalysisJobService(store, FakeAnalysisRunner())
            trace_id = str(uuid4())
            job_id = service.submit(asset.asset_id, trace_id=trace_id)
            deadline = time.monotonic() + 2
            while service.get(job_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
            job = service.get(job_id)
            self.assertEqual(job.trace_id, trace_id)
            self.assertEqual(job.result["provenance"]["trace_id"], trace_id)
    def test_cache_key_changes_with_algorithm_model_or_prompt_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"same-image")
            asset = AssetStore().register(image_path, "plant.jpg")
            base_context = {"algorithm_version": "stalk-v1", "model_version": "model-a", "prompt_version": "prompt-a"}
            base_service = AnalysisJobService(AssetStore(), FakeAnalysisRunner(), cache_context=base_context)
            base_key = base_service._cache_key(asset.asset_id, image_path)
            for changed_context in (
                {**base_context, "algorithm_version": "stalk-v2"},
                {**base_context, "model_version": "model-b"},
                {**base_context, "prompt_version": "prompt-b"},
            ):
                changed_service = AnalysisJobService(
                    AssetStore(), FakeAnalysisRunner(), cache_context=changed_context
                )
                self.assertNotEqual(base_key, changed_service._cache_key(asset.asset_id, image_path))

    def test_duplicate_submission_reuses_completed_result_without_running_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"same-image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            runner = FakeAnalysisRunner()
            job_service = AnalysisJobService(asset_store, runner)

            first_id = job_service.submit(asset.asset_id)
            deadline = time.monotonic() + 2
            while job_service.get(first_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
            second_id = job_service.submit(asset.asset_id)

            self.assertNotEqual(first_id, second_id)
            cached = job_service.get(second_id)
            self.assertEqual(cached.status, "completed")
            self.assertTrue(cached.cache_hit)
            self.assertEqual(cached.run_id, "fake-run")

    def test_duplicate_submission_while_running_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"same-image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            runner = BlockingAnalysisRunner()
            job_service = AnalysisJobService(asset_store, runner)

            first_id = job_service.submit(asset.asset_id)
            self.assertTrue(runner.started.wait(timeout=1))
            second_id = job_service.submit(asset.asset_id)
            self.assertEqual(first_id, second_id)
            runner.release.set()

    def test_asset_cannot_be_submitted_across_projects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            store = AssetStore()
            asset = store.register(image_path, "plant.jpg", project_id="trial-a")
            job_service = AnalysisJobService(store, FakeAnalysisRunner())
            first = job_service.submit(asset.asset_id, project_id="trial-a")
            self.assertEqual(job_service.get(first).project_id, "trial-a")
            with self.assertRaisesRegex(KeyError, "图片资源不存在"):
                job_service.submit(asset.asset_id, project_id="trial-b")
    def test_submitted_job_completes_with_runner_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            job_service = AnalysisJobService(asset_store, FakeAnalysisRunner())

            job_id = job_service.submit(asset.asset_id)
            deadline = time.monotonic() + 2
            job = job_service.get(job_id)
            while job.status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = job_service.get(job_id)

            self.assertEqual(job.status, "completed")
            self.assertEqual(job.run_id, "fake-run")

    def test_completed_job_invokes_optional_review_hook(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            completed = Event()
            received_job_ids = []

            def on_completed(job):
                received_job_ids.append(job.job_id)
                completed.set()

            job_service = AnalysisJobService(
                asset_store, FakeAnalysisRunner(), on_completed=on_completed
            )
            job_id = job_service.submit(asset.asset_id)

            self.assertTrue(completed.wait(timeout=2))
            self.assertEqual(received_job_ids, [job_id])
            self.assertEqual(job_service.get(job_id).status, "completed")

    def test_review_hook_marks_result_review_required_and_excludes_it_from_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            review_created = Event()

            def on_completed(_job):
                review_created.set()
                return True

            job_service = AnalysisJobService(asset_store, FakeAnalysisRunner(), on_completed=on_completed)
            first_id = job_service.submit(asset.asset_id)
            self.assertTrue(review_created.wait(timeout=2))
            self.assertEqual(job_service.get(first_id).status, "review_required")

            second_id = job_service.submit(asset.asset_id)
            self.assertNotEqual(first_id, second_id)
            deadline = time.monotonic() + 2
            while job_service.get(second_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(job_service.get(second_id).cache_hit)

    def test_runner_exception_marks_job_as_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            job_service = AnalysisJobService(asset_store, FailingAnalysisRunner())

            job_id = job_service.submit(asset.asset_id)
            deadline = time.monotonic() + 2
            job = job_service.get(job_id)
            while job.status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = job_service.get(job_id)

            self.assertEqual(job.status, "failed")
            self.assertEqual(job.error_message, ANALYSIS_FAILURE_MESSAGE)

    def test_running_job_times_out_and_late_result_cannot_overwrite_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            runner = BlockingAnalysisRunner()
            job_service = AnalysisJobService(
                asset_store, runner, max_workers=1, timeout_seconds=1
            )

            job_id = job_service.submit(asset.asset_id)
            self.assertTrue(runner.started.wait(timeout=1))
            deadline = time.monotonic() + 2
            while job_service.get(job_id).status != "failed" and time.monotonic() < deadline:
                time.sleep(0.01)

            timed_out_job = job_service.get(job_id)
            self.assertEqual(timed_out_job.status, "failed")
            self.assertEqual(timed_out_job.error_message, ANALYSIS_TIMEOUT_MESSAGE)
            self.assertIsNone(timed_out_job.run_id)
            runner.release.set()
            self.assertTrue(runner.finished.wait(timeout=1))

            late_job = job_service.get(job_id)
            self.assertEqual(late_job.status, "failed")
            self.assertEqual(late_job.error_message, ANALYSIS_TIMEOUT_MESSAGE)
            self.assertIsNone(late_job.run_id)

    def test_timeout_configuration_rejects_non_positive_value(self):
        with self.assertRaisesRegex(ValueError, "超时秒数"):
            AnalysisJobService(AssetStore(), FakeAnalysisRunner(), timeout_seconds=0)

    def test_asset_and_completed_job_are_available_after_service_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            database = create_database(f"sqlite:///{(Path(temp_dir) / 'agent.sqlite3').as_posix()}")
            assets = SqlAlchemyAssetRepository(database.session_factory)
            jobs = SqlAlchemyAnalysisJobRepository(database.session_factory)
            try:
                first_store = AssetStore(assets)
                asset = first_store.register(image_path, "plant.jpg")
                first_service = AnalysisJobService(first_store, FakeAnalysisRunner(), repository=jobs)

                job_id = first_service.submit(asset.asset_id)
                deadline = time.monotonic() + 2
                job = first_service.get(job_id)
                while job.status in {"queued", "running"} and time.monotonic() < deadline:
                    time.sleep(0.01)
                    job = first_service.get(job_id)

                restarted_store = AssetStore(assets)
                restarted_service = AnalysisJobService(restarted_store, FakeAnalysisRunner(), repository=jobs)
                self.assertEqual(restarted_store.get(asset.asset_id).file_path, image_path)
                self.assertEqual(restarted_service.get(job_id).status, "completed")
            finally:
                database.engine.dispose()

    def test_review_required_job_is_available_after_service_restart(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = create_database(f"sqlite:///{(Path(temporary_directory) / 'agent.sqlite3').as_posix()}")
            try:
                assets = SqlAlchemyAssetRepository(database.session_factory)
                jobs = SqlAlchemyAnalysisJobRepository(database.session_factory)
                image_path = Path(temporary_directory) / "plant.jpg"
                image_path.write_bytes(b"image")
                store = AssetStore(assets)
                asset = store.register(image_path, "plant.jpg")
                service = AnalysisJobService(store, FakeAnalysisRunner(), repository=jobs, on_completed=lambda _job: True)
                job_id = service.submit(asset.asset_id)
                deadline = time.monotonic() + 2
                while service.get(job_id).status != "review_required" and time.monotonic() < deadline:
                    time.sleep(0.01)

                self.assertEqual(service.get(job_id).status, "review_required")
                restarted = AnalysisJobService(AssetStore(assets), FakeAnalysisRunner(), repository=jobs)
                self.assertEqual(restarted.get(job_id).status, "review_required")
            finally:
                database.engine.dispose()

    def test_list_recent_hides_analysis_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            job_service = AnalysisJobService(asset_store, FakeAnalysisRunner())

            job_id = job_service.submit(asset.asset_id)
            deadline = time.monotonic() + 2
            while job_service.get(job_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)

            jobs = job_service.list_recent()
            self.assertEqual(jobs[0].job_id, job_id)
            self.assertIsNone(jobs[0].result)

    def test_retry_failed_creates_a_new_job_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_store = AssetStore()
            asset = asset_store.register(image_path, "plant.jpg")
            job_service = AnalysisJobService(asset_store, FailingAnalysisRunner())
            original_id = job_service.submit(asset.asset_id)
            deadline = time.monotonic() + 2
            while job_service.get(original_id).status != "failed" and time.monotonic() < deadline:
                time.sleep(0.01)

            retry_id = job_service.retry_failed(original_id)
            self.assertNotEqual(retry_id, original_id)
            self.assertEqual(job_service.get(original_id).status, "failed")

    def test_cancel_only_stops_a_queued_task(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AssetStore()
            asset_ids = []
            for name in ("first.jpg", "second.jpg"):
                path = Path(temp_dir) / name
                path.write_bytes(b"image")
                asset_ids.append(store.register(path, name).asset_id)
            runner = BlockingAnalysisRunner()
            job_service = AnalysisJobService(store, runner, max_workers=1)
            running_id = job_service.submit(asset_ids[0])
            self.assertTrue(runner.started.wait(timeout=1))
            queued_id = job_service.submit(asset_ids[1])

            cancelled = job_service.cancel_queued(queued_id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(job_service.get(running_id).status, "running")
            runner.release.set()

    def test_daily_budget_rejects_second_submission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            store = AssetStore()
            asset = store.register(image_path, "plant.jpg")
            job_service = AnalysisJobService(store, FakeAnalysisRunner(), daily_job_limit=1)
            job_service.submit(asset.asset_id)
            with self.assertRaisesRegex(ValueError, "预算不足"):
                job_service.submit(asset.asset_id)

    def test_budget_status_reports_used_and_remaining_capacity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            store = AssetStore()
            asset = store.register(image_path, "plant.jpg")
            job_service = AnalysisJobService(store, FakeAnalysisRunner(), daily_job_limit=2)
            self.assertEqual(job_service.budget_status(), {"used": 0, "limit": 2, "remaining": 2})
            job_service.submit(asset.asset_id)
            self.assertEqual(job_service.budget_status()["used"], 1)
        self.assertEqual(job_service.budget_status()["remaining"], 1)

    def test_policy_version_is_part_of_cache_context_and_budget_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            store = AssetStore()
            store.register(image_path, "plant.jpg")
            policy = PolicyEngine(PhenotypingPolicy(policy_version="project-v2", max_daily_analysis_jobs=2))
            job_service = AnalysisJobService(store, FakeAnalysisRunner(), daily_job_limit=2, policy_engine=policy)
            self.assertEqual(job_service.policy_status()["policy_version"], "project-v2")
            self.assertEqual(job_service.policy_status()["limit"], 2)
            self.assertEqual(job_service._cache_context["policy_version"], "project-v2")

    def test_durable_dispatcher_queues_job_without_running_it_in_api_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "plant.jpg"
            image_path.write_bytes(b"image")
            database = create_database(f"sqlite:///{(Path(temp_dir) / 'agent.sqlite3').as_posix()}")
            try:
                assets = SqlAlchemyAssetRepository(database.session_factory)
                jobs = SqlAlchemyAnalysisJobRepository(database.session_factory)
                store = AssetStore(assets)
                asset = store.register(image_path, "plant.jpg")
                dispatcher = RecordingDispatcher()
                service = AnalysisJobService(
                    store, FakeAnalysisRunner(), repository=jobs, dispatcher=dispatcher
                )

                job_id = service.submit(asset.asset_id)

                self.assertEqual(dispatcher.job_ids, [str(job_id)])
                self.assertEqual(service.get(job_id).status, "queued")
                service.execute_persisted(job_id)
                self.assertEqual(service.get(job_id).status, "completed")
                service.execute_persisted(job_id)
                self.assertEqual(service.get(job_id).status, "completed")
            finally:
                database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
