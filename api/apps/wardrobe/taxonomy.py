"""옷장 태그 체계 상수 (Confluence 카테고리-태그 매핑 문서 기준).

test/common/taxonomy.py의 한글 라벨을 API 검증용으로 발췌했다.
저장·필터에는 한글 라벨만 쓴다. 캡셔닝(이미지 프로세서)도 같은 라벨을 반환한다.
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


def is_valid_pair(category_large: str, category_small: str) -> bool:
    """대분류-소분류 짝 정합성 검사."""
    return category_small in CATEGORY_SMALL.get(category_large, [])


SINGLE_SLOT_LARGE: set[str] = {"하의", "신발", "원피스/세트"}
SINGLE_SLOT_SMALL: set[str] = {"모자"}


def get_slot_key(category_large: str, category_small: str = "") -> str | None:
    """아이템이 단일 슬롯(착장당 1개 제한) 대상인 경우 슬롯 키를 반환한다."""
    if category_small in SINGLE_SLOT_SMALL:
        return category_small
    if category_large in SINGLE_SLOT_LARGE:
        return category_large
    return None
