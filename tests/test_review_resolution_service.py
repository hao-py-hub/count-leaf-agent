"""复核结案共享编排服务测试。"""

from types import SimpleNamespace
import unittest
from uuid import uuid4

from app.services.review_resolution_service import ReviewResolutionService


class ReviewResolutionServiceTests(unittest.TestCase):
    def test_resolution_creates_revision_before_closing_review(self):
        review_id = uuid4()
        job_id = uuid4()
        operations = []

        class Reviews:
            def get(self, requested_id):
                return {"review_id": str(requested_id), "job_id": str(job_id), "status": "open"}

            def resolve(self, requested_id, resolution, *, run_id, history_revision):
                operations.append(("resolve", requested_id, resolution, run_id, history_revision))
                return {"status": "resolved", "history_revision": history_revision}

        class Jobs:
            def get(self, requested_id):
                if requested_id != job_id:
                    raise KeyError("分析任务不存在")
                return SimpleNamespace(status="completed", run_id="run-001")

        class History:
            def update(self, run_id, payload):
                operations.append(("update", run_id, payload))
                return {"manual_revision": 2, "plants": []}

        def snapshot(run_id, result):
            operations.append(("snapshot", run_id, result["manual_revision"]))

        result = ReviewResolutionService(
            review_service=Reviews(), analysis_job_service=Jobs(), history_store=History(),
            write_result_snapshot=snapshot,
        ).resolve(review_id, "人工修订", {"plants": []})

        self.assertEqual(result["status"], "resolved")
        self.assertEqual([operation[0] for operation in operations], ["update", "snapshot", "resolve"])

    def test_review_required_job_can_be_resolved(self):
        review_id = uuid4()
        job_id = uuid4()

        class Reviews:
            def get(self, _requested_id):
                return {"job_id": str(job_id), "status": "open"}

            def resolve(self, *_args, **_kwargs):
                return {"status": "resolved"}

        class Jobs:
            def get(self, _requested_id):
                return SimpleNamespace(status="review_required", run_id="run-review")

        service = ReviewResolutionService(
            review_service=Reviews(), analysis_job_service=Jobs(),
            history_store=SimpleNamespace(update=lambda *_args: {"manual_revision": 1}),
            write_result_snapshot=lambda *_args: None,
        )
        self.assertEqual(service.resolve(review_id, "已校正", {"total_pods": 10})["status"], "resolved")

    def test_wrong_project_is_rejected_before_revision_write(self):
        review_id = uuid4()
        history = SimpleNamespace(update=lambda *_args: self.fail("不应写入跨项目修订"))

        class Reviews:
            def get(self, _requested_id):
                return {"job_id": str(uuid4()), "status": "open", "project_id": "trial-a"}

        service = ReviewResolutionService(
            review_service=Reviews(), analysis_job_service=SimpleNamespace(),
            history_store=history, write_result_snapshot=lambda *_args: None,
        )
        with self.assertRaisesRegex(KeyError, "复核任务不存在"):
            service.resolve(review_id, "人工确认", {"plants": []}, expected_project_id="trial-b")


if __name__ == "__main__":
    unittest.main()
