"""룩북 API 직렬화."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from rest_framework import serializers

from apps.lookbook.contracts import (
    LookbookProcessingErrorCode,
    LookbookSourceType,
    LookbookStatus,
)
from apps.lookbook.models import LookbookPost, LookbookWardrobeItem
from apps.lookbook.services import storage

MAX_LOOKBOOK_UPLOAD_MB = 15
ALLOWED_LOOKBOOK_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
}
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

logger = logging.getLogger(__name__)


def _safe_presign(bucket: str, key: str) -> str:
    """서명 실패를 이미지 하나의 문제로 가둔다.

    룩북 목록은 한 응답에 수십 장을 담는다. 버킷 하나가 잘못 설정돼 있거나
    자격증명이 만료됐을 때 목록 전체를 500으로 만들면 사용자는 이미 잘 저장돼
    있는 룩까지 못 본다 — 이미지 없는 카드가 500 화면보다 낫다.
    (오늘의 룩 조회가 같은 판단을 한다: recommend/serializers.py)
    """

    try:
        return storage.presigned_get_in(bucket, key)
    except Exception:  # noqa: BLE001 — 이미지 하나가 목록을 죽이면 안 된다
        logger.warning("룩북 이미지 서명 실패: bucket=%s key=%s", bucket, key)
        return ""


class DiscoveryLookQuerySerializer(serializers.Serializer):
    query = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=100
    )
    tag = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=30
    )
    gender = serializers.ChoiceField(
        required=False, allow_blank=True, choices=("WOMAN", "MAN"), default=""
    )
    limit = serializers.IntegerField(
        required=False, min_value=1, max_value=50, default=20
    )
    offset = serializers.IntegerField(required=False, min_value=0, default=0)


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
                {field: "허용되지 않은 필드입니다." for field in sorted(unknown_fields)}
            )
        return super().to_internal_value(data)


class StringListField(serializers.ListField):
    """숫자를 문자열로 묵시적으로 변환하지 않는 문자열 배열 필드."""

    def to_internal_value(self, data):
        if not isinstance(data, list) or any(
            not isinstance(item, str) for item in data
        ):
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


class OptionalDateField(serializers.DateField):
    """multipart에서 비워 둔 날짜를 '캘린더 기록 안 함'으로 읽는다."""

    def to_internal_value(self, data):
        if data in ("", None):
            return None
        return super().to_internal_value(data)


class _LookbookCreateMixin:
    def validate_wardrobe_item_ids(self, item_ids):
        if len(item_ids) != len(set(item_ids)):
            raise serializers.ValidationError("중복된 옷장 아이템 ID가 있습니다.")
        return item_ids

    def validate(self, attrs):
        if attrs.get("overwrite_calendar") and attrs.get("calendar_date") is None:
            raise serializers.ValidationError(
                {"overwrite_calendar": "calendar_date 없이 덮어쓸 수 없습니다."}
            )
        return attrs


class LookbookWardrobeCreateSerializer(
    _LookbookCreateMixin,
    StrictObjectInputMixin,
    serializers.Serializer,
):
    """사진 없이 옷장 아이템만 골라 올리는 룩북."""

    wardrobe_item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        help_text="입은 옷. 룩 사진이 없으면 첫 아이템 이미지가 표지가 됩니다.",
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
    calendar_date = serializers.DateField(
        required=False,
        allow_null=True,
        default=None,
        help_text="'캘린더에도 기록하기'를 켠 경우의 날짜. 없으면 룩북에만 남깁니다.",
    )
    overwrite_calendar = serializers.BooleanField(
        required=False,
        default=False,
        help_text="그 날짜에 이미 캘린더가 있을 때 교체할지 여부 (사용자 확인 후 true).",
    )
    is_public = serializers.BooleanField(
        required=False,
        default=False,
        help_text="켜면 앱 사용자 전체가 둘러보기에서 볼 수 있습니다.",
    )


class LookbookPhotoCreateSerializer(
    _LookbookCreateMixin,
    StrictObjectInputMixin,
    serializers.Serializer,
):
    """룩 사진을 올려 만드는 룩북 (사진 속 아이템은 비동기 등록)."""

    image = serializers.ImageField()
    wardrobe_item_ids = OptionalUUIDListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        default=list,
        help_text=(
            "입은 옷. 여기서 지정한 옷의 대분류(상의/하의 등)는 사진에서 "
            "다시 등록하지 않습니다."
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
    calendar_date = OptionalDateField(
        required=False,
        allow_null=True,
        default=None,
        help_text="'캘린더에도 기록하기'를 켠 경우의 날짜. 없으면 룩북에만 남깁니다.",
    )
    overwrite_calendar = serializers.BooleanField(
        required=False,
        default=False,
        help_text="그 날짜에 이미 캘린더가 있을 때 교체할지 여부 (사용자 확인 후 true).",
    )
    is_public = serializers.BooleanField(
        required=False,
        default=False,
        help_text="켜면 앱 사용자 전체가 둘러보기에서 볼 수 있습니다.",
    )

    def validate_image(self, image):
        if image.size > MAX_LOOKBOOK_UPLOAD_MB * 1024 * 1024:
            raise serializers.ValidationError(
                f"이미지는 {MAX_LOOKBOOK_UPLOAD_MB}MB 이하여야 합니다."
            )
        if image.content_type not in ALLOWED_LOOKBOOK_IMAGE_TYPES:
            raise serializers.ValidationError(
                "지원하지 않는 이미지 형식입니다 (jpeg/png/webp/heic)."
            )
        return image


class LookbookMetadataUpdateSerializer(
    StrictObjectInputMixin,
    serializers.ModelSerializer,
):
    """PATCH — 일정·TPO·해시태그만 수정한다 (사진·아이템 구성은 재등록)."""

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

    is_public = serializers.BooleanField(required=False)

    class Meta:
        model = LookbookPost
        fields = ("schedule", "tpo", "hashtags", "is_public")


class LookbookListQuerySerializer(serializers.Serializer):
    """목록 조회 쿼리. 피드는 계속 자라므로 기본 페이지 크기를 강제한다."""

    hashtag = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(
        choices=[s.value for s in LookbookStatus],
        required=False,
        allow_blank=True,
        default="",
    )
    limit = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=MAX_PAGE_SIZE,
        default=DEFAULT_PAGE_SIZE,
    )
    offset = serializers.IntegerField(required=False, min_value=0, default=0)


class LookbookWardrobeItemSerializer(serializers.ModelSerializer):
    link_id = serializers.UUIDField(source="id", read_only=True)
    wardrobe_item_id = serializers.UUIDField(read_only=True)
    image_url = serializers.SerializerMethodField()
    # 이 옷이 옷장에 들어 있는가. NULL 이면 룩 상세가 '옷장에 추가' 버튼을 그린다.
    #
    # source="wardrobe_item.added_to_closet_at" 로 두면 안 된다 — 골든 코디의
    # 구성 아이템은 wardrobe_item 이 NULL 이라 DRF 의 속성 순회가 AttributeError
    # 로 죽고, 룩북 목록 전체가 500 이 된다.
    added_to_closet_at = serializers.SerializerMethodField()

    class Meta:
        model = LookbookWardrobeItem
        fields = (
            "link_id",
            "wardrobe_item_id",
            "link_type",
            "image_url",
            "sort_order",
            "snapshot",
            "added_to_closet_at",
        )
        read_only_fields = fields

    def get_image_url(self, obj) -> str:
        snapshot = obj.snapshot or {}
        return _safe_presign(
            str(snapshot.get("s3_bucket", "")),
            str(snapshot.get("s3_key", "")),
        )

    def get_added_to_closet_at(self, obj) -> str | None:
        item = obj.wardrobe_item
        if item is None or item.added_to_closet_at is None:
            return None
        return item.added_to_closet_at.isoformat()


class LookbookPostSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    wardrobe_items = LookbookWardrobeItemSerializer(
        source="wardrobe_links",
        many=True,
        read_only=True,
    )
    calendar = serializers.SerializerMethodField()

    class Meta:
        model = LookbookPost
        fields = (
            "id",
            "source_type",
            "golden_id",
            "image_s3_key",
            "image_url",
            "schedule",
            "tpo",
            "hashtags",
            "skipped_categories",
            "status",
            "is_public",
            "calendar",
            "wardrobe_items",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_image_url(self, obj) -> str:
        return _safe_presign(obj.image_s3_bucket, obj.image_s3_key)

    def get_calendar(self, obj) -> dict[str, str] | None:
        """캘린더에도 남긴 룩이면 그 날짜를 함께 준다.

        캘린더 상세를 여기서 펼치지 않는 이유: 같은 데이터를 두 화면이 각자
        조회하게 두면 한쪽 스키마가 바뀔 때 다른 쪽 응답까지 흔들린다.
        """

        if obj.calendar_entry_id is None:
            return None
        return {
            "id": str(obj.calendar_entry_id),
            "date": obj.calendar_entry.date.isoformat(),
        }


class LookbookProcessingStatusSerializer(serializers.ModelSerializer):
    lookbook_id = serializers.UUIDField(source="id", read_only=True)
    processing_required = serializers.SerializerMethodField()
    is_terminal = serializers.SerializerMethodField()
    result_available = serializers.SerializerMethodField()
    item_counts = serializers.SerializerMethodField()
    failure = serializers.SerializerMethodField()

    class Meta:
        model = LookbookPost
        fields = (
            "lookbook_id",
            "status",
            "processing_required",
            "is_terminal",
            "result_available",
            "skipped_categories",
            "item_counts",
            "failure",
            "processing_started_at",
            "processing_completed_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_processing_required(self, obj) -> bool:
        return obj.source_type == LookbookSourceType.PHOTO_UPLOAD.value

    def get_is_terminal(self, obj) -> bool:
        return obj.status in {
            LookbookStatus.COMPLETED.value,
            LookbookStatus.FAILED.value,
        }

    def get_result_available(self, obj) -> bool:
        return obj.status == LookbookStatus.COMPLETED.value

    def get_item_counts(self, obj) -> dict[str, int]:
        return {
            "total": obj.total_item_count,
            "selected": obj.selected_item_count,
            "extracted": obj.extracted_item_count,
        }

    def get_failure(self, obj) -> dict[str, str] | None:
        if obj.status != LookbookStatus.FAILED.value:
            return None
        code = (
            obj.processing_error_code
            or LookbookProcessingErrorCode.IMAGE_PROCESSING_FAILED.value
        )
        public_messages = {
            LookbookProcessingErrorCode.QUEUE_ENQUEUE_FAILED.value: (
                "처리 대기열 등록에 실패했습니다. 잠시 후 다시 시도해주세요."
            ),
            LookbookProcessingErrorCode.NO_ITEM_EXTRACTED.value: (
                "사진에서 처리할 수 있는 패션 아이템을 찾지 못했습니다."
            ),
            LookbookProcessingErrorCode.IMAGE_PROCESSING_FAILED.value: (
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
