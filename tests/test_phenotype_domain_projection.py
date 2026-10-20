from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from sqlalchemy import select

from app.infrastructure.persistence.database import create_database
from app.infrastructure.persistence.models import PlantPartModel, PlantSampleModel, PodObservationModel, QualityFindingModel, ResultVersionModel
from app.services.phenotype_domain_projection import PhenotypeDomainProjection


class PhenotypeDomainProjectionTests(unittest.TestCase):
    def test_completed_result_projects_idempotent_domain_records(self):
        with TemporaryDirectory() as directory:
            database = create_database(f"sqlite:///{(Path(directory) / 'domain.sqlite3').as_posix()}")
            job = SimpleNamespace(job_id=uuid4(), asset_id=uuid4(), project_id="trial-a", run_id="run-1", trace_id="trace-1", result={"plants": [{"id": 1, "label": "主干", "pod_count": 4}, {"id": 2, "label": "分枝", "pod_count": 3}], "provenance": {"algorithm_version": "stalk-v1"}})
            projection = PhenotypeDomainProjection(database.session_factory)
            version_id = projection.project_completed_analysis(job)
            projection.project_completed_analysis(job)
            projection.record_quality_findings(job, ["图像偏暗"], ["image_quality"])
            manual = {**job.result, "manual_revision": 1, "plants": [{"id": 1, "label": "主干", "pod_count": 5}]}
            projection.project_manual_revision(job, manual)
            self.assertEqual(projection.list_samples("trial-a")[0]["parts"][0]["pod_count"], 4)
            self.assertEqual(projection.list_samples("trial-b"), [])
            with database.session_factory() as session:
                self.assertEqual(session.scalar(select(PlantSampleModel).where(PlantSampleModel.project_id == "trial-a")).asset_id, str(job.asset_id))
                self.assertEqual(session.scalar(select(ResultVersionModel).where(ResultVersionModel.version_id == version_id)).algorithm_version, "stalk-v1")
                self.assertEqual(len(session.scalars(select(ResultVersionModel)).all()), 2)
                self.assertEqual(len(session.scalars(select(PlantPartModel)).all()), 3)
                self.assertEqual(sum(item.pod_count for item in session.scalars(select(PodObservationModel)).all()), 12)
                self.assertEqual(session.scalar(select(QualityFindingModel).where(QualityFindingModel.reason_code == "image_quality")).trace_id, "trace-1")
            database.engine.dispose()


if __name__ == "__main__":
    unittest.main()
