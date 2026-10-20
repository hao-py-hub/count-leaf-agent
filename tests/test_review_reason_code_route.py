from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.review_service import ReviewService


class ReviewReasonCodeRouteTests(unittest.TestCase):
    def test_create_and_filter_reviews_by_reason_code(self) -> None:
        original_reviews = app.state.review_service
        original_jobs = app.state.analysis_job_service
        try:
            with TemporaryDirectory() as temporary_directory:
                service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
                job_id = uuid4()
                app.state.review_service = service
                app.state.analysis_job_service = SimpleNamespace(get=lambda requested: SimpleNamespace(status="completed"))
                with TestClient(app) as client:
                    response = client.post("/api/v2/reviews", json={"job_id": str(job_id), "reason": "偏暗", "reason_code": "image_quality", "confirm": True})
                    filtered = client.get("/api/v2/reviews?reason_code=image_quality")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["reason_code"], "image_quality")
                self.assertEqual(len(filtered.json()["items"]), 1)
        finally:
            app.state.review_service = original_reviews
            app.state.analysis_job_service = original_jobs


if __name__ == "__main__":
    unittest.main()
