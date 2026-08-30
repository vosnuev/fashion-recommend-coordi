from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from body_measurement.src import inference


class BasicInferenceModelCompositionTests(unittest.TestCase):
    def test_exact_length_model_overrides_legacy_proxy_lengths(self) -> None:
        legacy = Mock()
        legacy.predict.return_value = [[
            85.0, 70.0, 95.0, 55.0, 35.0, 28.0, 37.0,
            30.0, 42.0, 45.0, 69.0, 9.0,
        ]]
        exact = Mock()
        exact.predict.return_value = [[31.0, 36.0, 44.0, 80.0, 6.5]]
        circumference = Mock()
        circumference.predict.return_value = [[54.0, 34.0, 27.0]]

        with (
            patch.object(inference, "load_model", return_value=legacy),
            patch.object(inference, "load_exact_length_model", return_value=exact),
            patch.object(inference, "load_circumference_model", return_value=circumference),
        ):
            result = inference.estimate_from_basic("female", 165, 55)

        self.assertEqual(result["leg_length"], 80.0)
        self.assertEqual(result["torso_leg_ratio"], 0.55)
        self.assertEqual(result["thigh_calf_ratio"], 0.861)
        self.assertEqual(result["chest"], 85.0)
        self.assertEqual(result["thigh"], 54.0)


class PhotoValidationTests(unittest.TestCase):
    def _payload(self) -> dict:
        payload = {
            "photo_valid": True,
            "failure_reason": "none",
            "front_person_count": 1,
            "side_person_count": 1,
            "front_head_visible": True,
            "side_head_visible": True,
            "front_face_visible": True,
            "side_face_visible": True,
            "front_feet_visible": True,
            "side_feet_visible": True,
            "front_full_body_visible": True,
            "side_full_body_visible": True,
            "front_pose_valid": True,
            "side_pose_valid": True,
            "image_quality_sufficient": True,
        }
        payload.update(
            {f"{target}_cm": 40.0 for target in inference.PHOTO_RESPONSE_TARGETS}
        )
        return payload

    def test_rejects_invalid_photo_before_measurement_parsing(self) -> None:
        payload = self._payload()
        payload.update(
            photo_valid=False,
            failure_reason="feet_not_visible",
            front_feet_visible=False,
        )
        for target in inference.PHOTO_RESPONSE_TARGETS:
            payload[f"{target}_cm"] = None

        with self.assertRaisesRegex(
            inference.PhotoValidationError, "사진 인식 실패.*양발"
        ):
            inference._parse_prediction(json.dumps(payload))

    def test_rejects_inconsistent_valid_flag(self) -> None:
        payload = self._payload()
        payload["side_full_body_visible"] = False

        with self.assertRaisesRegex(
            inference.PhotoValidationError, "사진 인식 실패.*전신"
        ):
            inference._parse_prediction(json.dumps(payload))

    def test_rejects_valid_photo_with_failure_reason(self) -> None:
        payload = self._payload()
        payload["failure_reason"] = "feet_not_visible"

        with self.assertRaisesRegex(
            inference.PhotoValidationError, "사진 인식 실패.*양발"
        ):
            inference._parse_prediction(json.dumps(payload))

    def test_rejects_unknown_failure_reason(self) -> None:
        payload = self._payload()
        payload["failure_reason"] = "invented_reason"

        with self.assertRaisesRegex(inference.BodyEstimationError, "알 수 없는"):
            inference._parse_prediction(json.dumps(payload))

    def test_rejects_boolean_person_count(self) -> None:
        payload = self._payload()
        payload["front_person_count"] = True

        with self.assertRaisesRegex(inference.BodyEstimationError, "0 이상의 정수"):
            inference._parse_prediction(json.dumps(payload))

    def test_rejects_non_boolean_validation_flag(self) -> None:
        payload = self._payload()
        payload["front_head_visible"] = 1

        with self.assertRaisesRegex(inference.BodyEstimationError, "boolean"):
            inference._parse_prediction(json.dumps(payload))

    def test_rejects_string_measurement(self) -> None:
        payload = self._payload()
        payload["chest_cm"] = "40.0"

        with self.assertRaisesRegex(inference.BodyEstimationError, "숫자로"):
            inference._parse_prediction(json.dumps(payload))

    def test_rejects_boolean_measurement(self) -> None:
        payload = self._payload()
        payload["chest_cm"] = True

        with self.assertRaisesRegex(inference.BodyEstimationError, "숫자로"):
            inference._parse_prediction(json.dumps(payload))

    def test_accepts_valid_full_body_pair(self) -> None:
        result = inference._parse_prediction(json.dumps(self._payload()))

        self.assertEqual(result["chest"], 40.0)
        self.assertEqual(result["thigh_calf_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
