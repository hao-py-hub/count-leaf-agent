"""Explainable pre-analysis image quality checks for plant phenotyping."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat


class ImageQualityService:
    """Checks basic image conditions without modifying the source file."""

    def inspect(self, image_path: Path) -> dict:
        with Image.open(image_path) as image:
            grayscale = image.convert("L")
            brightness = ImageStat.Stat(grayscale).mean[0]
            contrast = ImageStat.Stat(grayscale).stddev[0]
            width, height = image.size

        issues: list[str] = []
        if min(width, height) < 480:
            issues.append("图片分辨率较低，建议使用短边至少 480 像素的原图。")
        if brightness < 35:
            issues.append("图片整体偏暗，建议补光后重拍。")
        elif brightness > 225:
            issues.append("图片整体偏亮，建议避免过曝后重拍。")
        if contrast < 12:
            issues.append("图片对比度不足，角果和背景可能难以区分。")

        return {
            "width": width,
            "height": height,
            "brightness": round(brightness, 1),
            "contrast": round(contrast, 1),
            "recommended_to_retake": bool(issues),
            "issues": issues,
        }
