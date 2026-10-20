"""Regression coverage for the offline local-RAG retrieval baseline."""

import unittest

from scripts.evaluate_rag_retrieval import evaluate


class RagRetrievalEvaluationTests(unittest.TestCase):
    def test_keyword_and_hybrid_reports_include_ranked_recall(self) -> None:
        for mode in ("keyword", "hybrid"):
            report = evaluate(mode)

            self.assertEqual(report["scope"], "offline local-rag golden cases")
            self.assertEqual(report["sample_count"], 3)
            self.assertGreaterEqual(report["top_1_recall"], 0.0)
            self.assertLessEqual(report["top_1_recall"], report["top_3_recall"])
            self.assertLessEqual(report["top_3_recall"], report["top_5_recall"])
            self.assertEqual(report["top_3_recall"], 1.0)
            self.assertEqual(report["top_5_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
