import csv
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.deepcanola_benchmark import (
    barcode_from_filename,
    build_manifest,
    evaluate_predictions,
    load_predictions,
    run_pipeline_samples,
)


class DeepCanolaBenchmarkTests(unittest.TestCase):
    def test_project_root_is_available_to_script_imports(self) -> None:
        from scripts.deepcanola_benchmark import PROJECT_ROOT

        self.assertIn(str(PROJECT_ROOT), sys.path)

    def test_barcode_parser_handles_regular_and_missing_separator_names(self) -> None:
        self.assertEqual(barcode_from_filename("BR017-028111 001.jpg"), 28111)
        self.assertEqual(barcode_from_filename("BR017101112 001.jpg"), 101112)
        self.assertIsNone(barcode_from_filename("invalid.jpg"))

    def test_manifest_excludes_images_without_csv_truth(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"; images.mkdir()
            for name in ("BR017-028111 001.jpg", "BR017101112 001.jpg", "BR017-237213 001.jpg"):
                (images / name).write_bytes(b"image")
            truth = root / "truth.csv"
            with truth.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["Barcode", "Pod_number"])
                writer.writeheader()
                writer.writerows([{"Barcode": "28111", "Pod_number": "1"}, {"Barcode": "28111", "Pod_number": "2"}, {"Barcode": "101112", "Pod_number": "1"}])

            manifest = build_manifest(images, truth)

            self.assertEqual(manifest["sample_count"], 2)
            self.assertEqual(manifest["unmatched_images"], ["BR017-237213 001.jpg"])

    def test_evaluation_reports_count_error_and_coverage(self) -> None:
        manifest = {"dataset": "test", "sample_count": 2, "unmatched_images": [], "samples": [
            {"image_file": "one.jpg", "ground_truth_pods": 10},
            {"image_file": "two.jpg", "ground_truth_pods": 5},
        ]}
        report = evaluate_predictions(manifest, {"one.jpg": 8, "ignored.jpg": 99})

        self.assertEqual(report["evaluated_sample_count"], 1)
        self.assertEqual(report["prediction_coverage"], 0.5)
        self.assertEqual(report["mae"], 2.0)
        self.assertEqual(report["undercount_rate"], 1.0)
        self.assertEqual(report["unknown_prediction_images"], ["ignored.jpg"])

    def test_pipeline_runner_persists_completed_and_failed_records(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "one.jpg"; image.write_bytes(b"image")
            records = root / "records.jsonl"
            manifest = {"samples": [{"image_file": "one.jpg", "barcode": 1, "ground_truth_pods": 2}]}

            def pipeline_stream(*_args, **_kwargs):
                yield {"type": "step"}
                yield {"type": "result", "run_id": "run-1", "total_pods": 3, "model_calls": []}

            self.assertEqual(run_pipeline_samples(manifest, root, records, 1, pipeline_stream), 1)
            report = evaluate_predictions({"dataset": "test", "sample_count": 1, "unmatched_images": [], "samples": manifest["samples"]}, load_predictions(records))
            self.assertEqual(report["mae"], 1.0)

    def test_pipeline_runner_stops_after_non_plant_structure_error(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("one.jpg", "two.jpg"):
                (root / name).write_bytes(b"image")
            records = root / "records.jsonl"
            manifest = {"samples": [
                {"image_file": "one.jpg", "barcode": 1, "ground_truth_pods": 2},
                {"image_file": "two.jpg", "barcode": 2, "ground_truth_pods": 3},
            ]}

            def pipeline_stream(*_args, **_kwargs):
                yield {"type": "error", "message": "VLM 结构识别没有返回任何主干/主枝/分枝，本次已停止。"}

            self.assertEqual(run_pipeline_samples(manifest, root, records, 2, pipeline_stream), 1)
            persisted_records = [line for line in records.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(persisted_records), 1)


if __name__ == "__main__":
    unittest.main()
