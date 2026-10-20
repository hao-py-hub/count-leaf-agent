"""项目真实资料 RAG 评测脚本的回归测试。"""

import unittest

from scripts.evaluate_project_rag import evaluate
from scripts.import_rag_knowledge import document_title


class ProjectRagEvaluationTests(unittest.TestCase):
    def test_document_title_uses_markdown_heading(self) -> None:
        self.assertEqual(document_title("01-拍摄规范", "# 拍摄规范\n\n正文"), "拍摄规范")

    def test_reports_ranked_recall_and_out_of_scope_observation(self) -> None:
        for mode in ("keyword", "hybrid"):
            report = evaluate(mode)

            self.assertEqual(report["answerable_sample_count"], 12)
            self.assertEqual(report["out_of_scope_sample_count"], 1)
            self.assertGreaterEqual(report["top_1_recall"], 0.0)
            self.assertLessEqual(report["top_1_recall"], report["top_3_recall"])
            self.assertLessEqual(report["top_3_recall"], report["top_5_recall"])
            self.assertGreater(report["top_5_recall"], 0.0)
            self.assertEqual(report["out_of_scope_retrieval_count"], 0)


if __name__ == "__main__":
    unittest.main()
