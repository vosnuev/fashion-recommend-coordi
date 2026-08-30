"""스타일 캘린더 API 직렬화."""

from __future__ import annotations

from collections.abc import Mapping

from rest_framework import serializers

from apps.style_calendar.contracts import (
    CalendarProcessingErrorCode,
    CalendarSourceType,
    CalendarStatus,
)
from apps.style_calendar.models import CalendarEntry, CalendarWardrobeItem
from apps.style_calendar.services import storage

MAX_CALENDAR_UPLOAD_MB = 15
ALLOWED_CALENDAR_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}


class StrictObjectInputMixin:
    """입력 serializer가 JSON 객체와 선언된 필드만 받도록 제한한다."""

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                {"non_field_errors": ["요청 본문은 JSON 객체여야 합니다."]}
            )

        allowed_fields = set(self.fields)
        unknown_fields = set(data) - allowed_fields
        if unknown_fields:
            raise serializers.ValidationError(
                {
                    field: "허용되지 않은 필드입니다."
                    for field in sorted(unknown_fields)
                }
            )
        return super().to_internal_value(data)


class StringListField(serializers.ListField):
    """숫자를 문자열로 묵시적으로 변환하지 않는 문자열 배열 필드."""

    def to_internal_value(self, data):
        if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
            raise serializers.ValidationError("문자열 배열이어야 합니다.")
        return super().to_internal_value(data)


class OptionalUUIDListField(serializers.ListField):
    """Swagger multipart가 만드는 빈 문자열 항목을 선택 없음으로 정규화한다."""

    def to_internal_value(self, data):
        if data == "":
            data = []
        elif isinstance(data, (list, tuple)):
            data = [
                item
                for item in data
                if not (isinstance(item, str) and not item.strip())
            ]
        return super().to_internal_value(data)


class CalendarPeriodQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate(self, attrs):
        if attrs["start_date"] > attrs["end_date"]:
            raise serializers.ValidationError(
                {"end_date": "종료일은 시작일보다 빠를 수 없습니다."}
            )
        return attrs


class CalendarDateQuerySerializer(serializers.Serializer):
    date = serializers.DateField()


class CalendarMetadataUpdateSerializer(StrictObjectInputMixin, serializers.ModelSerializer):
    schedule = serializers.CharField(required=False, allow_blank=True)
    tpo = StringListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )
    hashtags = StringListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = CalendarEntry
        fields = ("schedule", "tpo", "hashtags")


class CalendarWardrobeCreateSerializer(StrictObjectInputMixin, serializers.Serializer):
    date = serializers.DateField()
    wardrobe_item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
    schedule = serializers.CharField(required=False, allow_blank=True, default="")
    tpo = StringListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
        default=list,
    )
    hashtags = StringListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
        default=list,
    )

    def validate_wardrobe_item_ids(self, item_ids):
        if len(item_ids) != len(set(item_ids)):
            raise serializers.ValidationError("중복된 옷장 아이템 ID가 있습니다.")
        return item_ids


class CalendarWardrobeItemLinkSerializer(StrictObjectInputMixin, serializers.Serializer):
    """이미 있는 캘린더에 더할 옷장 아이템 목록."""

    wardrobe_item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )

    def validate_wardrobe_item_ids(self, item_ids):
        if len(item_ids) != len(set(item_ids)):
            raise serializers.ValidationError("중복된 옷장 아이템 ID가 있습니다.")
        return item_ids


