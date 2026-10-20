import tempfile
import time
import unittest
from pathlib import Path

from app.services.analysis_job_service import AnalysisJobService
from app.services.asset_store import AssetStore
from app.services.domain_events import DomainEvent, InMemoryDomainEventPublisher


class _Runner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        return {"type": "result", "run_id": "event-run", "total_pods": 1}


class DomainEventTests(unittest.TestCase):
    def test_event_has_project_trace_and_unique_identity(self) -> None:
        first = DomainEvent("AnalysisSubmitted", "AnalysisRun", "job-1", "trial-a", "trace-1")
        second = DomainEvent("AnalysisSubmitted", "AnalysisRun", "job-1", "trial-a", "trace-1")
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(first.project_id, "trial-a")

    def test_job_service_publishes_project_scoped_lifecycle_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "plant.jpg"
            image_path.write_bytes(b"image")
            assets = AssetStore()
            asset = assets.register(image_path, "plant.jpg", project_id="trial-a")
            publisher = InMemoryDomainEventPublisher()
            service = AnalysisJobService(assets, _Runner(), domain_event_publisher=publisher)
            job_id = service.submit(asset.asset_id, trace_id="trace-1", project_id="trial-a")
            deadline = time.monotonic() + 2
            while service.get(job_id).status in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual([event.event_type for event in publisher.events], ["AnalysisSubmitted", "AnalysisCompleted"])
            self.assertTrue(all(event.project_id == "trial-a" for event in publisher.events))
            self.assertTrue(all(event.trace_id == "trace-1" for event in publisher.events))


if __name__ == "__main__":
    unittest.main()
