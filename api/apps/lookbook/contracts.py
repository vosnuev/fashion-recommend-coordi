"""룩북 API와 이미지 프로세서가 공유하는 계약 상수.

캘린더(style_calendar)와 같은 이름·같은 값을 쓴다. 두 도메인이 같은 옷장
업로드 파이프라인을 태우기 때문에 상태 문자열이 갈리면 프론트가 화면마다
다른 분기를 들고 있어야 한다. 그렇다고 캘린더의 것을 import하지는 않는다 —
룩북은 날짜가 없어도 되는 별도 도메인이라, 한쪽 상태가 늘어날 때 다른 쪽이
따라 늘어나야 하는 결합을 만들지 않는다.
"""

from enum import StrEnum


RECOMMENDATION_CARD_LOOKBOOK_PREFIX = "recommendation-card:"


def recommendation_card_lookbook_id(card_id: object) -> str:
    """추천 카드 스냅샷을 기존 서버 추천 룩 저장 경로에서 구분하는 안정 ID."""

    return f"{RECOMMENDATION_CARD_LOOKBOOK_PREFIX}{card_id}"


def recommendation_card_id_from_lookbook(value: str) -> str:
    """추천 카드 룩북 ID가 아니면 빈 문자열을 반환한다."""

    if not value.startswith(RECOMMENDATION_CARD_LOOKBOOK_PREFIX):
        return ""
    return value.removeprefix(RECOMMENDATION_CARD_LOOKBOOK_PREFIX)


class LookbookStatus(StrEnum):
    """룩북 이미지 처리 상태."""

    REGISTERED = "REGISTERED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class LookbookSourceType(StrEnum):
    """룩북 등록 경로.

    GOLDEN_LOOK — 오늘의 룩 카드에서 '저장'으로 담은 골든 코디. 사용자가 올린
    사진도, 사용자 옷장의 옷도 아니라서 옷장 업로드 파이프라인을 타지 않는다.
    이미지는 골든셋 버킷에 이미 있는 것을 **가리키기만** 한다(복사하지 않는다).
    """

    PHOTO_UPLOAD = "PHOTO_UPLOAD"
    WARDROBE_SELECTED = "WARDROBE_SELECTED"
    GOLDEN_LOOK = "GOLDEN_LOOK"


class LookbookLinkType(StrEnum):
    """룩북에 옷장 아이템이 붙은 경로.

    SELECTED  — 사용자가 '입은 옷'으로 직접 고른 아이템
    EXTRACTED — 룩 사진에서 이미지 프로세서가 새로 뽑아 등록한 아이템
    GOLDEN    — 오늘의 룩에서 담은 골든 코디의 구성 아이템. 사용자 옷장의 옷이
                아니므로 wardrobe_item은 NULL이고 snapshot만 남는다
    """

    SELECTED = "SELECTED"
    EXTRACTED = "EXTRACTED"
    GOLDEN = "GOLDEN"


class LookbookProcessingErrorCode(StrEnum):
    """룩 사진 처리의 표준 전체 실패 코드."""

    QUEUE_ENQUEUE_FAILED = "QUEUE_ENQUEUE_FAILED"
    NO_ITEM_EXTRACTED = "NO_ITEM_EXTRACTED"
    IMAGE_PROCESSING_FAILED = "IMAGE_PROCESSING_FAILED"


#: 룩북 필터 태그 어휘. 둘러보기의 필터 칩이자, 오늘의 룩 카드가 다는 태그다.
#:
#: **여기가 단일 정의다.** 지금까지는 mobile/src/state/lookbook.ts 의
#: ALLOWED_HASHTAGS 에만 있어서, 백엔드는 어떤 문자열이 룩북 태그인지 판정할
#: 방법이 없었다(해시태그 시리얼라이저도 CharField 검사만 한다). 오늘의 룩이
#: 같은 어휘로 태그를 만들려면 서버 쪽에 기준이 있어야 한다.
#:
#: 순서는 프론트 칩 배열과 같게 유지한다 — 두 화면의 나열 순서가 달라지면
#: 같은 어휘인데 다른 목록처럼 보인다.
LOOKBOOK_TAGS: tuple[str, ...] = (
    "출근", "데이트", "나들이", "여행",
    "미니멀", "캐주얼", "빈티지", "스트릿",
    "하객룩",
)

#: 어휘를 두 갈래로 나눠 둔다. 골든 코디 payload 에서 **TPO 는 occasion,
#: 스타일은 style** 이라는 서로 다른 축에서 오기 때문에, 한 덩어리로 두면
#: occasion 에서 온 "미니멀" 같은 잘못된 값도 통과시킨다.
LOOKBOOK_TPO_TAGS: frozenset[str] = frozenset({"출근", "데이트", "나들이", "여행", "하객룩"})
LOOKBOOK_STYLE_TAGS: frozenset[str] = frozenset({"미니멀", "캐주얼", "빈티지", "스트릿"})
