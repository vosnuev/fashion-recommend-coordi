"""채팅 사진 무드 분석 상태 전이와 사용자 승인·거절 처리."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.chat.models import (
    ChatAttachment,
    ChatIdentity,
    ChatRun,
    ChatSession,
)
from apps.chat.services import attachment_storage
from apps.chat.services.openai_adapter import LLMUsage, OpenAIChatAdapter
from apps.wardrobe import taxonomy as wardrobe_taxonomy


class ChatMoodError(RuntimeError):
    code = "CHAT_MOOD_INVALID"


class ChatMoodNotFound(ChatMoodError):
    code = "CHAT_ATTACHMENT_NOT_FOUND"


class ChatMoodAnalysisNotReady(ChatMoodError):
    code = "CHAT_MOOD_ANALYSIS_NOT_READY"


class ChatMoodDecisionFinalized(ChatMoodError):
    code = "CHAT_MOOD_DECISION_FINALIZED"


class ChatMoodAnalysisStateInvalid(ChatMoodError):
    code = "CHAT_MOOD_ANALYSIS_STATE_INVALID"


class ChatMoodImageInvalid(ChatMoodError):
    code = "CHAT_MOOD_IMAGE_INVALID"


class ChatMoodImageUnavailable(ChatMoodError):
    code = "CHAT_MOOD_IMAGE_UNAVAILABLE"


@dataclass(frozen=True)
class MoodAnalysisRequest:
    attachment: ChatAttachment
    run: ChatRun


@dataclass(frozen=True)
class MoodProcessingResult:
    analysis_result: dict
    response_id: str
    usage: LLMUsage


@dataclass(frozen=True)
class MoodDecisionResult:
    attachment: ChatAttachment
    session: ChatSession
    changed: bool
    applied: bool


def _owned_attachment(*, identity: ChatIdentity, session_id, attachment_id):
    return (
        ChatAttachment.objects.select_related("message", "message__session")
        .filter(
            pk=attachment_id,
            message__session_id=session_id,
            message__session__identity=identity,
            message__session__deleted_at__isnull=True,
        )
        .first()
    )


@transaction.atomic
def prepare_analysis(
    *,
    identity: ChatIdentity,
    session_id,
    attachment_id,
) -> MoodAnalysisRequest:
    """분석 실행을 하나만 만들고 실패한 동일 실행은 재사용한다."""
    attachment = _owned_attachment(
        identity=identity,
        session_id=session_id,
        attachment_id=attachment_id,
    )
    if attachment is None:
        raise ChatMoodNotFound("채팅 사진을 찾을 수 없습니다.")
    attachment = ChatAttachment.objects.select_for_update().get(pk=attachment.pk)

    run = ChatRun.objects.filter(request_message_id=attachment.message_id).first()
    if attachment.analysis_status == ChatAttachment.AnalysisStatus.SUCCEEDED:
        if run is None:
            raise ChatMoodAnalysisStateInvalid(
                "완료된 사진 분석 실행을 찾을 수 없습니다."
            )
        return MoodAnalysisRequest(attachment=attachment, run=run)

    if attachment.mood_decision != ChatAttachment.MoodDecision.UNDECIDED:
        raise ChatMoodAnalysisStateInvalid(
            "결정이 끝난 사진은 다시 분석할 수 없습니다."
        )

    if attachment.analysis_status == ChatAttachment.AnalysisStatus.FAILED:
        if run is None:
            attachment.analysis_status = ChatAttachment.AnalysisStatus.NOT_REQUESTED
        else:
            # 순환 import를 피하면서 기존 ChatRun 재시도 초기화 규칙을 그대로 쓴다.
            from apps.chat.services.orchestrator import reset_run_for_retry

            if not reset_run_for_retry(run.pk):
                raise ChatMoodAnalysisStateInvalid(
                    "현재 상태에서는 사진 분석을 다시 요청할 수 없습니다."
                )
            run.refresh_from_db()
            attachment.refresh_from_db()

    if attachment.analysis_status == ChatAttachment.AnalysisStatus.NOT_REQUESTED:
        attachment.analysis_status = ChatAttachment.AnalysisStatus.QUEUED
        attachment.analysis_result = {}
        attachment.save(update_fields=["analysis_status", "analysis_result"])

        from apps.chat.services.orchestrator import create_run

        run, _created = create_run(
            identity=identity,
            session_id=session_id,
            request_message_id=attachment.message_id,
        )

    if (
        attachment.analysis_status
        in {
            ChatAttachment.AnalysisStatus.QUEUED,
            ChatAttachment.AnalysisStatus.PROCESSING,
        }
        and run is None
    ):
        raise ChatMoodAnalysisStateInvalid("사진 분석 실행을 찾을 수 없습니다.")

    if run is None:
        raise ChatMoodAnalysisStateInvalid("사진 분석 실행을 준비할 수 없습니다.")
    return MoodAnalysisRequest(attachment=attachment, run=run)


def mark_analysis_failed(attachment_id) -> None:
    ChatAttachment.objects.filter(pk=attachment_id).exclude(
        analysis_status=ChatAttachment.AnalysisStatus.SUCCEEDED
    ).update(analysis_status=ChatAttachment.AnalysisStatus.FAILED)


def _register_heic_opener() -> None:
    try:
        from pillow_heif import register_heif_opener
    except ImportError as exc:
        raise ChatMoodImageInvalid("HEIC 분석 모듈이 설치되지 않았습니다.") from exc
    register_heif_opener()


def normalize_image(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """EXIF 방향을 적용하고 최대 변을 제한한 JPEG로 변환한다."""
    if mime_type == "image/heic":
        _register_heic_opener()
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail(
                (
                    settings.CHAT_MOOD_IMAGE_MAX_EDGE_PX,
                    settings.CHAT_MOOD_IMAGE_MAX_EDGE_PX,
                )
            )
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, "white")
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                image = canvas
            else:
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ChatMoodImageInvalid("분석할 수 없는 이미지입니다.") from exc


def _unique_allowed(values: list[str], allowed: list[str], limit: int) -> list[str]:
    allowed_set = set(allowed)
    output: list[str] = []
    for raw in values:
        value = str(raw).strip().lstrip("#")
        if value in allowed_set and value not in output:
            output.append(value)
        if len(output) >= limit:
            break
    return output


def _normalize_result(raw) -> dict:
    tags: list[str] = []
    for raw_tag in raw.tags:
        tag = str(raw_tag).strip().lstrip("#")[:24]
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 5:
            break
    if not tags:
        raise ChatMoodImageInvalid("사진에서 유효한 패션 무드를 찾지 못했습니다.")
    return {
        "summary": raw.summary.strip()[:160],
        "tags": tags,
        "styles": _unique_allowed(raw.styles, wardrobe_taxonomy.STYLES, 3),
        "colors": _unique_allowed(raw.colors, wardrobe_taxonomy.COLORS, 4),
        "fits": _unique_allowed(raw.fits, wardrobe_taxonomy.FITS, 2),
    }


def process_attachment(
    *,
    attachment: ChatAttachment,
    identity_id: str,
    llm: OpenAIChatAdapter,
) -> MoodProcessingResult:
    """S3 사진을 읽어 축소한 뒤 무드를 분석하고 결과를 영속화한다."""
    updated = ChatAttachment.objects.filter(
        pk=attachment.pk,
        analysis_status=ChatAttachment.AnalysisStatus.QUEUED,
    ).update(analysis_status=ChatAttachment.AnalysisStatus.PROCESSING)
    if not updated:
        attachment.refresh_from_db()
        raise ChatMoodAnalysisStateInvalid(
            f"현재 상태({attachment.analysis_status})에서는 분석을 시작할 수 없습니다."
        )

    try:
        image_bytes = attachment_storage.download_bytes(
            attachment.s3_key,
            max_bytes=settings.CHAT_ATTACHMENT_MAX_BYTES,
        )
    except ValueError as exc:
        raise ChatMoodImageInvalid(str(exc)) from exc
    except Exception as exc:
        raise ChatMoodImageUnavailable("저장된 사진을 불러올 수 없습니다.") from exc

    normalized_bytes, normalized_mime_type = normalize_image(
        image_bytes,
        attachment.mime_type,
    )
    analyzed = llm.analyze_photo_mood(
        identity_id=identity_id,
        image_bytes=normalized_bytes,
        mime_type=normalized_mime_type,
    )
    result = _normalize_result(analyzed.value)
    saved = ChatAttachment.objects.filter(
        pk=attachment.pk,
        analysis_status=ChatAttachment.AnalysisStatus.PROCESSING,
    ).update(
        analysis_status=ChatAttachment.AnalysisStatus.SUCCEEDED,
        analysis_result=result,
    )
    if not saved:
        raise ChatMoodAnalysisStateInvalid("사진 분석 결과를 저장할 수 없습니다.")
    return MoodProcessingResult(
        analysis_result=result,
        response_id=analyzed.response_id,
        usage=analyzed.usage,
    )


def _merge_unique(current, additions: list[str]) -> list[str]:
    values = list(current) if isinstance(current, list) else []
    for value in additions:
        if value not in values:
            values.append(value)
    return values


@transaction.atomic
def decide_mood(
    *,
    identity: ChatIdentity,
    session_id,
    attachment_id,
    decision: str,
) -> MoodDecisionResult:
    """첫 승인·거절만 확정하고 승인의 표준 태그만 세션 조건에 반영한다."""
    attachment = _owned_attachment(
        identity=identity,
        session_id=session_id,
        attachment_id=attachment_id,
    )
    if attachment is None:
        raise ChatMoodNotFound("채팅 사진을 찾을 수 없습니다.")
    attachment = ChatAttachment.objects.select_for_update().get(pk=attachment.pk)
    if attachment.analysis_status != ChatAttachment.AnalysisStatus.SUCCEEDED:
        raise ChatMoodAnalysisNotReady("사진 무드 분석이 아직 완료되지 않았습니다.")

    stored_decision = {
        "APPROVE": ChatAttachment.MoodDecision.APPROVED,
        "REJECT": ChatAttachment.MoodDecision.REJECTED,
    }[decision]
    if attachment.mood_decision != ChatAttachment.MoodDecision.UNDECIDED:
        if attachment.mood_decision != stored_decision:
            raise ChatMoodDecisionFinalized(
                "이미 확정한 사진 무드 결정은 반대로 변경할 수 없습니다."
            )
        return MoodDecisionResult(
            attachment=attachment,
            session=attachment.message.session,
            changed=False,
            applied=stored_decision == ChatAttachment.MoodDecision.APPROVED,
        )

    session = ChatSession.objects.select_for_update().get(pk=session_id)
    applied = stored_decision == ChatAttachment.MoodDecision.APPROVED
    if applied:
        analysis = attachment.analysis_result or {}
        state = deepcopy(session.context_state or {})
        conditions = dict(state.get("recommendation_conditions") or {})
        for key in ("styles", "colors", "fits"):
            conditions[key] = _merge_unique(
                conditions.get(key),
                list(analysis.get(key) or []),
            )
        state["recommendation_conditions"] = conditions
        approved = list(state.get("approved_photo_moods") or [])
        approved.append(
            {
                "attachment_id": str(attachment.pk),
                "summary": str(analysis.get("summary") or ""),
                "tags": list(analysis.get("tags") or []),
                "styles": list(analysis.get("styles") or []),
                "colors": list(analysis.get("colors") or []),
                "fits": list(analysis.get("fits") or []),
            }
        )
        state["approved_photo_moods"] = approved[-5:]
        session.context_state = state
        session.save(update_fields=["context_state", "updated_at"])

    attachment.mood_decision = stored_decision
    attachment.mood_decided_at = timezone.now()
    attachment.save(update_fields=["mood_decision", "mood_decided_at"])
    return MoodDecisionResult(
        attachment=attachment,
        session=session,
        changed=True,
        applied=applied,
    )
