"""카테고리·태그 체계 상수.

원천: Confluence "의류 상품 데이터 카테고리-태그 매핑 문서"
(https://jjeoe0317.atlassian.net/wiki/spaces/SKN281team/pages/14286849)

FashionSigLIP 텍스트 타워는 영어 중심이므로, 각 enum은
{한글 라벨(저장값): 영어 프롬프트(제로샷 분류용)} 매핑으로 관리한다.
저장·필터에는 한글 라벨만 쓰고, 영어 프롬프트는 분류 시점에만 사용한다.
"""

# ── 대분류 ──────────────────────────────────────────────
CATEGORY_LARGE: dict[str, str] = {
    "상의": "a top, upper body clothing",
    "하의": "bottoms, lower body clothing like pants or a skirt",
    "아우터": "outerwear, a jacket or coat worn over other clothes",
    "원피스/세트": "a dress or one-piece outfit",
    "신발": "shoes, footwear",
    "가방": "a bag",
    "액세서리": "a fashion accessory",
    "언더웨어/이너웨어": "underwear or inner wear",
}

# ── 소분류 (대분류별) ────────────────────────────────────
CATEGORY_SMALL: dict[str, dict[str, str]] = {
    "상의": {
        "티셔츠": "a t-shirt",
        "셔츠/블라우스": "a shirt or blouse",
        "니트/스웨터": "a knit sweater or knit vest",
        "후드/맨투맨": "a hoodie or sweatshirt",
        "민소매": "a sleeveless top or tank top",
    },
    "하의": {
        "데님 팬츠": "denim jeans",
        "슬랙스": "slacks, dress pants",
        "코튼 팬츠": "cotton chino pants or cargo pants",
        "트레이닝 팬츠": "jogger pants or sweatpants",
        "숏팬츠": "short pants, shorts",
        "스커트": "a skirt",
        "레깅스": "leggings",
    },
    "아우터": {
        "자켓": "a jacket or blazer",
        "코트": "a coat or trench coat",
        "패딩": "a padded puffer jacket",
        "점퍼/블루종": "a jumper, windbreaker or blouson",
        "가디건": "a cardigan",
        "후드집업": "a zip-up hoodie",
        "베스트": "a padded or fur vest worn as outerwear",
    },
    "원피스/세트": {
        "원피스": "a dress",
        "점프수트/오버롤": "a jumpsuit or overalls",
        "셋업": "a matching set-up suit",
        "파자마/홈웨어 세트": "a pajama or loungewear set",
    },
    "신발": {
        "스니커즈": "sneakers",
        "구두/로퍼": "dress shoes, loafers or heels",
        "부츠": "boots",
        "샌들/슬리퍼": "sandals or slippers",
        "플랫/단화": "flat shoes",
    },
    "가방": {
        "백팩": "a backpack",
        "크로스백": "a crossbody bag",
        "숄더백": "a shoulder bag",
        "토트백": "a tote bag",
        "에코백": "a canvas eco bag",
        "클러치/파우치": "a clutch bag or pouch",
        "지갑": "a wallet",
    },
    "액세서리": {
        "모자": "a hat or cap",
        "벨트": "a belt",
        "주얼리": "jewelry, a necklace, earrings or a ring",
        "머플러/스카프": "a muffler or scarf",
        "양말": "socks",
        "안경/선글라스": "glasses or sunglasses",
        "헤어 액세서리": "a hair accessory",
    },
    "언더웨어/이너웨어": {
        "브라": "a bra",
        "팬티/드로즈": "underwear briefs",
        "런닝/캐미솔": "an inner sleeveless camisole",
        "속바지": "inner shorts",
        "보정속옷": "shapewear",
        "내복/발열 이너": "thermal inner wear",
    },
}

# ── 유동 태그 후보군 ─────────────────────────────────────
STYLES: dict[str, str] = {
    "캐주얼": "casual everyday style",
    "포멀": "formal dressy style",
    "미니멀": "minimal clean style",
    "스트릿": "street fashion style",
    "스포티": "sporty athletic style",
    "러블리": "lovely cute style",
    "페미닌": "feminine style",
    "시크": "chic sophisticated style",
    "빈티지": "vintage retro style",
    "아웃도어": "outdoor functional style",
    "댄디": "dandy neat masculine style",
    "아메카지": "amekaji workwear casual style",
    "트렌디": "trendy fashionable style",
    "리조트": "resort vacation style",
    "베이직": "basic timeless style",
}

