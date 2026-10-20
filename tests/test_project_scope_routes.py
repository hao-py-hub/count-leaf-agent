import unittest
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


class ProjectScopeRouteTests(unittest.TestCase):
    def test_agent_evidence_aggregates_authorized_result_and_feedback(self) -> None:
        job_service = app.state.analysis_job_service
        review_service = app.state.review_service
        original_get, original_feedback, original_summary = job_service.get, review_service.list_for_job, review_service.feedback_summary_for_job
        job_id = uuid4()
        job_service.get = lambda _job_id: SimpleNamespace(
            project_id="trial-a", status="review_required", trace_id="trace-a",
            result={"total_pods": 12, "provenance": {"algorithm_version": "stalk-v1"}},
        )
        review_service.list_for_job = lambda received_job_id, *, project_id: [
            {"job_id": str(received_job_id), "project_id": project_id, "reason_code": "image_quality"}
        ]
        review_service.feedback_summary_for_job = lambda received_job_id, *, project_id: {
            "review_count": 1, "reason_codes": ["image_quality"], "project_id": project_id,
        }
        try:
            response = TestClient(app).get(f"/api/v2/agent/jobs/{job_id}/evidence?project_id=trial-a")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["result"]["total_pods"], 12)
            self.assertEqual(response.json()["provenance"]["algorithm_version"], "stalk-v1")
            self.assertEqual(response.json()["review_feedback"][0]["reason_code"], "image_quality")
            self.assertEqual(response.json()["feedback_summary"]["review_count"], 1)
        finally:
            job_service.get, review_service.list_for_job, review_service.feedback_summary_for_job = original_get, original_feedback, original_summary

    def test_agent_evidence_rejects_wrong_project_before_feedback_read(self) -> None:
        job_service = app.state.analysis_job_service
        review_service = app.state.review_service
        original_get, original_feedback = job_service.get, review_service.list_for_job
        job_id = uuid4()
        job_service.get = lambda _job_id: SimpleNamespace(project_id="trial-a", status="completed", result={})
        review_service.list_for_job = lambda *_args, **_kwargs: self.fail("不应读取跨项目反馈")
        try:
            response = TestClient(app).get(f"/api/v2/agent/jobs/{job_id}/evidence?project_id=trial-b")
            self.assertEqual(response.status_code, 404)
        finally:
            job_service.get, review_service.list_for_job = original_get, original_feedback

    def test_agent_job_list_filters_project_scope(self) -> None:
        service = app.state.analysis_job_service
        original_list = service.list_recent
        job_id = uuid4()
        service.list_recent = lambda **_kwargs: [
            SimpleNamespace(
                project_id="trial-a",
                as_dict=lambda: {
                    "job_id": job_id, "asset_id": uuid4(), "status": "completed",
                    "created_at": "2026-08-30T00:00:00+00:00",
                    "updated_at": "2026-08-30T00:00:00+00:00",
                    "run_id": None, "error_message": None, "result": None,
                },
            ),
            SimpleNamespace(
                project_id="trial-b",
                as_dict=lambda: {
                    "job_id": uuid4(), "asset_id": uuid4(), "status": "completed",
                    "created_at": "2026-08-30T00:00:00+00:00",
                    "updated_at": "2026-08-30T00:00:00+00:00",
                    "run_id": None, "error_message": None, "result": None,
                },
            ),
        ]
        try:
            response = TestClient(app).get("/api/v2/agent/jobs?project_id=trial-a")
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item["job_id"] for item in response.json()], [str(job_id)])
        finally:
            service.list_recent = original_list

    def test_job_read_rejects_wrong_project(self) -> None:
        service = app.state.analysis_job_service
        original_get = service.get
        job_id = uuid4()
        service.get = lambda _job_id: SimpleNamespace(
            project_id="trial-a",
            as_dict=lambda: {
                "job_id": job_id, "asset_id": uuid4(), "status": "completed",
                "created_at": "2026-08-30T00:00:00+00:00",
                "updated_at": "2026-08-30T00:00:00+00:00",
                "run_id": None, "error_message": None, "result": None,
            },
        )
        try:
            response = TestClient(app).get(f"/api/v2/jobs/{job_id}?project_id=trial-b")
            self.assertEqual(response.status_code, 404)
        finally:
            service.get = original_get

    def test_agent_job_read_normalizes_project_scope(self) -> None:
        service = app.state.analysis_job_service
        original_get = service.get
        job_id = uuid4()
        service.get = lambda _job_id: SimpleNamespace(
            project_id="trial-a",
            as_dict=lambda: {
                "job_id": job_id, "asset_id": uuid4(), "status": "completed",
                "created_at": "2026-08-30T00:00:00+00:00",
                "updated_at": "2026-08-30T00:00:00+00:00",
                "run_id": None, "error_message": None, "result": None,
            },
        )
        try:
            response = TestClient(app).get(f"/api/v2/agent/jobs/{job_id}?project_id=%20trial-a%20")
            self.assertEqual(response.status_code, 200)
        finally:
            service.get = original_get

    def test_batch_read_rejects_wrong_project(self) -> None:
        service = app.state.batch_analysis_service
        original_get = service.get_status
        batch_id = uuid4()
        service.get_status = lambda _batch_id: {"project_id": "trial-a"}
        try:
            response = TestClient(app).get(f"/api/v2/batches/{batch_id}?project_id=trial-b")
            self.assertEqual(response.status_code, 404)
        finally:
            service.get_status = original_get

    def test_batch_list_forwards_project_scope(self) -> None:
        service = app.state.batch_analysis_service
        original_list = service.list_recent
        received = []
        service.list_recent = lambda **kwargs: received.append(kwargs) or []
        try:
            response = TestClient(app).get("/api/v2/batches?project_id=trial-a")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(received, [{"limit": 20, "project_id": "trial-a"}])
        finally:
            service.list_recent = original_list

    def test_batch_list_normalizes_project_scope(self) -> None:
        service = app.state.batch_analysis_service
        original_list = service.list_recent
        received = []
        service.list_recent = lambda **kwargs: received.append(kwargs) or []
        try:
            response = TestClient(app).get("/api/v2/batches?project_id=%20trial-a%20")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(received, [{"limit": 20, "project_id": "trial-a"}])
        finally:
            service.list_recent = original_list

    def test_review_list_normalizes_project_scope(self) -> None:
        service = app.state.review_service
        original_list = service.list_recent
        received = []
        service.list_recent = lambda **kwargs: received.append(kwargs) or []
        try:
            response = TestClient(app).get("/api/v2/reviews?project_id=%20trial-a%20")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                received,
                [{"limit": 50, "reason_code": None, "project_id": "trial-a"}],
            )
        finally:
            service.list_recent = original_list

    def test_batch_cancel_rejects_wrong_project_before_write(self) -> None:
        service = app.state.batch_analysis_service
        original_get, original_cancel = service.get_status, service.cancel_queued
        batch_id = uuid4()
        service.get_status = lambda _batch_id: {"project_id": "trial-a"}
        service.cancel_queued = lambda _batch_id: self.fail("不应执行跨项目取消")
        try:
            response = TestClient(app).post(
                f"/api/v2/batches/{batch_id}/cancel",
                json={"confirm": True, "project_id": "trial-b"},
            )
            self.assertEqual(response.status_code, 404)
        finally:
            service.get_status, service.cancel_queued = original_get, original_cancel

    def test_agent_job_retry_rejects_wrong_project_before_write(self) -> None:
        service = app.state.analysis_job_service
        original_get, original_retry = service.get, service.retry_failed
        job_id = uuid4()
        service.get = lambda _job_id: SimpleNamespace(project_id="trial-a")
        service.retry_failed = lambda _job_id: self.fail("不应执行跨项目重试")
        try:
            response = TestClient(app).post(
                f"/api/v2/agent/jobs/{job_id}/retry?confirm=true&project_id=trial-b"
            )
            self.assertEqual(response.status_code, 404)
        finally:
            service.get, service.retry_failed = original_get, original_retry


if __name__ == "__main__":
    unittest.main()
