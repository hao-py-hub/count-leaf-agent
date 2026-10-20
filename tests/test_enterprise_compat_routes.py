"""PRD 约定兼容路径的 HTTP 契约测试。"""

import unittest

from fastapi.testclient import TestClient

from app.main import app


class EnterpriseCompatibilityRouteTests(unittest.TestCase):
    def test_history_alias_returns_bounded_scope(self):
        response = TestClient(app).get("/api/v2/history", params={"limit": 3, "offset": 0})
        self.assertEqual(response.status_code, 200)
        self.assertIn("scope", response.json())
        self.assertEqual(response.json()["scope"]["limit"], 3)

    def test_history_alias_rejects_whitespace_project_scope(self):
        response = TestClient(app).get("/api/v2/history", params={"project_id": "  "})
        self.assertEqual(response.status_code, 422)

    def test_audit_alias_returns_redacted_collections(self):
        response = TestClient(app).get("/api/v2/audit/events", params={"limit": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIn("events", response.json())
        self.assertIn("tool_calls", response.json())

    def test_batch_alias_requires_confirmation(self):
        from uuid import uuid4

        response = TestClient(app).post(
            "/api/v2/jobs/batch",
            json={"asset_ids": [str(uuid4())]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "confirmation_required")

    def test_missing_history_detail_is_not_found(self):
        response = TestClient(app).get("/api/v2/history/missing-run")
        self.assertEqual(response.status_code, 404)

    def test_report_alias_validates_job_before_confirmation(self):
        from uuid import uuid4

        response = TestClient(app).post(
            "/api/v2/reports",
            json={"analysis_job_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
