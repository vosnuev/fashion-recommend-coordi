import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.chat.models import (
    ChatAttachment,
    ChatMessage,
    ChatRun,
    ChatRunPersona,
    ChatSession,
    PersonaProfile,
)
from apps.chat.services import attachment_storage
from apps.chat.services.shared_reference import (
    REFERENCE_SCHEMA_VERSION,
    REFERENCE_TYPE_SHARED_WARDROBE_ITEM,
    REFERENCE_TYPE_WARDROBE_ITEM,
)
from apps.chat.services.stylist_personas import load_stylist_personas
from apps.recommend.models import (
    OutfitComposition,
    OutfitRenderJob,
    RecommendationResult,
)
from apps.recommend.serializers import (
    OutfitRenderJobSerializer,
    RecommendationCardItemSerializer,
)
from apps.wardrobe.services import storage as wardrobe_storage

logger = logging.getLogger(__name__)

# Django의 ImageField 검증 단계에서도 iPhone HEIC를 이미지로 인식하게 한다.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    logger.debug("pillow-heif 미설치: HEIC 채팅 첨부 검증을 사용할 수 없습니다.")


class ChatAttachmentSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ChatAttachment
        fields = [
            "id",
            "mime_type",
            "size",
            "sha256",
            "analysis_status",
            "analysis_result",
            "mood_decision",
            "mood_decided_at",
            "image_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_image_url(self, obj: ChatAttachment) -> str | None:
        try:
            return attachment_storage.presigned_get(obj.s3_key)
        except Exception:
            logger.warning(
                "채팅 첨부 presigned URL 발급 실패: attachment=%s",
                obj.pk,
                exc_info=True,
            )
            return None


class ChatReferenceSummarySerializer(serializers.Serializer):
    schema_version = serializers.CharField(read_only=True)
    type = serializers.CharField(read_only=True)
    shared_item_id = serializers.UUIDField(read_only=True, required=False)
    wardrobe_item_id = serializers.UUIDField(read_only=True, required=False)
    item_name = serializers.CharField(read_only=True, allow_blank=True)
    category_large = serializers.CharField(read_only=True, allow_blank=True)
    owner_name = serializers.CharField(read_only=True)
    room_name = serializers.CharField(read_only=True, allow_blank=True)
    image_url = serializers.URLField(read_only=True, allow_null=True)


class ChatMessageSerializer(serializers.ModelSerializer):
    attachments = ChatAttachmentSerializer(many=True, read_only=True)
    reference_summary = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = [
            "id",
            "sequence",
            "role",
            "content",
            "status",
            "client_message_id",
            "metadata",
            "attachments",
            "reference_summary",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(ChatReferenceSummarySerializer(allow_null=True))
    def get_reference_summary(self, obj: ChatMessage) -> dict | None:
        if obj.role != ChatMessage.Role.USER:
            return None
        run = getattr(obj, "run", None)
        snapshot = getattr(run, "reference_snapshot", None)
        if not isinstance(snapshot, dict) or not snapshot:
            return None
        reference_type = snapshot.get("type")
        if reference_type not in {
            REFERENCE_TYPE_SHARED_WARDROBE_ITEM,
            REFERENCE_TYPE_WARDROBE_ITEM,
        }:
            return None

        shared_item_id = str(snapshot.get("shared_item_id") or "").strip()
        wardrobe_item_id = str(snapshot.get("wardrobe_item_id") or "").strip()
        image_s3_key = str(snapshot.get("image_s3_key") or "").strip()
        item = snapshot.get("item")
        if not wardrobe_item_id or not image_s3_key or not isinstance(item, dict):
            return None

        try:
            image_url = wardrobe_storage.presigned_get(image_s3_key)
        except Exception:
            logger.warning(
                "공유 옷 레퍼런스 presigned URL 발급 실패: message=%s",
                obj.pk,
                exc_info=True,
            )
            image_url = None

        summary = {
            "schema_version": REFERENCE_SCHEMA_VERSION,
            "type": reference_type,
            "item_name": str(item.get("item_name") or ""),
            "category_large": str(item.get("category_large") or ""),
            "owner_name": str(snapshot.get("owner_name") or "멤버"),
            "room_name": str(snapshot.get("room_name") or ""),
            "image_url": image_url,
        }
        if shared_item_id:
            summary["shared_item_id"] = shared_item_id
        if reference_type == REFERENCE_TYPE_WARDROBE_ITEM:
            summary["wardrobe_item_id"] = wardrobe_item_id
        return summary


class ChatItemReferenceSerializer(serializers.Serializer):
    type = serializers.ChoiceField(
        choices=["SHARED_WARDROBE_ITEM", "WARDROBE_ITEM"]
    )
    shared_item_id = serializers.UUIDField(required=False)
    wardrobe_item_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        reference_type = attrs["type"]
        required_field = (
            "shared_item_id"
            if reference_type == REFERENCE_TYPE_SHARED_WARDROBE_ITEM
            else "wardrobe_item_id"
        )
        forbidden_field = (
            "wardrobe_item_id"
            if required_field == "shared_item_id"
            else "shared_item_id"
        )
        if not attrs.get(required_field):
            raise serializers.ValidationError(
                {required_field: "참고할 옷 아이템 ID가 필요합니다."}
            )
        if attrs.get(forbidden_field):
            raise serializers.ValidationError(
                {forbidden_field: "참조 유형과 맞지 않는 ID입니다."}
            )
        return attrs


class SharedReferenceNotFoundErrorSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=["REFERENCE_ITEM_NOT_FOUND"],
        read_only=True,
    )
    detail = serializers.CharField(read_only=True)


class SharedReferenceForbiddenErrorSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=["REFERENCE_ITEM_FORBIDDEN"],
        read_only=True,
    )
    detail = serializers.CharField(read_only=True)


class SharedReferenceNotReadyErrorSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=["REFERENCE_ITEM_NOT_READY"],
        read_only=True,
    )
    detail = serializers.CharField(read_only=True)


class WardrobeScopeSerializer(serializers.Serializer):
    system_categories = serializers.ListField(
        child=serializers.CharField(max_length=30),
        required=False,
        default=list,
        max_length=20,
    )
    hashtag_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=100,
    )
    match_mode = serializers.ChoiceField(
        choices=["REQUIRED", "PREFERRED"],
        required=False,
        default="REQUIRED",
    )

    def validate(self, attrs):
        if not attrs.get("system_categories") and not attrs.get("hashtag_ids"):
            raise serializers.ValidationError(
                "기본 카테고리 또는 해시태그를 하나 이상 선택해 주세요."
            )
        return attrs


class ChatMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        max_length=settings.CHAT_MESSAGE_MAX_CHARS,
        help_text="AI 스타일리스트에게 보낼 질문 또는 요청 문장",
    )
    client_message_id = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        max_length=128,
        help_text=(
            "클라이언트가 생성하는 메시지 고유값. 같은 요청 재시도에는 같은 값을, "
            "새 메시지에는 새 값을 사용합니다."
        ),
    )
    metadata = serializers.JSONField(
        required=False,
        help_text='선택 입력 JSON 객체. Swagger 테스트 예: {"source": "swagger"}',
    )
    reference = ChatItemReferenceSerializer(
        required=False,
        write_only=True,
        help_text=(
            "선택 입력. 옷장 아이템을 추천 결과가 아닌 참고 이미지로 사용할 때 "
            "공유 옷은 type=SHARED_WARDROBE_ITEM/shared_item_id, 내 옷은 "
            "type=WARDROBE_ITEM/wardrobe_item_id를 전달합니다."
        ),
    )
    wardrobe_scope = WardrobeScopeSerializer(
        required=False,
        write_only=True,
        help_text="개인 옷장 기본 카테고리·해시태그 추천 범위",
    )

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("metadata는 JSON 객체여야 합니다.")
        return value

    def validate_client_message_id(self, value: str) -> str:
        if value.startswith("run:"):
            raise serializers.ValidationError(
                "서버 예약 메시지 ID 접두사는 사용할 수 없습니다."
            )
        return value


