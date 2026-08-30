from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class GeminiConfigurationError(Exception):
    """Gemini 설정이 누락된 경우."""


class GeminiServiceError(Exception):
    """Gemini 호출 또는 응답 처리에 실패한 경우.

    실패한 호출도 DB에 기록하므로, 원인 파악에 쓸 수 있는 원본 응답을 함께 실어 보낸다.
    """

    def __init__(self, message: str = "", *, response_payload: Any = None) -> None:
        super().__init__(message)
        self.response_payload = response_payload


@dataclass(frozen=True)
class GeminiResult:
    """평가 1건의 호출 결과. DB 기록에 필요한 메타를 함께 담는다."""

    evaluation: dict[str, Any]
    response_payload: dict[str, Any]
    model: str
    latency_ms: int


# 저장용 요청 본문에서 이미지 base64 자리에 넣는 표시자.
# 원본 사진은 S3에 있으므로 요청 본문에 base64를 남길 이유가 없다 (행 크기 폭증).
IMAGE_PLACEHOLDER = "<image omitted: {size} bytes, stored in S3>"


EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "코디의 전체 완성도 점수",
        },
        "summary": {"type": "string", "description": "긍정적인 종합 평가"},
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 4,
            "description": "사진에서 확인되는 코디의 구체적인 장점",
        },
        "weather_comment": {
            "type": "string",
            "description": "현재 날씨와 코디의 어울림에 대한 평가",
        },
        "personalization_comment": {
            "type": "string",
            "description": "추구미와 체형 정보가 있을 때 제공하는 개인화 평가",
        },
        "styling_tips": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 3,
            "description": "현재 코디의 장점을 살리는 선택적인 스타일링 팁",
        },
    },
    "required": [
        "overall_score",
        "summary",
        "strengths",
        "weather_comment",
        "personalization_comment",
        "styling_tips",
    ],
}

SYSTEM_INSTRUCTION = """당신은 따뜻하고 전문적인 한국어 패션 스타일리스트입니다.
사진에서 실제로 확인되는 요소와 제공된 컨텍스트만 사용하세요.
코디의 장점을 먼저 구체적으로 찾아 긍정적으로 평가하되, 보이지 않는 의류나 신체 특징을 추측하지 마세요.
개선 제안은 비판이 아니라 현재 장점을 더 살리는 선택적인 팁으로 표현하세요.
성별과 체형 정보는 적합도 판단을 돕는 용도로만 사용하고 외모를 평가하거나 고정관념을 적용하지 마세요.
제공되지 않은 개인화 정보는 없다고 명확히 말하고, 모든 응답은 한국어로 작성하세요."""


def build_prompt(context: dict[str, Any]) -> str:
    return (
        "첨부된 코디 사진을 평가해 주세요. 다음 JSON은 평가에 활용할 컨텍스트입니다. "
        "값이 null이면 해당 항목을 평가에 사용하지 마세요.\n"
        f"{json.dumps(context, ensure_ascii=False, default=str)}"
    )


def _build_request_body(
    context: dict[str, Any],
    *,
    mime_type: str,
    image_data: str,
) -> dict[str, Any]:
    """실제 호출 본문과 저장용 본문을 같은 함수로 만든다 (둘이 어긋나지 않게)."""
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": build_prompt(context)},
                    {"inlineData": {"mimeType": mime_type, "data": image_data}},
                ],
            }
        ],
        # structured output은 responseMimeType + responseSchema로 지정한다
        # (v1beta GenerationConfig에 responseFormat 필드는 없어 400을 받는다).
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
            "responseSchema": EVALUATION_SCHEMA,
        },
    }


def build_request_payload(
    context: dict[str, Any],
    *,
    mime_type: str,
    image_bytes: int,
) -> dict[str, Any]:
    """DB에 남길 요청 본문. 호출 실패 시에도 기록해야 하므로 호출과 분리한다."""
    return _build_request_body(
        context,
        mime_type=mime_type,
        image_data=IMAGE_PLACEHOLDER.format(size=image_bytes),
    )


def _extract_text(payload: dict[str, Any]) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts if "text" in part)
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiServiceError(
            "Gemini 응답에 평가 결과가 없습니다.", response_payload=payload
        ) from exc


def _error_payload(response: requests.Response) -> Any:
    """오류 응답 본문. JSON이 아니면 잘라낸 문자열로 남긴다."""
    try:
        return response.json()
    except ValueError:
        return {"status_code": response.status_code, "text": response.text[:2000]}


