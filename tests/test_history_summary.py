"""Tests for explicit local-history aggregate metrics."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.history import HistoryStore


class HistorySummaryTests(unittest.TestCase):
    def test_summary_declares_sample_scope_and_average(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = HistoryStore(Path(temporary_directory) / "history.sqlite3")
            store.start("run-1", "N0-a.jpg", "image.jpg")
            store.complete("run-1", {"plants": [{"label": "主干", "pod_count": 10}]}, [])
            store.start("run-2", "N0-b.jpg", "image.jpg")
            store.fail("run-2", "failed")

            summary = store.summarize("N0")

            self.assertEqual(summary["sample_count"], 2)
            self.assertEqual(summary["completed_count"], 1)
            self.assertEqual(summary["failed_count"], 1)
            self.assertEqual(summary["average_total_pods"], 10.0)
            self.assertEqual(summary["reference_ids"], ["run-1"])
            version_before = store.summary_cache_version()
            store.start("run-3", "N0-c.jpg", "image.jpg")
            self.assertNotEqual(store.summary_cache_version(), version_before)


if __name__ == "__main__":
    unittest.main()
