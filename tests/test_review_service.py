"""Lifecycle tests for human review tasks."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID, uuid4

from app.services.review_service import ReviewService


class ReviewServiceTests(unittest.TestCase):

    def test_list_recent_isolated_by_project_id(self):
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            service.create(uuid4(), "项目 A", project_id="trial-a")
            service.create(uuid4(), "项目 B", project_id="trial-b")
            self.assertEqual([item["reason"] for item in service.list_recent(project_id="trial-a")], ["项目 A"])
            self.assertEqual([item["reason"] for item in service.list_recent(project_id="trial-b")], ["项目 B"])
    def test_teacher_can_resolve_an_open_review_with_result_version(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            review = service.create(uuid4(), "图像遮挡，需人工确认")
            resolved = service.resolve(
                UUID(review["review_id"]),
                "教师确认结果可用",
                run_id="run-001",
                history_revision=1,
            )
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(resolved["resolution"], "教师确认结果可用")
            self.assertEqual(resolved["run_id"], "run-001")
            self.assertEqual(resolved["history_revision"], 1)

    def test_review_cannot_be_resolved_twice(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            review = service.create(uuid4(), "图像遮挡，需人工确认")
            service.resolve(UUID(review["review_id"]), "已修订", run_id="run-001", history_revision=1)
            with self.assertRaisesRegex(KeyError, "已处理"):
                service.resolve(UUID(review["review_id"]), "重复结案", run_id="run-001", history_revision=2)

    def test_review_can_be_dismissed_without_creating_revision(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            review = service.create(uuid4(), "模型冲突")
            dismissed = service.dismiss(UUID(review["review_id"]), "保留自动结果")
            self.assertEqual(dismissed["status"], "dismissed")
            self.assertEqual(dismissed["resolution"], "保留自动结果")

    def test_review_reason_code_is_persisted(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            review = service.create(uuid4(), "图片偏暗", reason_code="image_quality")
            self.assertEqual(service.get(UUID(review["review_id"]))["reason_code"], "image_quality")
            self.assertEqual(service.list_recent()[0]["reason_code"], "image_quality")

    def test_reason_code_filter_matches_combined_trigger_codes(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            service.create(uuid4(), "组合原因", reason_code="image_quality,model_conflict")
            self.assertEqual(len(service.list_recent(reason_code="model_conflict")), 1)

    def test_unknown_reason_code_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            with self.assertRaisesRegex(ValueError, "不支持"):
                service.create(uuid4(), "未知", reason_code="unknown")

    def test_legacy_null_reason_code_is_readable_and_filterable(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "reviews.sqlite3"
            service = ReviewService(database)
            with service._connect() as connection:
                connection.execute(
                    "INSERT INTO review_tasks(review_id, job_id, reason, reason_code, status, resolution, created_at, resolved_at, run_id, history_revision, trace_id) VALUES (?, ?, ?, NULL, 'open', NULL, ?, NULL, NULL, NULL, NULL)",
                    (str(uuid4()), str(uuid4()), "旧记录", service._now()),
                )
            records = service.list_recent()
            self.assertEqual(records[0]["reason_code"], "legacy")
            self.assertEqual(service.list_recent(reason_code="manual"), [])

    def test_list_recent_orders_and_limits_review_queue(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = ReviewService(Path(temporary_directory) / "reviews.sqlite3")
            service.create(uuid4(), "第一项")
            service.create(uuid4(), "第二项")
            items = service.list_recent(limit=1)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["reason"], "第二项")


if __name__ == "__main__":
    unittest.main()
