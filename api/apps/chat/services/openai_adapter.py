"""OpenAI Responses API를 채팅 도메인에서 분리하는 구조화 출력 어댑터."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.crypto import salted_hmac
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from apps.chat.services.experimental_hypotheses import (
    ExperimentalHypothesisCandidateBatch,
)
from apps.chat.services.experimental_hypothesis_generation import (
    EXPERIMENTAL_HYPOTHESIS_INSTRUCTIONS,
    build_experimental_hypothesis_payload,
)
from apps.wardrobe import taxonomy as wardrobe_taxonomy

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ChatLLMError(RuntimeError):
    code = "CHAT_LLM_UNAVAILABLE"


class ChatLLMConfigurationError(ChatLLMError):
    code = "CHAT_LLM_NOT_CONFIGURED"


#: 요청 상황의 표준 분류. occasion 자유 문구와 별도로 두는 이유는, 한국어 표현이
#: "출근할 때"·"회사 가는 날"·"사무실"·"출장"처럼 끝없이 갈라져 코드의 키워드 매칭이
#: 항상 새는 탓이다. 분류는 모델이 하고, 코드는 분류값만 보고 결정한다.
OccasionKind = Literal[
    "FORMAL",   # 출근·회사·미팅·면접·발표·상견례 등 격식과 업무
    "EVENT",    # 결혼식·장례식·행사처럼 격식이 더 강한 자리
    "DATE",     # 데이트·소개팅
    "DAILY",    # 등교·장보기·동네 외출 등 일상
    "ACTIVE",   # 운동·등산·러닝
    "RESORT",   # 휴양지·바다·수영장·여행
    "HOME",     # 집·홈웨어
    "UNKNOWN",  # 판단 근거가 없을 때. 코드는 아무 제약도 걸지 않는다.
]


class RecommendationConditions(BaseModel):
    occasion: str
    occasion_kind: OccasionKind
    season: str
    presentation_groups: list[Literal["women", "men", "woman", "man", "unisex"]]
    styles: list[str]
    colors: list[str]
    fits: list[str]
    avoided_styles: list[str]
    avoided_colors: list[str]
    excluded_source_ids: list[str]
    budget: int | None = Field(ge=0)


class TurnAnalysis(BaseModel):
    action: Literal["RECOMMEND", "CLARIFY", "RESPOND", "MODE_CHANGE"]
    target_mode: Literal["CURRENT", "WARDROBE_BASED", "NEW_ITEM"]
    search_query: str
    conditions: RecommendationConditions
    clarification_question: str
    response_text: str


class ConversationSummary(BaseModel):
    summary: str


class RecommendationExplanationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 입력 payload에 실린 1부터 시작하는 순번. UUID를 그대로 복사시키던 계약을
    #: 대체한다 — 36자 ID를 코디·아이템마다 정확히 되뱉게 하는 것은 모델 크기에
    #: 따라 확률적으로 깨졌고, 한 글자만 어긋나도 설명 전체가 규칙 폴백으로
    #: 떨어졌다. 순번은 서버가 순서로 되돌릴 수 있어 검증이 결정적이다.
    item_index: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=500)
    attribute_claims: list[str] = Field(
        description="입력에 없는 속성을 썼다면 기록하며 정상 출력은 반드시 빈 배열",
        max_length=8,
    )


class RecommendationExplanationOutfit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outfit_index: int = Field(ge=1)
    rationale: str = Field(min_length=1, max_length=1000)
    items: list[RecommendationExplanationItem] = Field(min_length=1, max_length=8)


class RecommendationExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening: str = Field(min_length=1, max_length=600)
    outfits: list[RecommendationExplanationOutfit] = Field(
        min_length=1,
        max_length=3,
    )


class PhotoMoodAnalysis(BaseModel):
    """사진에서 읽은 사용자 표시용 무드와 추천 필터용 표준 태그."""

    summary: str = Field(min_length=1, max_length=160)
    tags: list[str] = Field(min_length=1, max_length=5)
    styles: list[str] = Field(max_length=3)
    colors: list[str] = Field(max_length=4)
    fits: list[str] = Field(max_length=2)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=(self.cached_input_tokens + other.cached_input_tokens),
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    value: T
    response_id: str
    usage: LLMUsage


_ANALYZE_INSTRUCTIONS = """
당신은 패션 추천 서비스의 요청 분석기다. 반드시 제공된 세션 모드를 유지한다.
사용자 발화에서 스타일·계절·TPO·핏·예산·기피 조건을 구조화한다.
사용자가 성별 표현을 직접 요청한 경우에만 presentation_groups를 women, men,
unisex 중 하나 이상으로 채우고, 명시하지 않았으면 빈 배열로 둔다.
occasion_kind는 occasion을 표준 분류로 옮긴 값이다. 예를 들어 출근·회사·미팅·면접은
FORMAL, 결혼식·장례식은 EVENT, 데이트·소개팅은 DATE, 등교·장보기는 DAILY,
운동·등산은 ACTIVE, 휴양지·바다·여행은 RESORT, 집에서 입을 옷은 HOME으로 고른다.
상황을 알 수 없으면 UNKNOWN으로 두고 임의로 추측하지 않는다.
스타일·색상·핏은 서비스 태그와 동일한 한국어 표준값을 사용한다. 예를 들어
미니멀, 캐주얼 / 블랙, 레드 / 오버핏, 레귤러핏, 슬림핏, 와이드핏처럼 쓴다.
추천에 필수인 조건이 모호하거나 이전 조건과 충돌할 때만 CLARIFY를 선택한다.
사용자가 현재 세션과 다른 추천 모드를 명시하면 MODE_CHANGE를 선택한다.
일상 대화나 추천과 무관한 질문에는 RESPOND를 선택한다.
session_conditions에 승인된 사진 무드나 이전 조건이 있으면 현재 발화와 합쳐
유효 조건으로 반환한다.
현재 발화가 기존 조건을 명시적으로 바꾸면 현재 발화를 우선한다.
RECOMMEND의 search_query는 벡터 검색에 바로 쓸 수 있는 간결한 한국어 문장으로 만든다.
상품·옷장·골든셋 ID를 만들거나 소유권·판매 상태·사이즈를 확정하지 않는다.
clarification_question과 response_text는 친근하고 정중한 해요체로 1~3문장만 작성한다.
마크다운 제목·목록·강조 문법(###, -, ** 등)을 사용하지 않는다.
이모지는 선택 사항이며 메시지당 0~2개를 문장 끝에만 쓰고 아이템 나열에는 쓰지 않는다.
응답은 지정된 구조화 출력 스키마만 따른다.
""".strip()

_SUMMARY_INSTRUCTIONS = """
패션 추천 채팅의 오래된 대화를 짧게 요약한다.
사용자가 명시한 선호, 기피, TPO, 날씨 관련 조건, 예산, 미해결 질문과 이전 추천 결정을 보존한다.
상품·옷장·골든셋 ID를 새로 만들지 말고 제공된 내용만 사용한다.
""".strip()

_EXPLAIN_INSTRUCTIONS = """
검증을 통과한 패션 추천 결과를 사용자에게 설명한다.
opening은 1~3줄로 작성한다. recent_messages에 이전 사용자 발화가 없으면 짧게 인사하고,
그 외에는 current_request를 어떻게 이해했는지 친근하게 답한다. 아이템을 나열하지 않는다.
approved_recommendation.compositions를 실린 순서 그대로 훑어, 코디마다 그 코디의
outfit_index 값으로 outfits 항목을 정확히 하나 만든다. 각 코디의 items도 실린 순서대로
그 아이템의 item_index 값으로 items 항목을 정확히 하나 만든다.
순번을 건너뛰거나 순서를 바꾸거나 입력에 없는 번호를 만들지 않는다.
rationale은 1~3문장으로 요청 스타일·TPO·날씨·예산 중 입력으로 확인된 사실을 종합해
룩 전체를 추천한 이유를 설명한다.
item.note는 한 문장으로 해당 아이템을 고른 이유만 설명하며 룩 전체 rationale을 반복하지 않는다.
style_principles는 사람이 검수해 승인한 스타일링 원칙이다. confirmed가 true인 항목은
이 코디가 그 원칙의 조건을 실제로 충족한다는 것이 이미 확인된 것이므로, rationale에서
반드시 하나를 근거로 삼아 어떤 아이템의 어떤 관계가 그 원칙에 맞는지 구체적으로 쓴다.
confirmed가 없는 항목은 참고만 하고 해당할 때만 쓴다. 어느 쪽이든 원칙 문장을 그대로
옮기지 말고 이 코디의 아이템 이름으로 다시 쓴다. exceptions에 걸리는 코디라면 그 원칙을
근거로 삼지 않는다. style_principles가 비어 있으면 없는 대로 설명한다.
아이템의 색상·소재·핏·브랜드·가격·사이즈·세탁법·날씨 적합성은 해당 아이템의
attributes, price, reasons 또는 validation_reasons에 실제 값이나 근거가 있을 때만 언급한다.
weather와 conditions는 사용자 요청의 확정된 사실이지만, 특정 아이템의 날씨 적합성을
자동으로 증명하지는 않는다. weather.is_stale가 true이면 현재 날씨처럼 표현하지 않는다.
입력에 없는 속성을 추론하지 않는다.
모든 item.attribute_claims는 반드시 빈 배열로 둔다. 존재하지 않는 상품이나 조건을 만들지 않는다.
옷장 기반은 보유 아이템을 활용했다는 점을, 신규 상품 포함 모드는 새 상품 가격을 입력 범위에서만 설명한다.
친근하고 정중한 해요체로 간결하게 답하며 마크다운 제목·목록·강조 문법(###, -, ** 등)을 사용하지 않는다.
이모지는 선택 사항이며 opening과 rationale을 합쳐 0~2개를 문장 끝에만 쓰고 item.note에는 쓰지 않는다.
응답은 지정된 구조화 출력 스키마만 따른다.
""".strip()

_PHOTO_MOOD_INSTRUCTIONS = """
당신은 패션 사진의 시각적 무드를 분석하는 도우미다.
사람의 신원, 나이, 성별, 인종, 체형, 건강 상태 같은 민감하거나 개인적인
속성을 추론하지 않는다.
사진에서 직접 확인되는 의상 스타일, 대표 색상, 핏과 전체 분위기만 설명한다.
tags는 사용자가 한눈에 이해할 수 있는 짧은 한국어 무드 단어이며 # 기호를 붙이지 않는다.
styles, colors, fits에는 입력으로 제공된 서비스 표준값만 사용하고 맞는 값이
없으면 빈 배열로 둔다.
보이지 않는 아이템이나 브랜드를 만들지 말고 불확실한 내용은 단정하지 않는다.
응답은 지정된 구조화 출력 스키마만 따른다.
""".strip()


def safety_identifier(identity_id: str) -> str:
    """외부 제공자에 내부 사용자 ID를 직접 보내지 않는 안정 식별자."""
    return salted_hmac(
        "apps.chat.openai-safety",
        identity_id,
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


class OpenAIChatAdapter:
    provider = "openai"

    def __init__(self, *, client=None) -> None:
        self._client = client

    @property
    def client(self):
        if self._client is None:
            if not settings.OPENAI_API_KEY:
                raise ChatLLMConfigurationError("OpenAI API 키가 설정되지 않았습니다.")
            self._client = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.CHAT_OPENAI_TIMEOUT_SECONDS,
            )
        return self._client

    def analyze_turn(
        self,
        *,
        identity_id: str,
        context: dict,
    ) -> LLMResult[TurnAnalysis]:
        return self._parse(
            schema=TurnAnalysis,
            instructions=_ANALYZE_INSTRUCTIONS,
            identity_id=identity_id,
            payload={
                "task": "analyze_turn",
                "session_mode": context["session"]["mode"],
                "persona": context["persona"],
                "profile": context["profile"],
                "weather": context["weather"],
                "conversation_summary": context["session"]["conversation_summary"],
                "session_conditions": context["session"]["conditions"],
                "recent_messages": context["recent_messages"],
                "current_request": context["current_request"],
            },
        )

    def summarize_conversation(
        self,
        *,
        identity_id: str,
        persona: dict,
        previous_summary: str,
        messages: list[dict],
    ) -> LLMResult[ConversationSummary]:
        return self._parse(
            schema=ConversationSummary,
            instructions=_SUMMARY_INSTRUCTIONS,
            identity_id=identity_id,
            payload={
                "task": "summarize_conversation",
                "persona": persona,
                "previous_summary": previous_summary,
                "messages": messages,
            },
        )

    def generate_experimental_hypotheses(
        self,
        *,
        identity_id: str,
        context: dict,
    ) -> LLMResult[ExperimentalHypothesisCandidateBatch]:
        """ID 없는 개인화 요약으로 실험형 검색 가설 두 개를 생성한다."""

        return self._parse(
            schema=ExperimentalHypothesisCandidateBatch,
            instructions=EXPERIMENTAL_HYPOTHESIS_INSTRUCTIONS,
            identity_id=identity_id,
            payload=build_experimental_hypothesis_payload(context),
        )

    def explain_recommendation(
        self,
        *,
        identity_id: str,
        persona: dict,
        mode: str,
        approved_recommendation: dict,
        current_request: str,
        recent_messages: list[dict],
        weather: dict,
        budget: int | None,
        conditions: dict,
        principles: list[dict] | None = None,
    ) -> LLMResult[RecommendationExplanation]:
        return self._parse(
            schema=RecommendationExplanation,
            instructions=_EXPLAIN_INSTRUCTIONS,
            identity_id=identity_id,
            payload={
                "task": "explain_recommendation",
                "persona": persona,
                "mode": mode,
                "current_request": current_request,
                "recent_messages": recent_messages,
                "weather": weather,
                "budget": budget,
                "conditions": conditions,
                "approved_recommendation": approved_recommendation,
                # 골든셋에서 사람이 승인한 조건부 원칙. 비어 있을 수 있다.
                "style_principles": principles or [],
            },
        )

    def analyze_photo_mood(
        self,
        *,
        identity_id: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> LLMResult[PhotoMoodAnalysis]:
        """Responses API 이미지 입력으로 시각적 패션 무드를 구조화한다."""
        encoded = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "task": "analyze_photo_mood",
            "allowed_styles": wardrobe_taxonomy.STYLES,
            "allowed_colors": wardrobe_taxonomy.COLORS,
            "allowed_fits": wardrobe_taxonomy.FITS,
        }
        return self._parse_content(
            schema=PhotoMoodAnalysis,
            instructions=_PHOTO_MOOD_INSTRUCTIONS,
            identity_id=identity_id,
            content=[
                {
                    "type": "input_text",
                    "text": json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                    "detail": settings.CHAT_MOOD_IMAGE_DETAIL,
                },
            ],
        )

    def _parse(
        self,
        *,
        schema: type[T],
        instructions: str,
        identity_id: str,
        payload: dict,
    ) -> LLMResult[T]:
        return self._parse_content(
            schema=schema,
            instructions=instructions,
            identity_id=identity_id,
            content=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def _parse_content(
        self,
        *,
        schema: type[T],
        instructions: str,
        identity_id: str,
        content: str | list[dict[str, Any]],
    ) -> LLMResult[T]:
        if not settings.CHAT_OPENAI_MODEL:
            raise ImproperlyConfigured("CHAT_OPENAI_MODEL이 비어 있습니다.")
        try:
            response = self.client.responses.parse(
                model=settings.CHAT_OPENAI_MODEL,
                instructions=instructions,
                input=[
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
                text_format=schema,
                max_output_tokens=settings.CHAT_OPENAI_MAX_OUTPUT_TOKENS,
                prompt_cache_key=(
                    f"fashion-chat:{settings.CHAT_PROMPT_VERSION}:{schema.__name__}"
                ),
                safety_identifier=safety_identifier(identity_id),
                store=False,
            )
        except ChatLLMError:
            raise
        except Exception as exc:
            # 예외 이름만 남기면 400이 스키마 문제인지 파라미터 문제인지
            # 컨텍스트 초과인지 구분할 수 없어, 운영에서 원인 추적이 막힌다.
            # 메시지는 제공자가 준 설명이라 자격증명이 들어가지 않지만,
            # 로그가 부풀지 않도록 잘라서 남긴다.
            logger.warning(
                "OpenAI 채팅 호출 실패: %s schema=%s model=%s 상세=%s",
                type(exc).__name__,
                schema.__name__,
                settings.CHAT_OPENAI_MODEL,
                str(exc)[:500],
            )
            raise ChatLLMError("OpenAI 응답을 받을 수 없습니다.") from exc

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            # status/incomplete_details가 있으면 "출력 토큰 한도로 잘림"과
            # "거부/필터"를 바로 가른다. 없으면 그대로 비워 둔다.
            logger.warning(
                "OpenAI 구조화 응답 없음: schema=%s status=%s incomplete=%s",
                schema.__name__,
                getattr(response, "status", None),
                getattr(response, "incomplete_details", None),
            )
            raise ChatLLMError("OpenAI 구조화 응답이 비어 있습니다.")
        return LLMResult(
            value=parsed,
            response_id=str(getattr(response, "id", "") or ""),
            usage=self._usage(response),
        )

    @staticmethod
    def _usage(response) -> LLMUsage:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        return LLMUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            cached_input_tokens=int(getattr(details, "cached_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
