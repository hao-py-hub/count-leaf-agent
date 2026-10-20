"""Tests for explainable image quality pre-checks."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from app.services.image_quality_service import ImageQualityService


class ImageQualityServiceTests(unittest.TestCase):
    def test_dark_low_resolution_image_requests_retake(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "dark.jpg"
            Image.new("RGB", (100, 100), "black").save(image_path)
            result = ImageQualityService().inspect(image_path)
            self.assertTrue(result["recommended_to_retake"])
            self.assertGreaterEqual(len(result["issues"]), 2)


if __name__ == "__main__":
    unittest.main()
