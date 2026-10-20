"""Contract tests for the SQLAlchemy persistence adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.infrastructure.persistence.database import create_database
from app.infrastructure.persistence.repositories import (
    SqlAlchemyAnalysisBatchRepository,
    SqlAlchemyAnalysisJobRepository,
    SqlAlchemyAssetRepository,
)


class SqlAlchemyRepositoryTests(unittest.TestCase):
    def test_asset_and_job_records_survive_repository_instances(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "agent.sqlite3"
            database = create_database(f"sqlite:///{database_path.as_posix()}")
            assets = SqlAlchemyAssetRepository(database.session_factory)
            jobs = SqlAlchemyAnalysisJobRepository(database.session_factory)
            batches = SqlAlchemyAnalysisBatchRepository(database.session_factory)

            assets.create(
                asset_id="asset-001",
                file_path=Path("data/assets/asset-001.jpg"),
                content_type="image/jpeg",
                filename="plant.jpg",
            )
            created_at = datetime.now(timezone.utc)
            jobs.create(job_id="job-001", asset_id="asset-001", created_at=created_at)
            jobs.create(job_id="job-project", asset_id="asset-001", created_at=created_at, project_id="trial-a")
            updated = jobs.update("job-001", status="completed", result={"pod_count": 12})
            batches.create(batch_id="batch-001", job_ids=["job-001"], created_at=created_at)

            self.assertEqual(assets.get("asset-001")["filename"], "plant.jpg")
            self.assertEqual(updated["status"], "completed")
            self.assertEqual(jobs.get("job-001")["result"], {"pod_count": 12})
            self.assertEqual(batches.get("batch-001")["job_ids"], ["job-001"])
            self.assertEqual(jobs.get("job-project")["project_id"], "trial-a")
            self.assertEqual(len(batches.list_recent(limit=10)), 1)
            database.engine.dispose()

    def test_retry_parent_is_persisted_and_migrated(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "agent.sqlite3"
            database = create_database(f"sqlite:///{database_path.as_posix()}")
            assets = SqlAlchemyAssetRepository(database.session_factory)
            jobs = SqlAlchemyAnalysisJobRepository(database.session_factory)
            assets.create(asset_id="asset-002", file_path=Path("plant.jpg"), content_type="image/jpeg", filename="plant.jpg")
            created_at = datetime.now(timezone.utc)
            jobs.create(job_id="job-parent", asset_id="asset-002", created_at=created_at)
            jobs.create(job_id="job-retry", asset_id="asset-002", created_at=created_at, retry_of="job-parent")
            self.assertEqual(jobs.get("job-retry")["retry_of"], "job-parent")
            database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
