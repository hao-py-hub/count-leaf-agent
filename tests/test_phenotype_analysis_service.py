"""Traceability metadata tests for the analysis service adapter."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.history import HistoryStore
from app.services.phenotype_analysis_service import PhenotypeAnalysisService
from app.services.error_messages import ANALYSIS_FAILURE_MESSAGE, failure_diagnostic


class PhenotypeAnalysisServiceTests(unittest.TestCase):
    def test_failure_diagnostic_excludes_exception_text(self) -> None:
        diagnostic = failure_diagnostic(RuntimeError("C:\\secret\\model-key=private"))

        self.assertEqual(diagnostic, "RuntimeError")
        self.assertNotIn("secret", diagnostic)

    def test_pipeline_exception_uses_safe_public_error_message(self) -> None:
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite3")
            service = PhenotypeAnalysisService(store)

            def failing_stream(*args, **kwargs):
                raise RuntimeError("C:\\secret\\model-key=private")
                yield

            with patch("app.services.phenotype_analysis_service._pipeline_stream", failing_stream):
                result = service.run("image.jpg", "image.jpg", "upload", run_id="failure-run")

            self.assertEqual(result["message"], ANALYSIS_FAILURE_MESSAGE)
            self.assertNotIn("secret", result["message"])
            self.assertEqual(store.get("failure-run")["error_message"], ANALYSIS_FAILURE_MESSAGE)

    def test_completed_result_contains_non_secret_provenance(self) -> None:
        with TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite3")
            service = PhenotypeAnalysisService(store)

            def fake_stream(*args, **kwargs):
                yield {"type": "result", "run_id": kwargs["run_id"], "plant_count": 1, "plants": [{"id": 1, "label": "主干", "pod_count": 3}], "summary": {}}

            with patch("app.services.phenotype_analysis_service._pipeline_stream", fake_stream):
                result = service.run("image.jpg", "image.jpg", "upload", run_id="trace-run")

            self.assertEqual(result["total_pods"], 3)
            self.assertEqual(result["provenance"]["algorithm"], "stalk")
            self.assertTrue(result["provenance"]["algorithm_version"])
            self.assertNotIn("api_key", str(result["provenance"]).lower())
            self.assertEqual(store.get("trace-run")["result"]["provenance"], result["provenance"])


if __name__ == "__main__":
    unittest.main()
