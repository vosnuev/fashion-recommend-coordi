"""ETRI 패션 코디 데이터셋(11번) 로더.

S3에 있는 원본을 그대로 읽는다 (로컬 사본 없음).

mdata.wst.txt 포맷 (EUC-KR, 탭 구분):
    아이템ID  구분  카테고리  속성  설명
    BL-001    T    BL       F    단추 여밈 의 전체 오픈형

- 구분: T 상의 | B 하의 | O 아우터 | S 신발
- 속성: F 형태 | M 소재 | C 색상 | E 감성
- 컬럼이 5개 미만인 줄은 직전 설명의 이어짐(줄바꿈)으로 처리한다.
- 설명 텍스트는 형태소 단위로 띄어쓰기 되어 있고 '_'로 붙임 표시가 있어
  임베딩 전에 정리한다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import boto3

from .config import ETRI11_IMG_PREFIX, ETRI11_MDATA_KEY, S3_BUCKET

logger = logging.getLogger(__name__)

PART_LABELS = {"T": "상의", "B": "하의", "O": "아우터", "S": "신발"}
CATEGORY_LABELS = {
    "BL": "블라우스",
    "CD": "가디건",
    "CT": "코트",
    "JK": "재킷",
    "JP": "점퍼",
    "KN": "니트",
    "OP": "원피스",
    "PT": "팬츠",
    "SE": "신발",
    "SH": "셔츠",
    "SK": "스커트",
    "SW": "스웨터",
    "VT": "베스트",
}
ASPECT_LABELS = {"F": "형태", "M": "소재", "C": "색상", "E": "감성"}


@dataclass
class FashionItem:
    item_id: str  # 예: BL-001
    part: str = ""  # T | B | O | S
    category: str = ""  # BL | CD | ...
    # 속성코드(F/M/C/E) → 설명 목록
    features: dict[str, list[str]] = field(default_factory=dict)
    image_key: str = ""  # S3 키 (없으면 빈 문자열)

    @property
    def category_ko(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def part_ko(self) -> str:
        return PART_LABELS.get(self.part, self.part)

    def embedding_text(self) -> str:
        """텍스트 임베딩 입력. SigLIP 텍스트 인코더의 컨텍스트(64 토큰)가 짧아
        중요한 순서(카테고리 → 색상 → 감성 → 형태 → 소재)로 배치한다."""
        parts = [self.category_ko]
        for aspect in ("C", "E", "F", "M"):
            descriptions = self.features.get(aspect)
            if descriptions:
                parts.append(f"{ASPECT_LABELS[aspect]}: " + ", ".join(descriptions))
        return ". ".join(parts)


def _clean_text(text: str) -> str:
    """형태소 분리 표기 정리: '깔끔_한' → '깔끔한', 다중 공백 축소."""
    return " ".join(text.replace("_", "").split())


def parse_mdata(raw: bytes) -> dict[str, FashionItem]:
    """mdata.wst.txt 바이트를 파싱해 {item_id: FashionItem}을 반환한다."""
    items: dict[str, FashionItem] = {}
    last_descriptions: list[str] | None = None
    skipped = 0

    for line in raw.decode("euc-kr", errors="replace").splitlines():
        if not line.strip():
            continue
        columns = line.split("\t")
        if len(columns) < 5:
            # 직전 설명의 줄바꿈 이어짐 (예: 신발 굽 높이 '3.5 CM')
            if last_descriptions:
                last_descriptions[-1] += " " + _clean_text(line)
            else:
                skipped += 1
            continue

        item_id, part, category, aspect = (c.strip() for c in columns[:4])
        description = _clean_text("\t".join(columns[4:]))
        if not item_id or not description:
            skipped += 1
            continue

        item = items.setdefault(
            item_id, FashionItem(item_id=item_id, part=part, category=category)
        )
        descriptions = item.features.setdefault(aspect, [])
        descriptions.append(description)
        last_descriptions = descriptions

    if skipped:
        logger.warning("mdata 파싱 중 %d개 줄을 건너뜀", skipped)
    logger.info("mdata 아이템 %d개 파싱 완료", len(items))
    return items


def load_items(s3_client=None) -> list[FashionItem]:
    """S3에서 mdata와 이미지 목록을 읽어 병합한 아이템 목록을 반환한다.

    - 이미지가 있는 아이템만 적재 대상으로 삼는다 (이미지 벡터가 기본).
    - mdata에 없는 이미지는 텍스트 없이 이미지 벡터만 만든다.
    """
    s3 = s3_client or boto3.client("s3")

    mdata_obj = s3.get_object(Bucket=S3_BUCKET, Key=ETRI11_MDATA_KEY)
    items = parse_mdata(mdata_obj["Body"].read())

    paginator = s3.get_paginator("list_objects_v2")
    image_keys: dict[str, str] = {}
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=ETRI11_IMG_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]  # BL-001.jpg → BL-001
            if stem:
                image_keys[stem] = key

    merged: list[FashionItem] = []
    text_only = 0
    for item_id, key in sorted(image_keys.items()):
        item = items.get(item_id)
        if item is None:
            # 메타데이터 없는 이미지: 카테고리는 파일명 접두사에서 유추
            item = FashionItem(item_id=item_id, category=item_id.split("-")[0])
        item.image_key = key
        merged.append(item)
    text_only = len(items) - sum(1 for m in merged if m.features)

    logger.info(
        "적재 대상 %d개 (이미지 기준) / 이미지 없는 mdata 아이템 %d개 제외",
        len(merged),
        text_only,
    )
    return merged


def download_images(
    items: list[FashionItem], workers: int, s3_client=None
) -> list[bytes | None]:
    """아이템들의 이미지 바이트를 병렬 다운로드한다. 실패는 None."""
    s3 = s3_client or boto3.client("s3")

    def fetch(item: FashionItem) -> bytes | None:
        try:
            return s3.get_object(Bucket=S3_BUCKET, Key=item.image_key)["Body"].read()
        except Exception:
            logger.exception("이미지 다운로드 실패: %s", item.image_key)
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch, items))