def evaluate_outfit(
    image_data: bytes,
    *,
    mime_type: str,
    context: dict[str, Any],
) -> GeminiResult:
    """업로드 파일 객체가 아니라 **읽어 둔 바이트**를 받는다.

    같은 업로드를 S3와 Gemini가 차례로 써야 하는데, boto3 upload_fileobj가
    넘겨받은 파일 객체를 닫아버려 두 번째 읽기가 ValueError로 죽었다.
    호출부에서 한 번만 읽고 바이트를 돌려쓰는 것이 유일하게 안전한 방식이다.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise GeminiConfigurationError("GEMINI_API_KEY가 설정되지 않았습니다.")

    encoded_image = base64.b64encode(image_data).decode("ascii")
    request_body = _build_request_body(
        context, mime_type=mime_type, image_data=encoded_image
    )
    model = settings.GEMINI_MODEL
    url = f"{settings.GEMINI_API_BASE_URL}/v1beta/models/{model}:generateContent"

    started = time.monotonic()
    error_payload: Any = None
    try:
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=request_body,
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            # 잘못된 필드명·스키마 등 실제 사유는 본문에만 담기므로 남긴다
            error_payload = _error_payload(response)
            logger.error(
                "Gemini 호출 실패 %s: %s", response.status_code, response.text[:2000]
            )
        response.raise_for_status()
        response_payload = response.json()
        evaluation = json.loads(_extract_text(response_payload))
    except requests.Timeout as exc:
        # 타임아웃은 대개 사진이 크거나 네트워크가 느린 경우다.
        # 전송 크기를 함께 남겨야 GEMINI_TIMEOUT_SECONDS를 올릴지 판단할 수 있다.
        logger.error(
            "Gemini 타임아웃 %.1fs (limit=%ss, 전송=%dKB)",
            time.monotonic() - started,
            settings.GEMINI_TIMEOUT_SECONDS,
            len(image_data) // 1024,
        )
        raise GeminiServiceError("Gemini 응답 시간이 초과되었습니다.") from exc
    except GeminiServiceError:
        # _extract_text가 이미 원본 응답을 실어 던진 경우 — 덮어쓰지 않는다
        raise
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        logger.exception("Gemini 코디 평가 호출 실패")
        raise GeminiServiceError(
            "Gemini 코디 평가에 실패했습니다.", response_payload=error_payload
        ) from exc

    return GeminiResult(
        evaluation=evaluation,
        response_payload=response_payload,
        model=model,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


# ────────────────────────────────────────────────────────────
# 오늘의 룩
# ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DailyLookResult:
    """오늘의 룩 조합 1건의 호출 결과."""

    parsed: dict[str, Any]
    request: dict[str, Any]
    response: dict[str, Any]
    model: str
    latency_ms: int


DAILY_LOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "한 줄 요약. 20자 내외의 자연스러운 한국어.",
        },
        "rationale_ko": {
            "type": "string",
            "description": "왜 이 코디인지. 체형 근거와 날씨를 함께 언급한다.",
        },
        "styling_tips": {
            "type": "array",
            "items": {"type": "string"},
            "description": "착장 팁 1~3개.",
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_key": {"type": "string"},
                    "note": {"type": "string", "description": "이 아이템을 고른 이유"},
                },
                "required": ["item_key"],
            },
        },
    },
    "required": ["headline", "rationale_ko"],
}

DAILY_LOOK_SYSTEM_INSTRUCTION = (
    "당신은 한국어로 답하는 패션 스타일리스트입니다. "
    "**이미 정해진** 오늘의 착장에 설명을 붙이는 일을 합니다.\n"
    "규칙:\n"
    "1. 코디를 고르거나 바꾸지 않습니다. 다른 코디를 제안하지 않습니다.\n"
    "2. rule_notes는 사용자 체형에 근거한 스타일링 원칙입니다. 그대로 옮기지 말고 "
    "자연스러운 문장으로 풀어 씁니다.\n"
    "3. 체형을 지적하거나 평가하지 않습니다. '단점을 가린다'가 아니라 "
    "'균형을 살린다'처럼 씁니다.\n"
    "4. 날씨 정보가 있으면 기온에 맞는 레이어링을 한 문장으로 덧붙입니다. "
    "기온은 섭씨이며, 주어진 값과 기온대 판정을 다르게 표현하지 않습니다. "
    "착장이 그 기온에 다소 맞지 않으면 억지로 맞다고 쓰지 말고 "
    "'겉옷은 벗어 들고 다녀도 좋아요' 처럼 실용적인 안내로 풀어 씁니다.\n"
    "5. items의 note는 주어진 item_key에 대해서만 씁니다. 없는 아이템을 만들지 않습니다."
)


def build_daily_look_prompt(
    *, outfit: dict[str, Any], context: dict[str, Any]
) -> str:
    from apps.recommend.services.retriever import celsius_of
    from apps.recommend.services.style_rules import load_weather_rules

    profile = context.get("body_profile") or {}
    weather = context.get("weather") or {}

    # 단위를 명시한다. "27.4도"만 주면 모델이 화씨로 읽을 여지가 남는다.
    celsius = celsius_of(weather)
    temperature = f"{celsius}°C (섭씨)" if celsius is not None else "정보 없음"

    lines = [
        "## 사용자",
        f"- 체형: {profile.get('describe') or '정보 없음'}",
        f"- 날씨: {weather.get('region', '')} {temperature}"
        f" {weather.get('sky_state', '')}".strip(),
    ]

    # 기온대 판정을 미리 내려서 준다. 모델이 스스로 판단하게 두면, 착장에 아우터가
    # 있고 기온이 높을 때 "말이 되게" 만들려고 날씨 쪽을 굽힌다 — 27도를 두고
    # "선선한 날씨"라고 쓴 사고가 실제로 있었다. 판정을 사실로 못 박아 둔다.
    if (band := load_weather_rules().band_for(celsius)) is not None:
        lines.append(f"- 기온대: {band.label} — {band.hint}")
    pursuit = context.get("pursuit") or {}
    if pursuit:
        lines.append(f"- 선호: {json.dumps(pursuit.get('preferred', {}), ensure_ascii=False)}")
        lines.append(f"- 기피: {json.dumps(pursuit.get('avoided', {}), ensure_ascii=False)}")
    lines.append("")
    lines.append("## 오늘의 착장 (이미 확정됨 — 바꾸지 마세요)")
    lines.append(json.dumps(outfit, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def _build_daily_look_body(
    *, outfit: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    return {
        "systemInstruction": {"parts": [{"text": DAILY_LOOK_SYSTEM_INSTRUCTION}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": build_daily_look_prompt(
                            outfit=outfit, context=context
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            # 평가(0.4)보다 조금 높게 둔다. 매일 같은 사용자에게 같은 문장이 나오면
            # 추천이 아니라 템플릿처럼 읽힌다.
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "responseSchema": DAILY_LOOK_SCHEMA,
        },
    }


def write_daily_look_copy(
    *, outfit: dict[str, Any], context: dict[str, Any]
) -> DailyLookResult:
    """**이미 정해진** 착장에 사람이 읽을 문장을 붙인다.

    코디 선택은 리트리버가 결정적으로 끝낸다. 여기서 다시 고르게 하면 체형·취향
    점수 계산을 버리는 셈이고, 같은 입력에 다른 결과가 나와 재현도 채점도 못 한다.
    그래서 LLM의 역할을 설명 생성으로만 좁혔다.

    이미지를 보내지 않는다. 골든 원본은 대개 노출 불가이고, 조합을 말로 풀어내는
    일이라 태그만으로 충분하다 — 멀티모달 호출보다 훨씬 싸고 빠르다.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise GeminiConfigurationError("GEMINI_API_KEY가 설정되지 않았습니다.")

    request_body = _build_daily_look_body(outfit=outfit, context=context)
    model = settings.GEMINI_MODEL
    url = f"{settings.GEMINI_API_BASE_URL}/v1beta/models/{model}:generateContent"

    started = time.monotonic()
    error_payload: Any = None
    try:
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=request_body,
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            error_payload = _error_payload(response)
            logger.error(
                "Gemini 오늘의 룩 실패 %s: %s",
                response.status_code,
                response.text[:2000],
            )
        response.raise_for_status()
        response_payload = response.json()
        parsed = json.loads(_extract_text(response_payload))
    except requests.Timeout as exc:
        raise GeminiServiceError("Gemini 응답 시간이 초과되었습니다.") from exc
    except GeminiServiceError:
        raise
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        logger.exception("Gemini 오늘의 룩 호출 실패")
        raise GeminiServiceError(
            "오늘의 룩 생성에 실패했습니다.", response_payload=error_payload
        ) from exc

    # 코디는 이미 정해져 있으므로 고를 여지가 없다. 남은 환각 위험은 없는
    # 아이템에 설명을 붙이는 것뿐이라, 모르는 item_key는 조용히 버린다.
    # 문장 전체를 실패시킬 만한 오류는 아니다.
    known_keys = {str(item.get("item_key")) for item in outfit.get("items", [])}
    notes = [
        note
        for note in (parsed.get("items") or [])
        if str(note.get("item_key")) in known_keys
    ]
    dropped = len(parsed.get("items") or []) - len(notes)
    if dropped:
        logger.warning("오늘의 룩 문장: 알 수 없는 item_key %d건을 버렸습니다", dropped)
    parsed["items"] = notes

    return DailyLookResult(
        parsed=parsed,
        request=request_body,
        response=response_payload,
        model=model,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