class ChatAttachmentUploadSerializer(serializers.Serializer):
    image = serializers.ImageField(
        allow_empty_file=False,
        help_text="분석할 로컬 이미지 파일 (jpeg/png/webp/heic, 최대 설정 용량)",
    )
    client_message_id = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        max_length=128,
        help_text=(
            "클라이언트가 생성하는 첨부 메시지 고유값. 재시도에는 같은 값을 사용합니다."
        ),
    )
    content = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=settings.CHAT_MESSAGE_MAX_CHARS,
        default="",
        help_text="선택 입력. 사진과 함께 저장할 사용자 설명",
    )
    metadata = serializers.JSONField(
        required=False,
        default=dict,
        help_text='선택 입력 JSON 객체. Swagger 테스트 예: {"source": "swagger"}',
    )

    def validate_image(self, image):
        if image.size > settings.CHAT_ATTACHMENT_MAX_BYTES:
            max_mb = settings.CHAT_ATTACHMENT_MAX_BYTES // (1024 * 1024)
            raise serializers.ValidationError(f"이미지는 {max_mb}MB 이하여야 합니다.")
        if image.content_type not in settings.CHAT_ATTACHMENT_ALLOWED_CONTENT_TYPES:
            raise serializers.ValidationError(
                "지원하지 않는 이미지 형식입니다 (jpeg/png/webp/heic)."
            )
        return image

    def validate_client_message_id(self, value: str) -> str:
        if value.startswith("run:"):
            raise serializers.ValidationError(
                "서버 예약 메시지 ID 접두사는 사용할 수 없습니다."
            )
        return value

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("metadata는 JSON 객체여야 합니다.")
        return value


class ChatAttachmentUploadResponseSerializer(serializers.Serializer):
    message = ChatMessageSerializer(read_only=True)
    attachment = ChatAttachmentSerializer(read_only=True)
    created = serializers.BooleanField(read_only=True)


class ChatRunPersonaErrorSerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)


class ChatRunPersonaCardSerializer(serializers.Serializer):
    card_id = serializers.UUIDField(source="id", read_only=True)
    rank = serializers.IntegerField(read_only=True)
    total_product_price = serializers.IntegerField(read_only=True)
    validation_reasons = serializers.JSONField(read_only=True)
    reference_match = serializers.JSONField(read_only=True)
    warnings = serializers.JSONField(read_only=True)
    items = RecommendationCardItemSerializer(many=True, read_only=True)
    image = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()

    @extend_schema_field(OutfitRenderJobSerializer(allow_null=True))
    def get_image(self, obj):
        try:
            render_job = obj.render_job
        except OutfitRenderJob.DoesNotExist:
            return None
        return OutfitRenderJobSerializer(render_job, context=self.context).data

    @extend_schema_field(serializers.BooleanField())
    def get_is_saved(self, obj: OutfitComposition) -> bool:
        return bool(obj.saved_records.all())


class ChatRunPersonaResultSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    result_id = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()
    validated_reason_codes = serializers.SerializerMethodField()
    card = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()
    result_type = serializers.SerializerMethodField()
    generation = serializers.SerializerMethodField()
    previous_result_ids = serializers.SerializerMethodField()

    class Meta:
        model = ChatRunPersona
        fields = (
            "persona_id",
            "display_name",
            "display_order",
            "status",
            "result_id",
            "result_type",
            "generation",
            "previous_result_ids",
            "message",
            "validated_reason_codes",
            "card",
            "error",
            "retry_count",
            "alternative_status",
            "alternative_count",
            "alternative_error_code",
            "alternative_error_message",
            "latency_ms",
            "started_at",
            "completed_at",
        )
        read_only_fields = fields

    def get_display_name(self, obj: ChatRunPersona) -> str:
        return load_stylist_personas().get(obj.persona_id).display_name

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_result_id(self, obj: ChatRunPersona):
        result = self._result(obj)
        return result.pk if result is not None else None

    def get_message(self, obj: ChatRunPersona) -> str:
        result = self._result(obj)
        return result.persona_explanation if result is not None else ""

    def get_result_type(self, obj: ChatRunPersona) -> str | None:
        result = self._result(obj)
        return (
            getattr(result, "result_type", RecommendationResult.ResultType.INITIAL)
            if result is not None
            else None
        )

    def get_generation(self, obj: ChatRunPersona) -> int | None:
        result = self._result(obj)
        return getattr(result, "generation", 1) if result is not None else None

    @extend_schema_field(serializers.ListField(child=serializers.UUIDField()))
    def get_previous_result_ids(self, obj: ChatRunPersona) -> list:
        result = self._result(obj)
        if result is None:
            return []
        prefetched = getattr(obj, "historical_recommendation_results", None)
        if prefetched is not None:
            return [row.pk for row in prefetched]
        manager = getattr(obj, "recommendation_results", None)
        if manager is None:
            return []
        return list(
            manager.filter(is_current=False)
            .order_by("generation")
            .values_list("id", flat=True)
        )

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_validated_reason_codes(self, obj: ChatRunPersona) -> list[str]:
        result = self._result(obj)
        return list(result.validated_reason_codes) if result is not None else []

    @extend_schema_field(ChatRunPersonaCardSerializer(allow_null=True))
    def get_card(self, obj: ChatRunPersona) -> dict | None:
        result = self._result(obj)
        if result is None:
            return None
        cards = (
            result.public_compositions
            if hasattr(result, "public_compositions")
            else result.compositions.filter(
                status="VALIDATED",
            )
            .prefetch_related("items", "saved_records")
            .order_by("rank", "created_at")
        )
        card = next(iter(cards), None)
        if card is None:
            return None
        return ChatRunPersonaCardSerializer(card, context=self.context).data

    @extend_schema_field(ChatRunPersonaErrorSerializer(allow_null=True))
    def get_error(self, obj: ChatRunPersona) -> dict[str, str] | None:
        if obj.status != ChatRunPersona.Status.FAILED:
            return None
        return {"code": obj.error_code, "message": obj.error_message}

    @staticmethod
    def _result(obj: ChatRunPersona) -> RecommendationResult | None:
        try:
            return obj.recommendation_result
        except RecommendationResult.DoesNotExist:
            return None


