"""사이즈코리아 인체치수조사 xlsx 파서 (설계 문서 5의 2-1·3-1절).

S3 `22.사이즈코리아/`의 5~8차 xlsx를 표준 스키마 레코드로 정규화한다.
차수별 측정항목 명칭이 다를 수 있어 **후보 부분문자열 매칭**으로 컬럼을
찾는다. 실제 컬럼명은 `scripts/body_records/build_manifest.py --dump-headers`
로 실측 확인 후, 매칭이 어긋나는 항목은 COLUMN_CANDIDATES에 명칭을 추가한다
(설계 10절 체크리스트 1번).

표준 스키마 (1행 = 피측정자 1인):
    gender(male|female), age(int|None), height, weight,
    head, chest, waist, hip, thigh, calf, arm, shoulder, pants_length  [cm]
    survey("5차".."8차"), source_row_id("파일명:행번호")
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# 출력 필드 → 컬럼명 후보 부분문자열 (앞선 후보 우선).
# "팔뚝" = 위팔둘레, 어깨너비 = 어깨사이길이 채택 (설계 10절 3번 — 팀 확정 반영 지점)
COLUMN_CANDIDATES: dict[str, list[str]] = {
    "gender": ["성별"],
    "age": ["만나이", "나이", "연령"],
    "height": ["키", "신장"],
    "weight": ["몸무게", "체중"],
    "head": ["머리둘레"],
    "chest": ["젖가슴둘레", "가슴둘레"],
    "waist": ["허리둘레"],
    "hip": ["엉덩이둘레"],
    "thigh": ["넙다리둘레", "허벅지둘레"],
    "calf": ["장딴지둘레", "종아리둘레"],
    "arm": ["위팔둘레", "팔뚝둘레"],
    "shoulder": ["어깨가쪽사이길이", "어깨사이길이", "어깨너비"],
    # 바지길이(아웃심) 유도용 높이 항목 (설계 2-1절 — 아웃심 정의 채택)
    "waist_height": ["허리높이"],
    "ankle_height": ["가쪽복사높이", "발목높이"],
    # 아웃심 유도와 후속 비율 분석을 위한 보조 높이 항목. 현재
    # body_measurement의 시각적 비율 target을 이 파서에서 직접 계산하지는 않는다.
    "sitting_height": ["앉은키"],
}

# 필수 필드 — 하나라도 없으면 해당 레코드 제외 (pants_length는 유도 후 판정)
REQUIRED_FIELDS = [
    "gender", "height", "weight",
    "head", "chest", "waist", "hip", "thigh", "calf", "arm", "shoulder",
]

# 이상치 필터 범위 (api의 body_constants.HEIGHT_RANGE/WEIGHT_RANGE와 동일)
HEIGHT_RANGE = (120.0, 210.0)
WEIGHT_RANGE = (30.0, 150.0)

_MALE_TOKENS = {"남", "남성", "남자", "m", "male", "1"}
_FEMALE_TOKENS = {"여", "여성", "여자", "f", "female", "2"}

# 가쪽복사높이(발목 복사뼈)가 없을 때의 근사값 (cm) — 실측 확인 전 임시
_DEFAULT_ANKLE_HEIGHT_CM = 7.0


def map_columns(columns: list[str]) -> dict[str, str]:
    """실제 컬럼명 → 표준 필드 매핑. 후보 부분문자열 매칭 (짧은 컬럼명 우선).

    '허리둘레'가 '배꼽수준허리둘레'보다 우선하도록, 매칭 후보 중 컬럼명이
    가장 짧은 것을 고른다.
    """
    mapping: dict[str, str] = {}
    for field, candidates in COLUMN_CANDIDATES.items():
        for candidate in candidates:
            matches = [c for c in columns if candidate in str(c)]
            if matches:
                mapping[field] = min(matches, key=lambda c: len(str(c)))
                break
    return mapping


def parse_gender(value: Any) -> str | None:
    token = str(value).strip().lower()
    token = re.sub(r"[.0]+$", "", token) or token  # "1.0" → "1"
    if token in _MALE_TOKENS:
        return "male"
    if token in _FEMALE_TOKENS:
        return "female"
    return None


def _to_cm(series: pd.Series) -> pd.Series:
    """mm 단위 감지 → cm 변환. 사이즈코리아 원본은 항목별로 mm/cm가 혼재한다."""
    numeric = pd.to_numeric(series, errors="coerce")
    median = numeric.median()
    if pd.notna(median) and median > 250:  # 키·둘레가 250을 넘으면 mm로 판단
        return numeric / 10.0
    return numeric


def iter_records(
    xlsx_path: str | Path, *, survey: str, sheet_name: int | str = 0
) -> Iterator[dict]:
    """xlsx 1개 → 표준 레코드 순회. 결측·이상치 레코드는 건너뛴다."""
    path = Path(xlsx_path)
    frame = pd.read_excel(path, sheet_name=sheet_name)
    mapping = map_columns(list(frame.columns))

    missing = [f for f in REQUIRED_FIELDS if f not in mapping]
    if missing:
        raise ValueError(
            f"{path.name}: 필수 컬럼 매칭 실패 {missing} — "
            "--dump-headers로 실제 컬럼명을 확인해 COLUMN_CANDIDATES를 갱신하세요."
        )

    numeric_fields = [f for f in mapping if f not in ("gender", "age")]
    converted = {f: _to_cm(frame[mapping[f]]) for f in numeric_fields}

    kept = 0
    for idx, row in frame.iterrows():
        gender = parse_gender(row[mapping["gender"]])
        if gender is None:
            continue

        record: dict = {"gender": gender, "survey": survey,
                        "source_row_id": f"{path.name}:{idx}"}
        if "age" in mapping:
            age = pd.to_numeric(row[mapping["age"]], errors="coerce")
            record["age"] = int(age) if pd.notna(age) else None
        else:
            record["age"] = None

        for f in numeric_fields:
            value = converted[f].iloc[idx] if isinstance(idx, int) else None
            record[f] = round(float(value), 1) if pd.notna(value) else None

        record["pants_length"] = _derive_pants_length(record)

        if any(record.get(f) is None for f in REQUIRED_FIELDS + ["pants_length"]):
            continue
        if not (HEIGHT_RANGE[0] <= record["height"] <= HEIGHT_RANGE[1]):
            continue
        if not (WEIGHT_RANGE[0] <= record["weight"] <= WEIGHT_RANGE[1]):
            continue

        kept += 1
        yield record

    logger.info("%s: %d행 중 %d건 정규화", path.name, len(frame), kept)


def _derive_pants_length(record: dict) -> float | None:
    """바지길이(아웃심) = 허리높이 − 가쪽복사높이 (설계 2-1절).

    가쪽복사높이가 없으면 근사 상수로 대체한다 (실측 확인 전 임시 — 10절 4번).
    """
    waist_height = record.get("waist_height")
    if waist_height is None:
        return None
    ankle = record.get("ankle_height")
    if ankle is None:
        ankle = _DEFAULT_ANKLE_HEIGHT_CM
    value = waist_height - ankle
    return round(value, 1) if value > 0 else None
