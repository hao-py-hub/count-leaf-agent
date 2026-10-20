"""Streaming upload helpers that enforce bounded local resource usage."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError


def copy_upload_limited(source: BinaryIO, destination: Path, maximum_bytes: int) -> int:
    """Copy an upload in chunks, removing incomplete output on size rejection."""
    if maximum_bytes < 1:
        raise ValueError("文件大小上限必须至少为 1 字节")
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := source.read(64 * 1024):
                total += len(chunk)
                if total > maximum_bytes:
                    raise ValueError(f"文件超过大小上限：最多 {maximum_bytes} 字节")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return total


def read_upload_limited(source: BinaryIO, maximum_bytes: int) -> bytes:
    """Read a bounded text upload without accepting an unbounded body."""
    content = source.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise ValueError(f"文件超过大小上限：最多 {maximum_bytes} 字节")
    return content


def verify_image_upload(path: Path, maximum_pixels: int) -> None:
    """Reject invalid or excessively large decoded images without loading pixels."""
    if maximum_pixels < 1:
        raise ValueError("图片像素上限必须至少为 1")
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width * height > maximum_pixels:
                raise ValueError(f"图片像素超过上限：最多 {maximum_pixels} 像素")
            image.verify()
    except (OSError, UnidentifiedImageError, ValueError) as error:
        path.unlink(missing_ok=True)
        if isinstance(error, ValueError) and "像素超过上限" in str(error):
            raise
        raise ValueError("上传文件不是有效图片") from error
