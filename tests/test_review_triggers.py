import unittest

from app.main import _review_reasons
from app.services.review_reasoning import review_reason_details


class ReviewTriggerTests(unittest.TestCase):
    def test_quality_conflict_and_total_mismatch_create_distinct_reasons(self) -> None:
        reasons = _review_reasons(
            {"recommended_to_retake": True, "issues": ["图片偏暗"]},
            {
                "total_pods": 9,
                "plants": [
                    {"pod_count": 2},
                    {"pod_count": 3, "pod_count_before_vlm": 4},
                ],
            },
        )
        self.assertEqual(len(reasons), 3)
        self.assertTrue(any("图片质量" in reason for reason in reasons))
        self.assertTrue(any("统计异常" in reason for reason in reasons))
        self.assertTrue(any("模型复核冲突" in reason for reason in reasons))

    def test_label_validation_warning_creates_model_conflict_reason(self) -> None:
        reasons = _review_reasons(
            {"recommended_to_retake": False},
            {"plants": [], "label_validation": {"warnings": [{"reason": "multiple_trunks"}]}},
        )
        self.assertEqual(reasons, ["模型标注异常：检测到多主干或疑似重复的部件框"])

    def test_label_validation_warning_uses_stable_model_conflict_code(self) -> None:
        reasons, codes = review_reason_details(
            {"recommended_to_retake": False},
            {"plants": [], "label_validation": {"warnings": [{"reason": "near_duplicate_overlap"}]}},
        )
        self.assertTrue(reasons)
        self.assertEqual(codes, ["model_conflict"])


if __name__ == "__main__":
    unittest.main()
