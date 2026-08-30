"""신체 실측을 성별 SizeKorea 분포에 상대화해 체형 프로파일로 만든다."""

from __future__ import annotations

import bisect
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from apps.recommend.services.gender import normalize_gender

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"
HOURGLASS = "hourglass"
INVERTED_TRIANGLE = "inverted_triangle"
TRIANGLE = "triangle"
RECTANGLE = "rectangle"
ROUND = "round"

SILHOUETTE_LABELS = {
    HOURGLASS: "모래시계형체형", INVERTED_TRIANGLE: "역삼각형체형",
    TRIANGLE: "삼각형체형", RECTANGLE: "직사각형체형", ROUND: "둥근체형",
    UNKNOWN: "미판정",
}

UNDERWEIGHT, NORMAL, OVERWEIGHT, OBESE = "underweight", "normal", "overweight", "obese"
BMI_LABELS = {
    UNDERWEIGHT: "저체중", NORMAL: "표준", OVERWEIGHT: "과체중",
    OBESE: "비만", UNKNOWN: "미판정",
}

RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "body_shape_thresholds.json"
_SNAPSHOT_ALIASES = {"inverted": INVERTED_TRIANGLE, "standard": RECTANGLE}
_RATIO_AXIS_ALIASES = {
    "leg_volume": "thigh_calf_ratio",
    "vertical_balance": "torso_leg_ratio",
}
_SEX_KEYS = {"male": ("M", "male"), "female": ("F", "female")}


def canonical_silhouette(value: Any) -> str:
    """과거 DB 스냅샷 라벨을 현재 5종 taxonomy로 정규화한다."""
    text = str(value or UNKNOWN).strip().lower()
    return _SNAPSHOT_ALIASES.get(text, text)


@dataclass(frozen=True)
class BodyProfile:
    silhouette: str = UNKNOWN
    bmi_band: str = UNKNOWN
    bmi: float | None = None
    ratios: dict[str, str] = field(default_factory=dict)
    known: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "silhouette", canonical_silhouette(self.silhouette))
        object.__setattr__(
            self,
            "ratios",
            {_RATIO_AXIS_ALIASES.get(axis, axis): value for axis, value in self.ratios.items()},
        )

    @property
    def is_empty(self) -> bool:
        return self.silhouette == UNKNOWN and self.bmi_band == UNKNOWN and not self.ratios

    def describe(self) -> str:
        parts = [SILHOUETTE_LABELS.get(self.silhouette, "미판정")]
        if self.bmi_band != UNKNOWN:
            parts.append(BMI_LABELS[self.bmi_band])
        parts.extend(f"{axis}:{value}" for axis, value in sorted(self.ratios.items()))
        return " · ".join(parts)


@lru_cache(maxsize=1)
def load_body_shape_thresholds() -> dict[str, Any]:
    """배포 artifact를 읽는다. 누락/손상은 잘못된 기본값 대신 미판정으로 이어진다."""
    try:
        document = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("체형 임계값 파일이 없습니다: %s", RULES_PATH)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("체형 임계값 JSON이 손상되었습니다: %s (%s)", RULES_PATH, exc)
        return {}
    if not isinstance(document, dict):
        logger.error("체형 임계값 최상위 값은 객체여야 합니다: %s", RULES_PATH)
        return {}
    required = ("version", "thresholds", "horizontal_classification_references")
    missing = [key for key in required if not document.get(key)]
    if missing:
        logger.error("체형 임계값 필수 섹션이 누락되었습니다: %s (파일=%s)", missing, RULES_PATH)
        return {}
    return document


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sex_block(document: dict[str, Any], section: str, gender: str) -> dict[str, Any]:
    root = document.get(section) or {}
    for key in _SEX_KEYS.get(gender, ()):
        block = root.get(key)
        if isinstance(block, dict):
            active = document.get("active_threshold_key", "all")
            return block.get(active, block.get("all", block))
    return {}


def _quantile(block: dict[str, Any], metric: str, percentile: int) -> float | None:
    values = block.get(metric)
    if not isinstance(values, dict):
        return None
    return _float(values.get(f"p{percentile}"))


