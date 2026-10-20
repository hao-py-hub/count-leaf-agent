"""Resource-boundary tests for image and knowledge uploads."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.upload_limits import copy_upload_limited, read_upload_limited
from app.services.upload_limits import verify_image_upload
from PIL import Image


class UploadLimitTests(unittest.TestCase):
    def test_copy_rejects_oversized_file_and_removes_partial_output(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "image.jpg"
            with self.assertRaisesRegex(ValueError, "大小上限"):
                copy_upload_limited(BytesIO(b"x" * 11), destination, 10)
            self.assertFalse(destination.exists())

    def test_copy_accepts_bounded_file(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "image.jpg"
            self.assertEqual(copy_upload_limited(BytesIO(b"image"), destination, 5), 5)
            self.assertEqual(destination.read_bytes(), b"image")

    def test_read_rejects_oversized_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "大小上限"):
            read_upload_limited(BytesIO(b"x" * 11), 10)

    def test_invalid_image_is_removed_after_verification(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "fake.jpg"
            destination.write_bytes(b"not an image")
            with self.assertRaisesRegex(ValueError, "有效图片"):
                verify_image_upload(destination, 100)
            self.assertFalse(destination.exists())

    def test_image_over_pixel_limit_is_removed(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "large.png"
            Image.new("RGB", (11, 11)).save(destination)
            with self.assertRaisesRegex(ValueError, "像素超过上限"):
                verify_image_upload(destination, 100)
            self.assertFalse(destination.exists())
