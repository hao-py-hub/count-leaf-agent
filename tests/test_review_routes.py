"""API contracts for human-reviewed result revisions."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.history import HistoryStore
from app.main import app
from app.services.analysis_job_service import AnalysisJob
from app.services.review_service import ReviewService
from app.services.domain_events import InMemoryDomainEventPublisher


class CompletedJobService:
    def __init__(self, job_id: UUID, run_id: str) -> None:
        self.job = AnalysisJob(job_id, uuid4(), "completed", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", run_id=run_id)

    def get(self, job_id: UUID) -> AnalysisJob:
        if job_id != self.job.job_id:
            raise KeyError("分析任务不存在")
        return self.job


class ReviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.job_id = uuid4()
        self.original_history_store = app.state.history_store
        self.original_review_service = app.state.review_service
        self.original_job_service = app.state.analysis_job_service
        self.original_writer = app.state.write_result_snapshot
        self.original_domain_event_publisher = app.state.domain_event_publisher
        self.store = HistoryStore(root / "history.sqlite3")
        self.store.start("review-run", "sample.jpg", "uploads/sample.jpg")
        self.store.complete("review-run", {"type": "result", "run_id": "review-run", "plant_count": 2, "plants": [{"id": 1, "label": "主干", "pod_count": 1}, {"id": 2, "label": "分枝", "pod_count": 4}], "summary": {}}, [])
        app.state.history_store = self.store
        app.state.review_service = ReviewService(root / "reviews.sqlite3")
        app.state.analysis_job_service = CompletedJobService(self.job_id, "review-run")
        app.state.write_result_snapshot = lambda run_id, result: None
        app.state.domain_event_publisher = InMemoryDomainEventPublisher()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        app.state.history_store = self.original_history_store
        app.state.review_service = self.original_review_service
        app.state.analysis_job_service = self.original_job_service
        app.state.write_result_snapshot = self.original_writer
        app.state.domain_event_publisher = self.original_domain_event_publisher
        self.temporary_directory.cleanup()

    def test_resolve_applies_valid_result_revision(self) -> None:
        review = app.state.review_service.create(self.job_id, "需核对遮挡区域")
        payload = {"resolution": "已人工校正", "revision_payload": {"plant_count": 2, "plants": [{"id": 1, "label": "主干", "pod_count": 2}, {"id": 2, "label": "分枝", "pod_count": 6}]}}
        response = self.client.put(f"/api/v2/reviews/{review['review_id']}/resolve", json=payload)
        self.assertEqual(response.json()["status"], "confirmation_required")
        response = self.client.put(f"/api/v2/reviews/{review['review_id']}/resolve", json={**payload, "confirm": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["history_revision"], 1)
        self.assertEqual(response.json()["run_id"], "review-run")
        record = self.store.get("review-run")
        self.assertEqual(record["result"]["total_pods"], 8)
        self.assertEqual(record["original_result"]["total_pods"], 5)
        event = app.state.domain_event_publisher.events[-1]
        self.assertEqual(event.event_type, "ReviewResolved")
        self.assertEqual(event.project_id, "default")

    def test_manual_review_creation_requires_confirmation(self) -> None:
        response = self.client.post(
            "/api/v2/reviews",
            json={"job_id": str(self.job_id), "reason": "需核对遮挡区域"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "confirmation_required")
        self.assertEqual(app.state.review_service.list_recent(), [])

    def test_confirmed_manual_review_publishes_domain_event(self) -> None:
        response = self.client.post(
            "/api/v2/reviews",
            json={"job_id": str(self.job_id), "reason": "需核对遮挡区域", "confirm": True},
        )
        self.assertEqual(response.status_code, 200)
        event = app.state.domain_event_publisher.events[-1]
        self.assertEqual(event.event_type, "ReviewCreated")
        self.assertEqual(event.aggregate_id, response.json()["review_id"])
        self.assertEqual(event.project_id, "default")

    def test_resolve_rejects_invalid_revision_payload(self) -> None:
        review = app.state.review_service.create(self.job_id, "需核对遮挡区域")
        response = self.client.put(f"/api/v2/reviews/{review['review_id']}/resolve", json={"resolution": "已人工校正", "revision_payload": {"plant_count": 2, "plants": [{"id": 1, "label": "主干", "pod_count": -1}]}, "confirm": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(app.state.review_service.get(UUID(review["review_id"]))["status"], "open")

    def test_review_detail_rejects_a_different_project_scope(self) -> None:
        review = app.state.review_service.create(self.job_id, "需核对遮挡区域", project_id="trial-a")
        response = self.client.get(f"/api/v2/reviews/{review['review_id']}?project_id=trial-b")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