class ChatRunSerializer(serializers.ModelSerializer):
    results = ChatRunPersonaResultSerializer(
        source="persona_executions",
        many=True,
        read_only=True,
    )

    class Meta:
        model = ChatRun
        fields = [
            "id",
            "session_id",
            "request_message_id",
            "response_message_id",
            "status",
            "response_mode",
            "persona_ids",
            "results",
            "reference_snapshot",
            "wardrobe_scope_snapshot",
            "enqueued_at",
            "error_code",
            "error_message",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class StylistListItemSerializer(serializers.Serializer):
    id = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    display_order = serializers.IntegerField(read_only=True)


class StylistListResponseSerializer(serializers.Serializer):
    schema_version = serializers.CharField(read_only=True)
    min_select = serializers.IntegerField(read_only=True)
    max_select = serializers.IntegerField(read_only=True)
    default_persona_ids = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    last_selected_persona_ids = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    stylists = StylistListItemSerializer(many=True, read_only=True)


class ChatSessionResponseModeUpdateSerializer(serializers.Serializer):
    response_mode = serializers.ChoiceField(
        choices=ChatSession.ResponseMode.choices,
        help_text="응답 구성 모드 (DEFAULT/STYLIST)",
    )
    selected_persona_ids = serializers.ListField(
        child=serializers.CharField(allow_blank=False, max_length=32),
        required=False,
        max_length=3,
        help_text=(
            "STYLIST 전환 시 선택할 스타일리스트 ID 배열. 생략하면 현재 세션 "
            "선택값, 회원 마지막 선택값, minimal 순서로 복원합니다."
        ),
    )


class GuestIdentityResponseSerializer(serializers.Serializer):
    identity_id = serializers.UUIDField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)


class GuestClaimResponseSerializer(serializers.Serializer):
    guest_identity_id = serializers.UUIDField(read_only=True)
    member_identity_id = serializers.UUIDField(read_only=True)
    session_count = serializers.IntegerField(read_only=True)
    message_count = serializers.IntegerField(read_only=True)
    attachment_count = serializers.IntegerField(read_only=True)
    recommendation_count = serializers.IntegerField(read_only=True)


class ChatMessageSubmitResponseSerializer(serializers.Serializer):
    message = ChatMessageSerializer(read_only=True)
    run = ChatRunSerializer(read_only=True)
    events_url = serializers.URLField(read_only=True)


class ChatMoodAnalysisResponseSerializer(serializers.Serializer):
    attachment = ChatAttachmentSerializer(read_only=True)
    run = ChatRunSerializer(read_only=True)
    events_url = serializers.URLField(read_only=True)


class ChatMoodDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=["APPROVE", "REJECT"],
        help_text="APPROVE는 분석 무드를 추천 조건에 반영하고 REJECT는 반영하지 않습니다.",
    )


