"""Offline BR017 count benchmark utilities for the DeepCanola validation set."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def barcode_from_filename(filename: str) -> int | None:
    """Extract the terminal barcode from regular and malformed BR017 filenames."""
    match = re.search(r"(\d{5,6})\s+001\.(?:jpe?g|png)$", filename, re.IGNORECASE)
    return int(match.group(1)) if match else None


def load_pod_counts(csv_path: Path) -> dict[int, int]:
    """Aggregate one DeepCanola CSV row per manually recorded pod."""
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        counts = Counter(int(row["Barcode"]) for row in rows if row.get("Barcode"))
    return dict(counts)


def build_manifest(images_dir: Path, csv_path: Path) -> dict[str, Any]:
    """Match validation images to count truth without guessing missing labels."""
    counts = load_pod_counts(csv_path)
    image_files = sorted(
        path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    samples: list[dict[str, Any]] = []
    unmatched_images: list[str] = []
    matched_barcodes: set[int] = set()
    for image_path in image_files:
        barcode = barcode_from_filename(image_path.name)
        if barcode is None or barcode not in counts:
            unmatched_images.append(image_path.name)
            continue
        matched_barcodes.add(barcode)
        samples.append({"image_file": image_path.name, "barcode": barcode, "ground_truth_pods": counts[barcode]})
    return {
        "dataset": "DeepCanola BR017",
        "image_directory": str(images_dir),
        "ground_truth_csv": str(csv_path),
        "sample_count": len(samples),
        "unmatched_images": unmatched_images,
        "unmatched_truth_barcodes": sorted(set(counts) - matched_barcodes),
        "samples": samples,
    }


def load_predictions(predictions_path: Path) -> dict[str, int]:
    """Load explicit per-image count predictions from JSON or JSONL."""
    content = predictions_path.read_text(encoding="utf-8")
    parsed = json.loads(content) if predictions_path.suffix.lower() == ".json" else [json.loads(line) for line in content.splitlines() if line.strip()]
    records = parsed.get("predictions", []) if isinstance(parsed, dict) else parsed
    if not isinstance(records, list):
        raise ValueError("predictions 必须是记录列表，或包含 predictions 列表的 JSON 对象")
    predictions: dict[str, int] = {}
    for record in records:
        if not isinstance(record, dict) or "image_file" not in record:
            raise ValueError("每条 prediction 必须包含 image_file")
        if record.get("status") not in {None, "completed"}:
            continue
        if "total_pods" not in record:
            raise ValueError("已完成的 prediction 必须包含 total_pods")
        count = record["total_pods"]
        if not isinstance(count, int) or count < 0:
            raise ValueError("total_pods 必须是非负整数")
        predictions[str(record["image_file"])] = count
    return predictions


def run_pipeline_samples(
    manifest: dict[str, Any],
    images_dir: Path,
    records_path: Path,
    limit: int,
    pipeline_stream,
) -> int:
    """Run a bounded, resumable VLM+CV pilot and stop on incompatible image structure."""
    completed_images = set(load_predictions(records_path)) if records_path.exists() else set()
    pending = [sample for sample in manifest["samples"] if sample["image_file"] not in completed_images][:limit]
    records_path.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    with records_path.open("a", encoding="utf-8") as stream:
        for sample in pending:
            processed += 1
            image_path = images_dir / sample["image_file"]
            run_id = f"deepcanola_br017_{sample['barcode']}"
            terminal: dict[str, Any] | None = None
            try:
                for event in pipeline_stream(
                    str(image_path), method="stalk", run_id=run_id,
                    cache_key=run_id, source_label=sample["image_file"],
                ):
                    if event.get("type") in {"result", "error"}:
                        terminal = event
                if terminal is None:
                    raise RuntimeError("分析流程未返回终态")
                record = {
                    **sample,
                    "run_id": terminal.get("run_id", run_id),
                    "status": "completed" if terminal.get("type") == "result" else "failed",
                    "total_pods": terminal.get("total_pods") if terminal.get("type") == "result" else None,
                    "model_usage": terminal.get("model_usage"),
                    "model_calls": terminal.get("model_calls"),
                    "error": terminal.get("message") if terminal.get("type") == "error" else None,
                }
            except Exception as error:
                record = {**sample, "run_id": run_id, "status": "failed", "error_type": type(error).__name__}
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            print(f"[{processed}/{len(pending)}] {sample['image_file']} status={record['status']} pods={record.get('total_pods')}")
            if (
                record["status"] == "failed"
                and "没有返回任何主干/主枝/分枝" in str(record.get("error", ""))
            ):
                print("已检测到与整株结构不匹配的数据，停止后续样本以避免额外模型调用。")
                break
    return processed


def evaluate_predictions(manifest: dict[str, Any], predictions: dict[str, int]) -> dict[str, Any]:
    """Calculate count metrics only for manifest samples with provided predictions."""
    comparisons = [
        (sample, predictions[sample["image_file"]])
        for sample in manifest["samples"]
        if sample["image_file"] in predictions
    ]
    errors = [predicted - sample["ground_truth_pods"] for sample, predicted in comparisons]
    sample_count = len(comparisons)
    return {
        "dataset": manifest["dataset"],
        "eligible_sample_count": manifest["sample_count"],
        "evaluated_sample_count": sample_count,
        "prediction_coverage": sample_count / manifest["sample_count"] if manifest["sample_count"] else None,
        "mae": sum(abs(error) for error in errors) / sample_count if sample_count else None,
        "rmse": math.sqrt(sum(error * error for error in errors) / sample_count) if sample_count else None,
        "bias": sum(errors) / sample_count if sample_count else None,
        "exact_match_rate": sum(error == 0 for error in errors) / sample_count if sample_count else None,
        "undercount_rate": sum(error < 0 for error in errors) / sample_count if sample_count else None,
        "overcount_rate": sum(error > 0 for error in errors) / sample_count if sample_count else None,
        "unmatched_images": manifest["unmatched_images"],
        "unknown_prediction_images": sorted(set(predictions) - {sample["image_file"] for sample in manifest["samples"]}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and evaluate a DeepCanola BR017 count benchmark.")
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--ground-truth-csv", type=Path, required=True)
    parser.add_argument("--write-manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--write-report", type=Path)
    parser.add_argument("--run-pipeline-limit", type=int, default=0)
    parser.add_argument("--write-run-records", type=Path)
    arguments = parser.parse_args()

    manifest = build_manifest(arguments.images, arguments.ground_truth_csv)
    arguments.write_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest samples={manifest['sample_count']} unmatched_images={len(manifest['unmatched_images'])}")
    if arguments.predictions:
        report = evaluate_predictions(manifest, load_predictions(arguments.predictions))
        if arguments.write_report:
            arguments.write_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if arguments.run_pipeline_limit:
        if arguments.run_pipeline_limit < 1 or arguments.write_run_records is None:
            parser.error("--run-pipeline-limit 必须为正数，且需要 --write-run-records")
        from app.pipeline.analyzer import analyze_plant_image_stream

        run_pipeline_samples(
            manifest, arguments.images, arguments.write_run_records,
            arguments.run_pipeline_limit, analyze_plant_image_stream,
        )


if __name__ == "__main__":
    main()
