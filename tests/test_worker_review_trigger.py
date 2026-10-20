from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.infrastructure.persistence.database import create_database
from app.infrastructure.persistence.repositories import SqlAlchemyAnalysisJobRepository, SqlAlchemyAssetRepository
from app.infrastructure.tasks import phenotype_tasks
from app.services.review_service import ReviewService
from app.infrastructure.persistence.models import PlantSampleModel, ResultVersionModel
from sqlalchemy import select


class WorkerReviewReasonContractTests(unittest.TestCase):
    def test_worker_reason_codes_are_allowed_by_review_service(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            record = service.create(__import__("uuid").uuid4(), "质量和模型冲突", reason_code="image_quality,model_conflict")
            self.assertEqual(record["reason_code"], "image_quality,model_conflict")

    def test_celery_worker_writes_model_and_completion_audit_events(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = create_database(f"sqlite:///{(root / 'agent.sqlite3').as_posix()}")
            assets = SqlAlchemyAssetRepository(database.session_factory)
            jobs = SqlAlchemyAnalysisJobRepository(database.session_factory)
            image_path = root / "plant.jpg"
            image_path.write_bytes(b"image")
            asset_id = str(uuid4())
            job_id = str(uuid4())
            assets.create(asset_id=asset_id, file_path=image_path, content_type="image/jpeg", filename="plant.jpg")
            jobs.create(job_id=job_id, asset_id=asset_id, created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), trace_id="trace-worker")

            class Runner:
                def __init__(self, _history_store):
                    pass

                def run(self, *_args):
                    return {"type": "result", "run_id": "run-worker", "model_usage": {"input_tokens": 3, "output_tokens": 2}}

            dependencies = SimpleNamespace(database=database, assets=assets, analysis_jobs=jobs)
            with patch.object(phenotype_tasks, "build_enterprise_dependencies", return_value=dependencies), \
                 patch.object(phenotype_tasks, "PhenotypeAnalysisService", Runner), \
                 patch.object(phenotype_tasks, "AUDIT_LEDGER_DB_PATH", root / "ledger.sqlite3"), \
                 patch.object(phenotype_tasks.ImageQualityService, "inspect", return_value={"recommended_to_retake": False, "issues": []}):
                result = phenotype_tasks.execute_analysis_job.run(job_id)

            from app.services.audit_ledger import AuditLedger
            ledger = AuditLedger(root / "ledger.sqlite3")
            self.assertEqual(result["status"], "completed")
            self.assertEqual({event["event_type"] for event in ledger.list_events(trace_id="trace-worker")}, {"analysis_completed"})
            self.assertEqual(ledger.list_model_calls(trace_id="trace-worker")[0]["input_tokens"], 3)
            with database.session_factory() as session:
                self.assertEqual(len(session.scalars(select(PlantSampleModel)).all()), 1)
                self.assertEqual(session.scalar(select(ResultVersionModel)).source, "automatic")
            database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