def _empirical_percentile(value: float, reference: Any) -> float | None:
    """원자료 배열 또는 pXX 분위수 맵에서 0..1 경험 백분위를 구한다."""
    if isinstance(reference, list):
        values = sorted(v for raw in reference if (v := _number(raw)) is not None)
        if not values:
            return None
        left = bisect.bisect_left(values, value)
        right = bisect.bisect_right(values, value)
        # 생성기의 pandas rank(method="average", pct=True)와 동점 처리까지 같다.
        return (left + right + 1) / (2 * len(values))
    if not isinstance(reference, dict):
        return None
    points: list[tuple[float, float]] = []
    for key, raw in reference.items():
        if isinstance(key, str) and key.startswith("p") and key[1:].isdigit():
            if (sample := _number(raw)) is not None:
                points.append((sample, int(key[1:]) / 100))
    points.sort()
    if not points:
        return None
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (low_x, low_p), (high_x, high_p) in zip(points, points[1:]):
        if value <= high_x:
            return low_p + (value - low_x) * (high_p - low_p) / (high_x - low_x)
    return None


def _silhouette(measure: dict[str, float], gender: str, document: dict[str, Any]) -> str:
    required = ("shoulder", "chest", "waist", "hip")
    if not gender or any(name not in measure for name in required):
        return UNKNOWN
    references = _sex_block(document, "horizontal_classification_references", gender)
    thresholds = _sex_block(document, "thresholds", gender)
    percentiles = {
        name: _empirical_percentile(measure[name], references.get(name))
        for name in ("shoulder", "chest", "hip")
    }
    if any(value is None for value in percentiles.values()):
        return UNKNOWN

    upper_lower = 0.6 * percentiles["shoulder"] + 0.4 * percentiles["chest"] - percentiles["hip"]
    waist_definition = measure["waist"] / ((measure["chest"] + measure["hip"]) / 2)
    upper_p33 = _quantile(thresholds, "upper_lower", 33)
    upper_p67 = _quantile(thresholds, "upper_lower", 67)
    waist_p33 = _quantile(thresholds, "waist_definition", 33)
    waist_p90 = _quantile(thresholds, "waist_definition", 90)
    if None in (upper_p33, upper_p67, waist_p33, waist_p90):
        return UNKNOWN
    if waist_definition >= waist_p90:
        return ROUND
    if upper_lower >= upper_p67:
        return INVERTED_TRIANGLE
    if upper_lower <= upper_p33:
        return TRIANGLE
    if waist_definition <= waist_p33:
        return HOURGLASS
    return RECTANGLE


def _band(value: float, block: dict[str, Any], metric: str, low: str, middle: str, high: str) -> str | None:
    p33, p67 = _quantile(block, metric, 33), _quantile(block, metric, 67)
    if p33 is None or p67 is None:
        return None
    return low if value <= p33 else high if value >= p67 else middle


def _ratios(measure: dict[str, float], gender: str, document: dict[str, Any]) -> dict[str, str]:
    if not gender:
        return {}
    thresholds = _sex_block(document, "thresholds", gender)
    result: dict[str, str] = {}
    specs = (
        ("thigh_calf_ratio", "thigh_calf_ratio", "calf_dominant", "balanced", "thigh_dominant"),
        ("torso_leg_ratio", "torso_leg_ratio", "short_torso", "balanced", "long_torso"),
        ("neck_length", "neck_length", "short", "average", "long"),
    )
    for metric, axis, low, middle, high in specs:
        if metric in measure and (value := _band(measure[metric], thresholds, metric, low, middle, high)):
            result[axis] = value
    return result


def _bmi_band(bmi: float) -> str:
    return UNDERWEIGHT if bmi < 18.5 else NORMAL if bmi < 23 else OVERWEIGHT if bmi < 25 else OBESE


def build_profile(measurement: dict[str, Any] | None) -> BodyProfile:
    if not measurement:
        return BodyProfile(missing=("gender", "height", "weight", "shoulder", "chest", "waist", "hip"))
    gender = normalize_gender(measurement.get("gender"))
    names = (
        "height", "weight", "shoulder", "chest", "waist", "hip", "thigh_length",
        "calf_length", "torso_length", "leg_length", "neck_length",
        "thigh_calf_ratio", "torso_leg_ratio",
    )
    measure = {name: value for name in names if (value := _number(measurement.get(name))) is not None}
    bmi = round(measure["weight"] / (measure["height"] / 100) ** 2, 1) if "height" in measure and "weight" in measure else None
    document = load_body_shape_thresholds()
    known = (("gender",) if gender else ()) + tuple(name for name in names if name in measure)
    wanted = ("gender",) + names
    return BodyProfile(
        silhouette=_silhouette(measure, gender, document),
        bmi_band=_bmi_band(bmi) if bmi is not None else UNKNOWN,
        bmi=bmi,
        ratios=_ratios(measure, gender, document),
        known=known,
        missing=tuple(name for name in wanted if name not in known),
    )
