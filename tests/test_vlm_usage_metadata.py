"""Unit tests for VLM usage metadata without provider access."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from app.pipeline.vlm_counter import validate_plant_detections, vlm_detect_adhesion, vlm_label_plants, vlm_verify_crop
from app.pipeline.pod_verify_vlm import verify_pod_markers


def _client_with_response(content: str, prompt_tokens: int = 0, completion_tokens: int = 0):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens),
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response)))


class VlmUsageMetadataTests(unittest.TestCase):
    def test_plant_detection_validation_rejects_invalid_and_clamps_bbox(self) -> None:
        accepted, metadata = validate_plant_detections([
            {"id": 1, "label": "主干", "bbox": [-20, 10, 1100, 900]},
            {"id": 1, "label": "分枝", "bbox": [0, 0, 10, 10]},
            {"id": 2, "label": "unknown", "bbox": [0, 0, 10, 10]},
            {"id": 3, "label": "分枝", "bbox": [20, 20, 20, 40]},
        ])
        self.assertEqual([plant["id"] for plant in accepted], [1])
        self.assertEqual(accepted[0]["bbox"], [0.0, 10.0, 1000.0, 900.0])
        self.assertEqual(len(metadata["rejected"]), 3)

    def test_plant_detection_validation_warns_on_multiple_trunks_and_duplicate_overlap(self) -> None:
        accepted, metadata = validate_plant_detections([
            {"id": 1, "label": "主干", "bbox": [0, 0, 1000, 1000]},
            {"id": 2, "label": "主干", "bbox": [1, 1, 999, 999]},
            {"id": 3, "label": "分枝", "bbox": [100, 100, 500, 500]},
            {"id": 4, "label": "分枝", "bbox": [101, 101, 499, 499]},
        ])
        self.assertEqual([plant["id"] for plant in accepted], [1, 3, 4])
        reasons = [warning["reason"] for warning in metadata["warnings"]]
        self.assertIn("multiple_trunks", reasons)
        self.assertIn("near_duplicate_overlap", reasons)

    def test_label_call_exposes_provider_usage(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        content = '{"plants":[{"id":1,"label":"主干","bbox":[0,0,1000,1000]}],"ruler":null,"label_tag":null}'
        with patch("app.pipeline.vlm_counter._get_client", return_value=_client_with_response(content, 12, 3)):
            result = vlm_label_plants(image)
        self.assertEqual(result["model_call"], {"source": "label", "input_tokens": 12, "output_tokens": 3, "error_code": None})

    def test_crop_verify_parse_failure_is_accounted_for(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        plant = {"id": 1, "label": "分枝", "bbox": [0, 0, 1000, 1000]}
        with patch("app.pipeline.vlm_counter._get_client", return_value=_client_with_response("not json", 8, 2)):
            result = vlm_verify_crop(image, image, plant, [plant])
        self.assertEqual(result["model_call"], {"source": "crop_verify", "input_tokens": 8, "output_tokens": 2, "error_code": "parse_error"})

    def test_pod_verify_exposes_provider_usage(self) -> None:
        image = np.zeros((40, 40, 3), dtype=np.uint8)
        markers = [{"id": 1, "type": "pod", "x_norm": 0.5, "y_norm": 0.5}]
        with patch("app.pipeline.pod_verify_vlm._get_client", return_value=_client_with_response('{"false_positive_ids":[],"restore_filtered_ids":[]}', 7, 4)), \
             patch("app.pipeline.pod_verify_vlm._load_support_examples", return_value=([], [])):
            result = verify_pod_markers(image, image, markers, pod_count=1)
        self.assertEqual(result["model_call"], {"source": "pod_verify", "input_tokens": 7, "output_tokens": 4})

    def test_adhesion_call_exposes_provider_usage(self) -> None:
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        with patch("app.pipeline.vlm_counter._get_client", return_value=_client_with_response('{"has_foreign":false,"regions":[]}', 6, 1)):
            result = vlm_detect_adhesion(image, plant_id=1, label="分枝")
        self.assertEqual(result["model_call"], {"source": "adhesion", "input_tokens": 6, "output_tokens": 1, "error_code": None})


if __name__ == "__main__":
    unittest.main()
