"""11번가 카테고리 경로를 추천 시스템 공통 분류로 매핑한다."""

from __future__ import annotations

from typing import Iterable, Optional

from naver.category_mapping import map_naver_category

from config import CATEGORY_MAPPING_VERSION

Mapping = tuple[str, str]

# 11번가 카테고리명이 네이버와 다른 대표 항목만 먼저 보정한다.
ELEVEN_CATEGORY_MAP: dict[str, Mapping] = {
    "여성티셔츠": ("상의", "티셔츠"),
    "남성티셔츠": ("상의", "티셔츠"),
    "여성셔츠": ("상의", "셔츠/블라우스"),
    "남성셔츠": ("상의", "셔츠/블라우스"),
    "여성블라우스": ("상의", "셔츠/블라우스"),
    "여성니트": ("상의", "니트/스웨터"),
    "남성니트": ("상의", "니트/스웨터"),
    "여성청바지": ("하의", "데님 팬츠"),
    "남성청바지": ("하의", "데님 팬츠"),
    "여성자켓": ("아우터", "자켓"),
    "남성자켓": ("아우터", "자켓"),
    "여성재킷": ("아우터", "자켓"),
    "남성재킷": ("아우터", "자켓"),
    "여성운동화": ("신발", "스니커즈"),
    "남성운동화": ("신발", "스니커즈"),
}


def normalize(value: Optional[str]) -> str:
    return (value or "").replace(" ", "").strip()


def map_eleven_category(category_path: Iterable[str]) -> tuple[
    Optional[str], Optional[str], Optional[str]
]:
    """최하위 카테고리부터 매핑하고 성공 시 매핑 버전도 반환한다."""
    names = [name.strip() for name in category_path if name and name.strip()]
    for name in reversed(names):
        direct = ELEVEN_CATEGORY_MAP.get(normalize(name))
        if direct:
            return direct[0], direct[1], CATEGORY_MAPPING_VERSION

        # 공통 분류 어휘는 네이버 카테고리 매핑과 동일하게 유지한다.
        large, small = map_naver_category("패션의류", None, name, name)
        if large and small:
            return large, small, CATEGORY_MAPPING_VERSION
    return None, None, None
