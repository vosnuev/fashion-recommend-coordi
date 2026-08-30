"""옷장 태그 체계 상수 (Confluence 카테고리-태그 매핑 문서 v4 기준).

test/test-llm2/common/taxonomy.py 및 api/apps/wardrobe/taxonomy.py와
동일한 라벨을 사용한다 — 콜백 검증(CallbackSerializer)이 이 값 기준이다.
"""
from __future__ import annotations

CATEGORY_LARGE = [
    "상의", "하의", "아우터", "원피스/세트",
    "신발", "가방", "액세서리", "언더웨어/이너웨어",
]

CATEGORY_SMALL: dict[str, list[str]] = {
    "상의": ["티셔츠", "셔츠/블라우스", "니트/스웨터", "후드/맨투맨", "민소매"],
    "하의": ["데님 팬츠", "슬랙스", "코튼 팬츠", "트레이닝 팬츠",
             "숏팬츠", "스커트", "레깅스"],
    "아우터": ["자켓", "코트", "패딩", "점퍼/블루종", "가디건", "후드집업", "베스트"],
    "원피스/세트": ["원피스", "점프수트/오버롤", "셋업", "파자마/홈웨어 세트"],
    "신발": ["스니커즈", "구두/로퍼", "부츠", "샌들/슬리퍼", "플랫/단화"],
    "가방": ["백팩", "크로스백", "숄더백", "토트백", "에코백", "클러치/파우치", "지갑"],
    "액세서리": ["모자", "벨트", "주얼리", "머플러/스카프", "양말",
                 "안경/선글라스", "헤어 액세서리"],
    "언더웨어/이너웨어": ["브라", "팬티/드로즈", "런닝/캐미솔", "속바지",
                          "보정속옷", "내복/발열 이너"],
}

STYLES = ["캐주얼", "포멀", "미니멀", "스트릿", "스포티", "러블리", "페미닌",
          "시크", "빈티지", "아웃도어", "댄디", "아메카지", "트렌디", "리조트", "베이직"]
COLORS = ["화이트", "블랙", "그레이", "네이비", "블루", "스카이블루", "레드", "핑크",
          "오렌지", "옐로우", "그린", "카키", "브라운", "베이지", "아이보리", "퍼플", "멀티"]
PATTERNS = ["무지", "체크", "스트라이프", "도트", "플로럴", "그래픽/로고", "카모", "애니멀"]
FITS = ["오버핏", "레귤러핏", "슬림핏", "와이드핏"]
MATERIALS = ["코튼", "데님", "니트", "울", "린넨", "레더", "나일론", "폴리에스터",
             "시폰", "코듀로이", "트위드", "퍼/무스탕", "패딩충전재"]
SLEEVES = ["반팔", "긴팔", "민소매"]
LENGTHS = ["크롭", "기본", "롱"]
SEASONS = ["봄", "여름", "가을", "겨울", "간절기"]
LAYER_ROLES = ["기본 상의", "레이어드 상의", "아우터"]

ALL_SMALL = [s for smalls in CATEGORY_SMALL.values() for s in smalls]

# 대분류별 필수 필드 (문서 §5-2) — manifest 검증용
REQUIRED_BY_LARGE = {
    "상의": ["fit", "sleeve", "length"],
    "하의": ["fit", "length"],
    "아우터": ["fit", "sleeve", "length"],
    "원피스/세트": ["fit", "sleeve", "length"],
}
COMMON_REQUIRED = ["item_name", "category_large", "category_small",
                   "season", "style", "color", "pattern"]


def is_valid_pair(category_large: str, category_small: str) -> bool:
    return category_small in CATEGORY_SMALL.get(category_large, [])


def fix_category_pair(tags: dict) -> dict:
    """enum이 대분류-소분류 짝까지 강제하지 못하므로 사후 보정한다."""
    small = tags.get("category_small")
    if small and not is_valid_pair(tags.get("category_large", ""), small):
        for large, smalls in CATEGORY_SMALL.items():
            if small in smalls:
                tags["category_large"] = large
                break
    return tags


def missing_required(tags: dict) -> list[str]:
    """필수 필드 누락 목록 (manifest의 검증 결과로 기록)."""
    missing = [f for f in COMMON_REQUIRED if not tags.get(f)]
    for f in REQUIRED_BY_LARGE.get(tags.get("category_large", ""), []):
        if not tags.get(f):
            missing.append(f)
    return missing
