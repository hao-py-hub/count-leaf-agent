from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_ledger import AuditLedger
from app.services.review_service import ReviewService


class ReviewDismissRouteTests(unittest.TestCase):
    def test_dismiss_requires_confirmation_then_closes_open_review(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            original_reviews = app.state.review_service
            original_ledger = app.state.audit_ledger
            try:
                service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
                app.state.review_service = service
                app.state.audit_ledger = AuditLedger(Path(temporary_directory) / "ledger.sqlite3")
                review = service.create(uuid4(), "统计异常")
                with TestClient(app) as client:
                    blocked = client.put(f"/api/v2/reviews/{review['review_id']}/dismiss")
                    confirmed = client.put(f"/api/v2/reviews/{review['review_id']}/dismiss", json={"confirm": True})
                self.assertEqual(blocked.json()["status"], "confirmation_required")
                self.assertEqual(confirmed.json()["status"], "dismissed")
            finally:
                app.state.review_service = original_reviews
                app.state.audit_ledger = original_ledger


if __name__ == "__main__":
    unittest.main()
