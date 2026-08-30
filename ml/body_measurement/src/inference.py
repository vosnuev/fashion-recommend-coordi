"""API 서버가 호출하는 신체치수 추론 인터페이스.

추론 경로는 두 개지만, 둘 다 상세 14개 필드를 채운 같은 형태의 dict를 반환한다.

- ``estimate_from_basic``  : 코어·둘레·정확 길이 Hist 모델을 조합하고 비율 계산
- ``estimate_from_photos`` : 사진 VLM의 길이 예측에서 비율을 계산

사진 VLM은 저장할 치수와 비율 계산용 길이를 함께 요청한다. 기존 결과는 허벅지·종아리
둘레와 팔뚝둘레를 사용했으므로 새 길이 정의의 평가에 재사용하지 않는다.

학습 코드(``benchmark.py``)와 달리 이 모듈은 서빙 전용이다. 모델을 하나만 lazy 로드하고
CLI·S3·학습 의존성을 갖지 않는다. 상수는 학습 시점
``data/hist/manifest.json``
값과 반드시 일치해야 한다.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import threading
from pathlib import Path

import joblib
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 학습 시점과 동일해야 하는 값들 (data/hist/manifest.json / retrain_11targets.py 기준).
# 순서가 어긋나면 예외 없이 조용히 틀린 숫자가 나오므로 임의로 바꾸지 않는다.
FEATURES = ["gender", "height", "weight"]
# 모델(hist_gradient_boosting_181.joblib)이 내놓는 값의 **순서**.
# scripts/train_hist_181.py 의 TARGETS 와 반드시 같아야 한다 — zip(strict=True)로 묶는다.
#
# 학습 자료는 이미지 세트 181명(SizeKorea 8차 직접측정) 한 벌이다. 사진 경로와 무사진
# 경로가 같은 사람·같은 계측 정의를 쓰게 하려는 것이다. 3D 측정(4,545행)에는 허벅지·
# 종아리·팔뚝 둘레 컬럼이 없고 우리 181명이 그 조사에 들어 있지도 않다(8개 항목
# 최근접 L1거리 최소 4.46cm, 0거리 0명).
MEASUREMENT_TARGETS = [
    "chest",
    "waist",
    "hip",
    "thigh",
    "calf",
    "arm",
    "shoulder",
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
]

# 길이에서 나눗셈으로 만드는 비율 2개 — 학습하지 않는다.
# 원본에서 비율 컬럼이 길이의 몫과 100퍼센트 일치함을 확인했고, 몫을 회귀로 배우면
# 예측 비율이 예측 길이의 몫과 어긋날 수 있어 계산으로 만든다.
RATIO_TARGETS = ["thigh_calf_ratio", "torso_leg_ratio"]
RATIO_SOURCES = {
    "thigh_calf_ratio": ("thigh_length", "calf_length"),
    "torso_leg_ratio": ("torso_length", "leg_length"),
}

# ⚠️ neck_length 는 키·몸무게로 예측되지 않는다. 정의를 4가지로 바꿔 재봐도
#    5-겹 CV R2가 -0.36~0.10 이라 사실상 집단 평균이 나온다(main 도 성별 회귀식으로
#    채우던 값이라 동작은 같다). 실제 값을 얻으려면 사진(VLM) 경로를 써야 한다.
UNINFORMATIVE_TARGETS = ["neck_length"]

TARGETS = MEASUREMENT_TARGETS + RATIO_TARGETS
GENDER_CODES = {"M": 0.0, "F": 1.0}
GENDER_ALIASES = {
    "M": "M",
    "F": "F",
    "MALE": "M",
    "FEMALE": "F",
    "남": "M",
    "여": "F",
    "남성": "M",
    "여성": "F",
}
GENDER_PUBLIC_LABELS = {"M": "male", "F": "female"}

# 사진 VLM에게 직접 물어보는 값. 비율은 응답값을 저장하지 않고 서버에서 계산한다.
PHOTO_MEASUREMENT_TARGETS = [
    "shoulder",
    "chest",
    "waist",
    "hip",
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
    # 둘레 3종도 사진에서 직접 물어본다 — 무사진 모델은 181명으로만 학습해서
    # 사진이 있으면 그 값을 우선 쓰는 편이 낫다.
    "thigh",
    "calf",
    "arm",
]
PHOTO_SUPPORT_TARGETS = ["torso_length", "leg_length"]
PHOTO_TARGETS = TARGETS
PHOTO_RESPONSE_TARGETS = PHOTO_MEASUREMENT_TARGETS

PHOTO_FAILURE_MESSAGES = {
    "person_not_detected": "사진에서 사람을 찾지 못했습니다.",
    "multiple_people": "사진에는 한 사람만 나오게 촬영해 주세요.",
    "head_not_visible": "머리와 얼굴이 모두 보이게 촬영해 주세요.",
    "face_not_visible": "얼굴이 보이게 촬영해 주세요.",
    "feet_not_visible": "양발이 모두 보이게 촬영해 주세요.",
    "body_cropped": "머리부터 발끝까지 전신이 나오게 촬영해 주세요.",
    "invalid_front_pose": "정면 사진은 카메라를 똑바로 바라보고 촬영해 주세요.",
    "invalid_side_pose": "측면 사진은 몸 전체가 옆을 향하도록 촬영해 주세요.",
    "low_image_quality": "사진이 너무 어둡거나 흐립니다. 밝은 곳에서 다시 촬영해 주세요.",
}
PHOTO_FAILURE_REASONS = frozenset(PHOTO_FAILURE_MESSAGES)

# SizeKorea 기준 참고 분포. 저장 실패 조건이 아니라 해석·문서화 기준으로만 쓴다.
RATIO_REFERENCE_RANGES = {
    "thigh_calf_ratio": (0.652, 0.970),
    "torso_leg_ratio": (0.466, 0.637),
}

# 학습 데이터(SizeKorea) 범위를 벗어난 입력은 KNN이 외삽하지 못해 신뢰할 수 없다.
HEIGHT_RANGE_CM = (100.0, 230.0)
WEIGHT_RANGE_KG = (25.0, 300.0)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# 서빙은 새 11개 저장 항목을 물어보는 _full 프롬프트를 쓴다.
PROMPT_PATH = PROJECT_ROOT / "prompts" / "body_measurement_prompt_full.j2"
SCHEMA_PATH = PROJECT_ROOT / "prompts" / "body_measurement_schema_full.json"

# 기존 181명 모델은 사진과 같은 직접측정 모집단의 코어 치수 4개만 담당한다.
# ⚠️ 이 아티팩트는 scikit-learn 1.8.0으로 저장됐고, 1.9.0에서 열면
#    ModuleNotFoundError: No module named '_loss'로 실패한다. 실행 환경의
#    scikit-learn은 반드시 1.8.0으로 고정해야 한다 (api/requirements.txt).
DEFAULT_MODEL_PATH = (
    PROJECT_ROOT / "data" / "hist" / "models" / "hist_gradient_boosting_181.joblib"
)
DEFAULT_EXACT_LENGTH_MODEL_PATH = (
    PROJECT_ROOT
    / "data"
    / "hist"
    / "models"
    / "hist_gradient_boosting_exact_lengths_v2.joblib"
)
# 사진 기반 서빙 모델. validation 39명에서 평균 MAE 2.757cm로 후보 중 가장 정확했다
# (Qwen 3.597 / Grok 3.441 / Gemini 3.962). 호출당 $0.004492로 Qwen보다 약 30배
# 비싸지만 정확도를 우선한다.
DEFAULT_VLM_MODEL = "moonshotai/kimi-k2.5"

# 기존 181명 모델의 12개 출력 순서. 서빙에서는 코어 치수 4개만 채택한다.
LEGACY_MODEL_OUTPUTS = [
    "chest",
    "waist",
    "hip",
    "thigh",
    "calf",
    "arm",
    "shoulder",
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
]

CORE_TARGETS = ["chest", "waist", "hip", "shoulder"]
EXACT_LENGTH_TARGETS = [
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
]

# 예측 결과에서 추출할 실제 길이/기본 지표 9개
LENGTH_TARGETS = [
    "shoulder",
    "chest",
    "waist",
    "hip",
    "thigh_length",
    "calf_length",
    "torso_length",
    "leg_length",
    "neck_length",
]

# 둘레 모델이 예측하는 타겟
CIRCUMFERENCE_TARGETS = ["thigh", "calf", "arm"]

_model = None
_model_lock = threading.Lock()

_exact_length_model = None
_exact_length_model_lock = threading.Lock()

_circumference_model = None
_circumference_model_lock = threading.Lock()


def _circumference_model_path() -> Path:
    return Path(
        os.getenv("BODY_CIRCUMFERENCE_MODEL_PATH")
        or (PROJECT_ROOT / "data" / "hist" / "models" / "hist_gradient_boosting_circumference.joblib")
    )


def load_circumference_model():
    global _circumference_model
    if _circumference_model is None:
        with _circumference_model_lock:
            if _circumference_model is None:
                path = _circumference_model_path()
                if not path.exists():
                    raise BodyEstimationError(
                        f"둘레 추정 모델 파일이 없습니다: {path}. "
                        "BODY_CIRCUMFERENCE_MODEL_PATH 환경변수를 확인하세요."
                    )
                _circumference_model = joblib.load(path)
    return _circumference_model



class BodyEstimationError(Exception):
    """추론 입력이 잘못됐거나 추론에 실패했을 때."""


class PhotoValidationError(BodyEstimationError):
    """사진이 신체치수 추정에 적합하지 않을 때."""

    def __init__(self, reason: str):
        self.reason = reason
        detail = PHOTO_FAILURE_MESSAGES.get(
            reason, "사진에서 전신을 정확히 인식하지 못했습니다. 다시 촬영해 주세요."
        )
        super().__init__(f"사진 인식 실패: {detail}")


def _model_path() -> Path:
    """서빙에 쓸 joblib 경로. 배포 환경에서는 환경변수로 주입한다.

    ``artifacts/models/*.joblib``은 .gitignore 대상이라 클론만으로는 파일이 없다.
    AWS에서는 S3에서 내려받은 경로를 BODY_MODEL_PATH로 지정해야 한다.
    """
    return Path(os.getenv("BODY_MODEL_PATH") or DEFAULT_MODEL_PATH)


def load_model():
    """추론 모델을 한 번만 로드해서 재사용한다 (프로세스당 1회)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                path = _model_path()
                if not path.exists():
                    raise BodyEstimationError(
                        f"신체치수 추정 모델 파일이 없습니다: {path}. "
                        "BODY_MODEL_PATH 환경변수를 확인하세요."
                    )
                _model = joblib.load(path)
    return _model


def _exact_length_model_path() -> Path:
    return Path(
        os.getenv("BODY_EXACT_LENGTH_MODEL_PATH") or DEFAULT_EXACT_LENGTH_MODEL_PATH
    )


def load_exact_length_model():
    """사용자 랜드마크 정의로 학습한 길이 v2 모델을 한 번만 로드한다."""
    global _exact_length_model
    if _exact_length_model is None:
        with _exact_length_model_lock:
            if _exact_length_model is None:
                path = _exact_length_model_path()
                if not path.exists():
                    raise BodyEstimationError(
                        f"정확 길이 추정 모델 파일이 없습니다: {path}. "
                        "BODY_EXACT_LENGTH_MODEL_PATH 환경변수를 확인하세요."
                    )
                _exact_length_model = joblib.load(path)
    return _exact_length_model


def normalize_gender(gender: str) -> str:
    """'male'/'남성'/'M' 등 표기 차이를 학습 때 쓴 'M'/'F'로 맞춘다."""
    key = str(gender).strip().upper()
    normalized = GENDER_ALIASES.get(key)
    if normalized is None:
        raise BodyEstimationError(f"성별은 male 또는 female이어야 합니다: {gender!r}")
    return normalized


def public_gender(gender: str) -> str:
    """API/Swagger/VLM 프롬프트에 노출할 성별 표기는 male/female로 통일한다."""
    return GENDER_PUBLIC_LABELS[normalize_gender(gender)]


def _build_features(gender: str, height: float, weight: float) -> pd.DataFrame:
    """모델 입력 1행을 만든다. 학습 때와 같은 컬럼명·순서를 유지한다."""
    try:
        height = float(height)
        weight = float(weight)
    except (TypeError, ValueError) as error:
        raise BodyEstimationError("키와 몸무게는 숫자여야 합니다.") from error

    if not HEIGHT_RANGE_CM[0] <= height <= HEIGHT_RANGE_CM[1]:
        raise BodyEstimationError(
            f"키는 {HEIGHT_RANGE_CM[0]:.0f}~{HEIGHT_RANGE_CM[1]:.0f}cm 사이여야 합니다."
        )
    if not WEIGHT_RANGE_KG[0] <= weight <= WEIGHT_RANGE_KG[1]:
        raise BodyEstimationError(
            f"몸무게는 {WEIGHT_RANGE_KG[0]:.0f}~{WEIGHT_RANGE_KG[1]:.0f}kg 사이여야 합니다."
        )

    return pd.DataFrame(
        [
            {
                "gender": GENDER_CODES[normalize_gender(gender)],
                "height": height,
                "weight": weight,
            }
        ],
        columns=FEATURES,
    )


def apply_ratios(measurements: dict[str, float]) -> dict[str, float]:
    """길이에서 비율 2개를 계산해 채운다. 분모가 없거나 0이면 그 비율만 건너뛴다.

    비율을 예측값으로 받지 않고 여기서 만드는 이유는, 그래야 응답의 비율이 같은 응답의
    길이와 항상 일치하기 때문이다 (사진 경로에서 길이만 덮어써도 비율이 따라 움직인다).
    """
    for ratio, (numerator, denominator) in RATIO_SOURCES.items():
        top, bottom = measurements.get(numerator), measurements.get(denominator)
        if top is None or bottom is None or bottom <= 0:
            continue
        value = top / bottom
        if math.isfinite(value):
            measurements[ratio] = round(value, 3)
    return measurements


def estimate_from_basic(gender: str, height: float, weight: float) -> dict[str, float]:
    """성별·키·몸무게로 치수 12개를 추정하고 비율 2개를 계산한다 (총 14개).

    학습 데이터가 달라 모델이 셋으로 나뉘어 있다 — 코어 둘레 4개(181 모델),
    길이 5개(exact_lengths_v2 모델), 부가 둘레 3개(circumference 모델).
    181 모델은 길이도 함께 내놓지만 옛 랜드마크 정의라 CORE_TARGETS만 취한다.
    길이 모델은 비율 2개도 함께 내놓지만 그 출력은 버리고 계산값으로 대체한다.
    """
    features = _build_features(gender, height, weight)

    predicted = load_model().predict(features)[0]
    measurements = {
        target: round(float(value), 1)
        for target, value in zip(LEGACY_MODEL_OUTPUTS, predicted, strict=True)
        if target in CORE_TARGETS
    }

    exact_lengths = load_exact_length_model().predict(features)[0]
    measurements.update(
        {
            target: round(float(value), 1)
            for target, value in zip(EXACT_LENGTH_TARGETS, exact_lengths, strict=True)
        }
    )

    circumference = load_circumference_model().predict(features)[0]
    measurements.update(
        {
            target: round(float(value), 1)
            for target, value in zip(CIRCUMFERENCE_TARGETS, circumference, strict=True)
        }
    )

    return apply_ratios(measurements)


def _safe_ratio(numerator: float, denominator: float, field_name: str) -> float:
    """VLM이 준 기준 길이 2개로 저장용 비율을 계산한다."""
    if denominator <= 0:
        raise BodyEstimationError(f"{field_name} 계산에 필요한 분모가 0 이하입니다.")
    ratio = numerator / denominator
    if not math.isfinite(ratio):
        raise BodyEstimationError(f"{field_name} 계산 결과가 유효하지 않습니다.")
    return round(ratio, 3)


# ---------------------------------------------------------------------------
# 사진 기반 (VLM)
# ---------------------------------------------------------------------------


def _render_prompt(gender: str, height: float, weight: float) -> str:
    """평가 때 쓴 프롬프트를 그대로 재사용한다.

    벤치마크와 서빙이 다른 프롬프트를 쓰면 측정한 MAE가 운영 성능을 설명하지 못한다.
    """
    from jinja2 import Environment, StrictUndefined

    template = Environment(undefined=StrictUndefined).from_string(
        PROMPT_PATH.read_text(encoding="utf-8")
    )
    prompt = template.render(
        gender=public_gender(gender),
        height_cm=float(height),
        weight_kg=float(weight),
    )
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return prompt + "\n\nRequired JSON schema:\n" + json.dumps(schema)


def _image_part(image_bytes: bytes) -> dict:
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }


def _parse_prediction(content: str) -> dict[str, float]:
    """모델 응답 JSON에서 부위별 수치를 꺼낸다. 코드펜스로 감싸서 오는 경우가 있다.

    필수 측정값 중 하나라도 빠지거나 숫자가 아니면 실패로 본다. 사진 응답을
    무사진 모델의 임시값으로 조용히 대체하지 않는다.
    """
    cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise BodyEstimationError(f"모델 응답을 JSON으로 읽지 못했습니다: {error}") from error
    if not isinstance(payload, dict):
        raise BodyEstimationError("모델 응답은 JSON 객체여야 합니다.")

    validation_fields = (
        "photo_valid",
        "failure_reason",
        "front_person_count",
        "side_person_count",
        "front_head_visible",
        "side_head_visible",
        "front_face_visible",
        "side_face_visible",
        "front_feet_visible",
        "side_feet_visible",
        "front_full_body_visible",
        "side_full_body_visible",
        "front_pose_valid",
        "side_pose_valid",
        "image_quality_sufficient",
    )
    missing_validation = [key for key in validation_fields if key not in payload]
    if missing_validation:
        raise BodyEstimationError(
            f"모델 응답에 사진 품질 판정 키가 없습니다: {missing_validation}"
        )

    bool_fields = (
        "photo_valid",
        "front_head_visible",
        "side_head_visible",
        "front_face_visible",
        "side_face_visible",
        "front_feet_visible",
        "side_feet_visible",
        "front_full_body_visible",
        "side_full_body_visible",
        "front_pose_valid",
        "side_pose_valid",
        "image_quality_sufficient",
    )
    for key in bool_fields:
        if type(payload[key]) is not bool:
            raise BodyEstimationError(f"모델이 {key} 값을 boolean으로 주지 않았습니다.")
    for key in ("front_person_count", "side_person_count"):
        if type(payload[key]) is not int or payload[key] < 0:
            raise BodyEstimationError(f"모델이 {key} 값을 0 이상의 정수로 주지 않았습니다.")

    reason = payload.get("failure_reason")
    if reason != "none" and reason not in PHOTO_FAILURE_REASONS:
        raise BodyEstimationError(f"모델이 알 수 없는 사진 실패 코드를 반환했습니다: {reason!r}")
    if payload.get("photo_valid") is not True:
        if reason == "none":
            reason = "unknown"
        raise PhotoValidationError(reason)
    if reason != "none":
        raise PhotoValidationError(reason)

    # 모델의 종합 판정이 개별 근거와 모순되면 안전하게 실패시킨다.
    if payload["front_person_count"] != 1 or payload["side_person_count"] != 1:
        reason = "person_not_detected" if 0 in (
            payload["front_person_count"], payload["side_person_count"]
        ) else "multiple_people"
        raise PhotoValidationError(reason)
    required_true = {
        "front_head_visible": "head_not_visible",
        "side_head_visible": "head_not_visible",
        "front_face_visible": "face_not_visible",
        "side_face_visible": "face_not_visible",
        "front_feet_visible": "feet_not_visible",
        "side_feet_visible": "feet_not_visible",
        "front_full_body_visible": "body_cropped",
        "side_full_body_visible": "body_cropped",
        "front_pose_valid": "invalid_front_pose",
        "side_pose_valid": "invalid_side_pose",
        "image_quality_sufficient": "low_image_quality",
    }
    for key, failure_reason in required_true.items():
        if payload[key] is not True:
            raise PhotoValidationError(failure_reason)

    missing = []
    for target in PHOTO_RESPONSE_TARGETS:
        key_name = f"{target}_cm"
        if key_name not in payload:
            missing.append(key_name)
    if missing:
        raise BodyEstimationError(f"모델 응답에 필수 키가 없습니다: {missing}")

    predicted: dict[str, float] = {}
    support: dict[str, float] = {}
    for target in PHOTO_RESPONSE_TARGETS:
        key_name = f"{target}_cm"
        value = payload.get(key_name)
        try:
            if type(value) not in (int, float):
                raise TypeError("number required")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("finite number required")
            predicted[target] = round(numeric, 1)
            if target in PHOTO_SUPPORT_TARGETS:
                support[target] = numeric
        except (TypeError, ValueError):
            raise BodyEstimationError(
                f"모델이 {key_name} 값을 숫자로 주지 않았습니다: {value!r}"
            ) from None
    predicted["thigh_calf_ratio"] = _safe_ratio(
        predicted["thigh_length"], predicted["calf_length"], "thigh_calf_ratio"
    )
    predicted["torso_leg_ratio"] = _safe_ratio(
        predicted["torso_length"], predicted["leg_length"], "torso_leg_ratio"
    )
    return predicted


def _call_vlm(prompt: str, front_image: bytes, side_image: bytes) -> str:
    """OpenRouter로 사진 2장을 보내고 응답 본문을 받는다.

    벤치마크와 동일하게, 응답이 길이 제한으로 잘렸을 때만 토큰을 늘려 한 번 재시도한다
    (검증 39명 중 1건이 이 경우였다).
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise BodyEstimationError("OPENROUTER_API_KEY가 설정되지 않았습니다.")

    model = os.getenv("BODY_VLM_MODEL") or DEFAULT_VLM_MODEL
    timeout = float(os.getenv("BODY_VLM_TIMEOUT_SECONDS", "90"))
    max_retries = int(os.getenv("BODY_VLM_MAX_RETRIES", "3"))
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_finish_reason = None
    for max_tokens in (256, 512):
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        _image_part(front_image),
                        _image_part(side_image),
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "reasoning": {"effort": "none"},
            "response_format": {"type": "json_object"},
        }
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    OPENROUTER_URL, headers=headers, json=payload, timeout=timeout
                )
            except requests.RequestException as error:
                last_error = error
                if attempt < max_retries:
                    time.sleep(min(attempt, 3))
                continue

            if response.ok:
                break
            last_error = BodyEstimationError(
                f"VLM 호출 실패 (HTTP {response.status_code})"
            )
            if response.status_code < 500 or attempt == max_retries:
                raise last_error
            time.sleep(min(attempt, 3))
        else:
            raise BodyEstimationError(f"VLM 호출 실패: {last_error}") from last_error

        choice = response.json()["choices"][0]
        content = choice["message"].get("content")
        if content:
            return content
        last_finish_reason = choice.get("finish_reason")
        if last_finish_reason != "length":
            break

    raise BodyEstimationError(
        f"모델이 응답 본문을 반환하지 않았습니다 (finish_reason={last_finish_reason})."
    )


def estimate_from_photos(
    gender: str,
    height: float,
    weight: float,
    front_image: bytes,
    side_image: bytes,
) -> dict[str, float]:
    """사진 2장 + 기본 정보로 상세 치수·체형 지표를 추정한다.

    VLM이 필수 길이값을 모두 반환해야 성공한다. 누락 시 기본 정보의 임시 수치로
    대체하지 않고 오류를 반환한다.
    반환 형태는
    ``estimate_from_basic``과 완전히 같아서 API 응답 스키마가 갈라지지 않는다.
    """
    prompt = _render_prompt(gender, height, weight)
    content = _call_vlm(prompt, front_image, side_image)
    return _parse_prediction(content)