class CalendarPhotoCreateSerializer(StrictObjectInputMixin, serializers.Serializer):
    image = serializers.ImageField()
    date = serializers.DateField()
    wardrobe_item_ids = OptionalUUIDListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        default=list,
        help_text=(
            "선택 사항. 기존 옷장 아이템을 함께 연결하지 않으면 비워 둡니다."
        ),
    )
    schedule = serializers.CharField(required=False, allow_blank=True, default="")
    tpo = StringListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
        default=list,
    )
    hashtags = StringListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
        default=list,
    )

    def validate_image(self, image):
        if image.size > MAX_CALENDAR_UPLOAD_MB * 1024 * 1024:
            raise serializers.ValidationError(
                f"이미지는 {MAX_CALENDAR_UPLOAD_MB}MB 이하여야 합니다."
            )
        if image.content_type not in ALLOWED_CALENDAR_IMAGE_TYPES:
            raise serializers.ValidationError(
                "지원하지 않는 이미지 형식입니다 (jpeg/png/webp/heic)."
            )
        return image

    def validate_wardrobe_item_ids(self, item_ids):
        if len(item_ids) != len(set(item_ids)):
            raise serializers.ValidationError("중복된 옷장 아이템 ID가 있습니다.")
        return item_ids


class CalendarProcessingStatusSerializer(serializers.ModelSerializer):
    calendar_id = serializers.UUIDField(source="id", read_only=True)
    processing_required = serializers.SerializerMethodField()
    is_terminal = serializers.SerializerMethodField()
    result_available = serializers.SerializerMethodField()
    item_counts = serializers.SerializerMethodField()
    failure = serializers.SerializerMethodField()

    class Meta:
        model = CalendarEntry
        fields = (
            "calendar_id",
            "status",
            "processing_required",
            "is_terminal",
            "result_available",
            "item_counts",
            "failure",
            "processing_started_at",
            "processing_completed_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_processing_required(self, obj) -> bool:
        return obj.source_type == CalendarSourceType.PHOTO_UPLOAD.value

    def get_is_terminal(self, obj) -> bool:
        return obj.status in {
            CalendarStatus.COMPLETED.value,
            CalendarStatus.FAILED.value,
        }

    def get_result_available(self, obj) -> bool:
        return obj.status == CalendarStatus.COMPLETED.value

    def get_item_counts(self, obj) -> dict[str, int]:
        return {
            "total": obj.total_item_count,
            "extracted": obj.extracted_item_count,
            "failed": obj.failed_item_count,
        }

    def get_failure(self, obj) -> dict[str, str] | None:
        if obj.status != CalendarStatus.FAILED.value:
            return None
        code = (
            obj.processing_error_code
            or CalendarProcessingErrorCode.IMAGE_PROCESSING_FAILED.value
        )
        public_messages = {
            CalendarProcessingErrorCode.QUEUE_ENQUEUE_FAILED.value: (
                "처리 대기열 등록에 실패했습니다. 잠시 후 다시 시도해주세요."
            ),
            CalendarProcessingErrorCode.NO_ITEM_EXTRACTED.value: (
                "사진에서 처리할 수 있는 패션 아이템을 찾지 못했습니다."
            ),
            CalendarProcessingErrorCode.IMAGE_PROCESSING_FAILED.value: (
                "이미지 처리에 실패했습니다. 잠시 후 다시 시도해주세요."
            ),
        }
        return {
            "code": code,
            "message": public_messages.get(
                code,
                "이미지 처리에 실패했습니다. 잠시 후 다시 시도해주세요.",
            ),
        }


class CalendarWardrobeItemSerializer(serializers.ModelSerializer):
    link_id = serializers.UUIDField(source="id", read_only=True)
    wardrobe_item_id = serializers.UUIDField(read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = CalendarWardrobeItem
        fields = (
            "link_id",
            "wardrobe_item_id",
            "image_url",
            "sort_order",
            "snapshot",
        )

    def get_image_url(self, obj) -> str:
        return storage.presigned_get(obj.snapshot.get("s3_key", ""))


class CalendarEntrySerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    wardrobe_items = CalendarWardrobeItemSerializer(
        source="wardrobe_links",
        many=True,
        read_only=True,
    )

    class Meta:
        model = CalendarEntry
        fields = (
            "id",
            "date",
            "source_type",
            "image_s3_key",
            "image_url",
            "schedule",
            "tpo",
            "weather_snapshot",
            "hashtags",
            "skipped_categories",
            "status",
            "wardrobe_items",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_image_url(self, obj) -> str:
        return storage.presigned_get(obj.image_s3_key)
