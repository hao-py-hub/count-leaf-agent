"""Tests for bounded, cache-aware stalk twig filter configuration."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from app.config import bounded_unit_ratio
from app.services.analysis_job_service import AnalysisJobService


class _NoopAssetStore:
    pass


class _NoopRunner:
    def run(self, image_path: str, source_label: str, source_kind: str) -> dict:
        return {"type": "result", "run_id": "unused"}


class StalkThresholdConfigTests(unittest.TestCase):
    def test_ratio_accepts_positive_unit_interval(self) -> None:
        self.assertEqual(bounded_unit_ratio("0.70", "RATIO"), 0.7)
        self.assertEqual(bounded_unit_ratio(1, "RATIO"), 1.0)

    def test_ratio_rejects_invalid_values(self) -> None:
        for value in ("invalid", "0", "-0.1", "1.01"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "RATIO"):
                    bounded_unit_ratio(value, "RATIO")

    def test_cache_key_changes_when_stalk_filter_ratio_changes(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "plant.jpg"
            image_path.write_bytes(b"same-image")
            base_context = {
                "algorithm_version": "stalk-v1",
                "stalk_twig_width_ratio": "0.70",
                "stalk_twig_area_ratio": "0.25",
            }
            base_service = AnalysisJobService(_NoopAssetStore(), _NoopRunner(), cache_context=base_context)
            asset_id = uuid4()
            base_key = base_service._cache_key(asset_id, image_path)
            for changed_context in (
                {**base_context, "stalk_twig_width_ratio": "0.65"},
                {**base_context, "stalk_twig_area_ratio": "0.20"},
            ):
                with self.subTest(changed_context=changed_context):
                    changed_service = AnalysisJobService(
                        _NoopAssetStore(), _NoopRunner(), cache_context=changed_context
                    )
                    self.assertNotEqual(base_key, changed_service._cache_key(asset_id, image_path))


if __name__ == "__main__":
    unittest.main()