COLORS: dict[str, str] = {
    "화이트": "white color",
    "블랙": "black color",
    "그레이": "gray color",
    "네이비": "navy blue color",
    "블루": "blue color",
    "스카이블루": "light sky blue color",
    "레드": "red color",
    "핑크": "pink color",
    "오렌지": "orange color",
    "옐로우": "yellow color",
    "그린": "green color",
    "카키": "khaki olive color",
    "브라운": "brown color",
    "베이지": "beige color",
    "아이보리": "ivory cream color",
    "퍼플": "purple color",
    "멀티": "multicolor",
}

PATTERNS: dict[str, str] = {
    "무지": "solid plain, no pattern",
    "체크": "check plaid pattern",
    "스트라이프": "stripe pattern",
    "도트": "polka dot pattern",
    "플로럴": "floral pattern",
    "그래픽/로고": "graphic print or logo print",
    "카모": "camouflage pattern",
    "애니멀": "animal print",
}

FITS: dict[str, str] = {
    "오버핏": "oversized loose fit",
    "레귤러핏": "regular standard fit",
    "슬림핏": "slim tight fit",
    "와이드핏": "wide fit",
}

MATERIALS: dict[str, str] = {
    "코튼": "cotton fabric",
    "데님": "denim fabric",
    "니트": "knitted fabric",
    "울": "wool fabric",
    "린넨": "linen fabric",
    "레더": "leather material",
    "나일론": "nylon fabric",
    "폴리에스터": "polyester fabric",
    "시폰": "sheer chiffon fabric",
    "코듀로이": "corduroy fabric",
    "트위드": "tweed fabric",
    "퍼/무스탕": "fur or shearling material",
    "패딩충전재": "padded quilted material",
}

SLEEVES: dict[str, str] = {
    "반팔": "short sleeves",
    "긴팔": "long sleeves",
    "민소매": "sleeveless",
}

LENGTHS: dict[str, str] = {
    "크롭": "cropped short length",
    "기본": "regular length",
    "롱": "long length",
}

SEASONS = ["봄", "여름", "가을", "겨울", "간절기"]

# ── 필드 적용 범위 (문서 5-2 필수/선택 태그 기준) ─────────
# 시각적으로 판단 불가한 usage는 기본값으로 두고 이후 사용자/LLM 보정.
DEFAULT_USAGE = ["데일리", "외출"]

CLOTHING_LARGE = {"상의", "하의", "아우터", "원피스/세트"}  # sleeve/fit/length 적용 대상
SLEEVE_TARGET = {"상의", "아우터", "원피스/세트"}

# ── layer_role / layer_order 규칙 (문서 5-3) ─────────────
def infer_layer(category_large: str, category_small: str, sleeve: str | None) -> tuple[str | None, int | None]:
    """카테고리 → 레이어드 역할. 니트 베스트(민소매 니트)는 레이어드 상의로 본다."""
    if category_large == "아우터":
        return "아우터", 3
    if category_large == "상의":
        if category_small == "니트/스웨터" and sleeve == "민소매":
            return "레이어드 상의", 2
        return "기본 상의", 1
    if category_large == "원피스/세트":
        return "기본 상의", 1
    return None, None


# ── season 유도 규칙 (문서 3-1·3-3) ──────────────────────
# 계절은 사진에서 직접 보이지 않으므로 소분류·소매·소재 기반 규칙으로 유도한다.
def infer_season(category_large: str, category_small: str,
                 sleeve: str | None, material: str | None) -> list[str]:
    if category_small in {"패딩", "부츠"} or material in {"울", "퍼/무스탕", "패딩충전재"}:
        return ["겨울"]
    if category_small == "코트":
        return ["가을", "겨울"]
    if sleeve in {"반팔", "민소매"} or material == "린넨" \
            or category_small in {"숏팬츠", "샌들/슬리퍼", "민소매", "레깅스"}:
        return ["여름"]
    if category_small in {"니트/스웨터", "후드/맨투맨"}:
        return ["가을", "겨울", "간절기"]
    if category_large in {"가방", "액세서리"} and category_small != "머플러/스카프":
        return ["봄", "여름", "가을", "겨울"]
    if category_small == "머플러/스카프":
        return ["가을", "겨울"]
    return ["봄", "가을", "간절기"]