class ChatMoodDecisionResponseSerializer(serializers.Serializer):
    attachment = ChatAttachmentSerializer(read_only=True)
    changed = serializers.BooleanField(read_only=True)
    applied = serializers.BooleanField(read_only=True)
    context_state = serializers.JSONField(read_only=True)


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = [
            "id",
            "mode",
            "response_mode",
            "selected_persona_ids",
            "persona_selection_updated_at",
            "title",
            "persona_profile_id",
            "parent_session_id",
            "context_state",
            "conversation_summary",
            "summary_through_sequence",
            "last_message_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ChatHistoryCursorSerializer(serializers.Serializer):
    cursor = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2048,
        default="",
    )


class ChatMessagePageQuerySerializer(ChatHistoryCursorSerializer):
    limit = serializers.IntegerField(
        required=False, min_value=1, max_value=100, default=50
    )


class ChatMessagePageResponseSerializer(serializers.Serializer):
    items = ChatMessageSerializer(many=True, read_only=True)
    total_count = serializers.IntegerField(read_only=True)
    next_cursor = serializers.CharField(read_only=True, allow_null=True)
    has_more = serializers.BooleanField(read_only=True)


class ChatSessionSearchQuerySerializer(ChatHistoryCursorSerializer):
    query = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        max_length=100,
    )
    limit = serializers.IntegerField(
        required=False, min_value=1, max_value=50, default=20
    )


class ChatSessionSearchMatchSerializer(serializers.Serializer):
    message_id = serializers.UUIDField(read_only=True)
    sequence = serializers.IntegerField(read_only=True)
    role = serializers.ChoiceField(choices=ChatMessage.Role.choices, read_only=True)
    preview = serializers.CharField(read_only=True)


class ChatSessionSearchItemSerializer(ChatSessionSerializer):
    search_match = ChatSessionSearchMatchSerializer(read_only=True, allow_null=True)

    class Meta(ChatSessionSerializer.Meta):
        fields = [*ChatSessionSerializer.Meta.fields, "search_match"]
        read_only_fields = fields


class ChatRunPersonaRetryResponseSerializer(serializers.Serializer):
    run = ChatRunSerializer(read_only=True)
    events_url = serializers.URLField(read_only=True)


class ChatRunPersonaAlternativeResponseSerializer(serializers.Serializer):
    run = ChatRunSerializer(read_only=True)
    events_url = serializers.URLField(read_only=True)


class ChatSessionSearchResponseSerializer(serializers.Serializer):
    query = serializers.CharField(read_only=True)
    items = ChatSessionSearchItemSerializer(many=True, read_only=True)
    total_count = serializers.IntegerField(read_only=True)
    next_cursor = serializers.CharField(read_only=True, allow_null=True)
    has_more = serializers.BooleanField(read_only=True)


class ChatSessionCreateSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=ChatSession.Mode.choices,
        help_text=(
            "WARDROBE_BASED는 내 옷장 아이템만 사용하고, NEW_ITEM은 새 상품을 "
            "포함해 추천합니다."
        ),
    )
    title = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=120,
        help_text="선택 입력. 비우면 첫 사용자 질문을 바탕으로 제목이 자동 저장됩니다.",
    )
    persona_profile_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="선택 입력. 사용할 스타일리스트 페르소나 UUID이며 없으면 null 또는 생략합니다.",
    )

    def validate_persona_profile_id(self, value):
        if value is not None and not PersonaProfile.objects.filter(pk=value).exists():
            raise serializers.ValidationError("존재하지 않는 페르소나 프로필입니다.")
        return value


class ChatSessionUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(
        allow_blank=True,
        max_length=120,
        help_text="대화 목록에 표시할 새 제목 (최대 120자)",
    )


class ChatSessionDeriveSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        choices=ChatSession.Mode.choices,
        help_text="파생 세션에 적용할 추천 모드",
    )
    title = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=120,
        help_text="선택 입력. 비우면 원본 세션 제목을 바탕으로 생성합니다.",
    )


class GuestClaimSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(
        help_text="게스트 대화·추천 이력을 현재 로그인 회원에게 이전하려면 true"
    )

    def validate_confirm(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError("게스트 대화 이전 확인이 필요합니다.")
        return value
