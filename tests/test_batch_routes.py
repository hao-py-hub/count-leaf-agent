"""HTTP contracts for controlled batch submission."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


class BudgetRejectingBatchService:
    @staticmethod
    def validate_assets(asset_ids):
        return None

    @staticmethod
    def submit_confirmed(asset_ids):
        raise ValueError("今日分析任务预算不足")


class CompletedBatchService:
    def __init__(self, batch_id):
        self.batch_id = batch_id

    def get_status(self, requested_id):
        if requested_id != self.batch_id:
            raise KeyError("批量任务不存在")
        return {
            "batch_id": str(requested_id), "status": "completed", "total": 2,
            "counts": {"completed": 2, "failed": 0, "cancelled": 0}, "job_ids": [],
        }


class BatchRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_service = app.state.batch_analysis_service
        app.state.batch_analysis_service = BudgetRejectingBatchService()

    def tearDown(self):
        app.state.batch_analysis_service = self.original_service

    def test_budget_rejection_returns_conflict(self):
        response = self.client.post(
            "/api/v2/batches",
            json={"asset_ids": [str(uuid4())], "confirm": True},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("预算不足", response.json()["detail"])

    def test_completed_batch_event_stream_emits_terminal_payload(self):
        batch_id = uuid4()
        app.state.batch_analysis_service = CompletedBatchService(batch_id)
        response = self.client.get(f"/api/v2/batches/{batch_id}/events?follow=false")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: status", response.text)
        self.assertIn("event: terminal", response.text)
        self.assertIn('"status": "completed"', response.text)

    def test_batch_cancel_and_retry_require_confirmation(self):
        batch_id = uuid4()
        for suffix in ("cancel", "retry-failed"):
            response = self.client.post(f"/api/v2/batches/{batch_id}/{suffix}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "confirmation_required")


if __name__ == "__main__":
    unittest.main()
