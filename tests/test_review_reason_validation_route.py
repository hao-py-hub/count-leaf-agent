from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.review_service import ReviewService


class ReviewReasonValidationRouteTests(unittest.TestCase):
    def test_unknown_reason_code_returns_bad_request(self) -> None:
        original_reviews = app.state.review_service
        original_jobs = app.state.analysis_job_service
        try:
            with TemporaryDirectory() as temporary_directory:
                app.state.review_service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
                app.state.analysis_job_service = SimpleNamespace(get=lambda requested: SimpleNamespace(status="completed"))
                with TestClient(app) as client:
                    response = client.post("/api/v2/reviews", json={"job_id": str(uuid4()), "reason": "未知", "reason_code": "unknown", "confirm": True})
                self.assertEqual(response.status_code, 400)
        finally:
            app.state.review_service = original_reviews
            app.state.analysis_job_service = original_jobs


if __name__ == "__main__":
    unittest.main()
