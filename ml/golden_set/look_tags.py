"""코디 태그 어휘와 Gemini 스키마.

`tag_manifests`와 `sync_qdrant`가 공유한다. 어휘를 한 곳에 두는 이유는, 이 값이
사람이 읽는 설명이 아니라 **리트리버가 필터로 쓰는 키**이기 때문이다. 표기가
갈리면 예외 없이 조용히 검색에서 빠진다.

같은 이유로 프롬프트에 "예시"를 나열하지 않고 responseSchema의 enum으로 못 박는다.
예시는 인접한 새 값을 만들어 낼 여지를 남긴다.

style·season은 image-processor의 taxonomy에서 **import한다.** 복제하면 태그 체계가
바뀔 때 조용히 갈라진다. occasion만 이 프로젝트에 어휘가 없어 여기서 정의한다.
"""

from __future__ import annotations

from typing import Any

#: 태그 계약 버전. 프롬프트나 어휘를 바꾸면 올린다 — 올려야 다음 실행이
#: 기존 manifest를 다시 태깅한다.
LOOK_TAG_SCHEMA_VERSION = "golden-look-tags-v1"

#: 성별 표현 그룹. 사람의 정체성이 아니라 **착장의 표현**을 가리킨다.
#: 리트리버가 하드 필터로 쓴다 (api/apps/recommend/services/retriever.py).
PRESENTATION_GROUPS = ["men", "women", "unisex"]

#: "판단 못 함"을 나타내는 값. 저장할 때는 빈 문자열로 되돌린다.
#:
#: 빈 문자열을 enum에 직접 넣을 수 없다 — Gemini가 400으로 거부한다:
#:   response_schema.properties[presentation_group].enum[3]: cannot be empty
#: 그래서 모델에게는 명시적인 단어를 주고, normalize()가 저장 형태로 옮긴다.
#: 미분류를 unisex로 흘리지 않으려면 이 경로가 반드시 있어야 한다.
PRESENTATION_UNKNOWN = "unknown"

#: 착용 상황. style·season과 달리 기존 어휘가 없어 여기서 정한다.
#: 이 값이 곧 앱 화면의 선택지이자 RetrievalRequest.occasion에 들어갈 값이므로,
#: 프론트 선택지를 바꾸면 여기도 함께 바꿔야 한다.
#: metadata.example.csv의 '데일리'·'출근'을 출발점으로 확장했다.
OCCASIONS = [
    "데일리",
    "출근",
    "데이트",
    "나들이",
    "여행",
    "운동",
    "모임",
    "행사",
    "홈웨어",
]

#: 한 코디에 붙일 수 있는 다중값 상한. 열어두면 style에 8개가 붙어 변별력이 사라진다.
MAX_VALUES_PER_AXIS = 3


def _taxonomy() -> tuple[list[str], list[str]]:
    """image-processor의 태그 어휘를 가져온다.

    골든셋 이미지에는 image-processor가 함께 들어 있다(ml/golden_set/Dockerfile).
    import에 실패하면 값을 지어내지 않고 그대로 실패시킨다 — 어휘가 갈린 채로
    태깅하면 그 결과는 검색에서 통째로 빠진다.
    """
    from pipeline.taxonomy import SEASONS, STYLES

    return list(STYLES), list(SEASONS)


def build_schema() -> dict[str, Any]:
    styles, seasons = _taxonomy()
    return {
        "type": "object",
        "properties": {
            "presentation_group": {
                "type": "string",
                "enum": [*PRESENTATION_GROUPS, PRESENTATION_UNKNOWN],
                "description": f"착장의 성별 표현. 확신이 없으면 '{PRESENTATION_UNKNOWN}'.",
            },
            "style": {
                "type": "array",
                "items": {"type": "string", "enum": styles},
                "description": f"최대 {MAX_VALUES_PER_AXIS}개.",
            },
            "season": {
                "type": "array",
                "items": {"type": "string", "enum": seasons},
                "description": f"착용하기 좋은 계절. 최대 {MAX_VALUES_PER_AXIS}개.",
            },
            "occasion": {
                "type": "array",
                "items": {"type": "string", "enum": OCCASIONS},
                "description": f"어울리는 상황. 최대 {MAX_VALUES_PER_AXIS}개.",
            },
            "confidence": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "1=낮음, 2=보통, 3=높음. 사람 검수 우선순위에 쓴다.",
            },
            "note": {"type": "string", "description": "판단이 애매한 이유 (없으면 빈 문자열)"},
        },
        "required": ["presentation_group", "style", "season", "occasion", "confidence"],
    }


SYSTEM_INSTRUCTION = (
    "당신은 패션 코디 사진에 검색용 태그를 붙이는 분류기입니다.\n"
    "규칙:\n"
    "1. 주어진 목록 밖의 값을 만들지 않습니다.\n"
    "2. presentation_group은 **착장의 표현**을 뜻합니다. 사진 속 인물의 외모나 "
    "정체성을 판단하지 말고, 그 옷차림이 통상 남성복/여성복/공용 중 어디로 "
    "유통되는지로 고릅니다. 애매하면 'unknown'을 씁니다.\n"
    "3. 인물의 외모·체형·나이·인종에 대해 어떤 판단도 하지 않습니다.\n"
    "4. 축마다 최대 3개까지만 고릅니다. 많이 고를수록 검색에서 쓸모가 없어집니다.\n"
    "5. season은 그 옷차림으로 지내기 적당한 계절입니다. 얇은 겉옷처럼 "
    "봄·가을 양쪽에 맞으면 둘 다와 '간절기'를 함께 고릅니다.\n"
    "6. 확실하지 않으면 적게 고르거나 비웁니다. 억지로 채우면 검색이 틀립니다."
)

PROMPT = (
    "이 전신 코디 사진에 검색용 태그를 붙여 주세요. "
    "의상의 종류·실루엣·소재감·색 조합을 근거로 판단합니다."
)


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """모델 응답을 저장 형태로 다듬는다.

    스키마 enum이 값을 강제하지만 개수까지는 막지 못한다. 상한을 넘으면 자르고,
    중복은 순서를 지키며 제거한다.
    """
    styles, seasons = _taxonomy()
    allowed = {"style": set(styles), "season": set(seasons), "occasion": set(OCCASIONS)}

    result: dict[str, Any] = {}
    for axis, valid in allowed.items():
        seen: list[str] = []
        for value in raw.get(axis) or []:
            text = str(value).strip()
            # enum을 벗어난 값은 버린다. 남겨두면 검색에서 영원히 안 걸리는
            # 유령 태그가 payload에 남는다.
            if text in valid and text not in seen:
                seen.append(text)
        result[axis] = seen[:MAX_VALUES_PER_AXIS]

    # 모델은 'unknown'을 돌려주지만 저장 형태는 빈 문자열이다. 리트리버는
    # 빈 값을 "라벨 없음"으로 읽어 성별 필터에서 제외한다.
    group = str(raw.get("presentation_group", "")).strip().lower()
    result["presentation_group"] = group if group in PRESENTATION_GROUPS else ""

    try:
        confidence = int(raw.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    result["confidence"] = min(max(confidence, 0), 3)
    result["note"] = str(raw.get("note", "")).strip()
    return result
