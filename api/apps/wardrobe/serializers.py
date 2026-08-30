"""옷장 등록 API 시리얼라이저.

태그 값 검증은 taxonomy.py 상수를 기준으로 한다.
콜백은 이미지 프로세서가 보내는 페이로드(캡션 + 벡터 + S3 키)를 받는다.
"""
from __future__ import annotations

import math
import os
import re

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from . import taxonomy as T
from .models import WardrobeHashtag, WardrobeItem, WardrobeUploadJob, WardrobeViewPreference
from .services import storage

MAX_UPLOAD_MB = 15
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic"}
MAX_BATCH_ITEMS = int(os.getenv("WARDROBE_BATCH_MAX_ITEMS", "30"))
MAX_BATCH_TOTAL_MB = int(os.getenv("WARDROBE_BATCH_MAX_TOTAL_MB", "100"))


class WardrobeHashtagCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, trim_whitespace=False)
    item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=500,
    )


class WardrobeHashtagUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, trim_whitespace=False)


class WardrobeHashtagItemsPatchSerializer(serializers.Serializer):
    add_item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=500,
    )
    remove_item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=500,
    )


class WardrobeItemHashtagsPutSerializer(serializers.Serializer):
    names = serializers.ListField(
        child=serializers.CharField(max_length=80, trim_whitespace=False),
        allow_empty=True,
        max_length=100,
    )


class WardrobeHashtagOrderSerializer(serializers.Serializer):
    hashtag_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=True,
        max_length=500,
    )


class WardrobeHashtagSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = WardrobeHashtag
        fields = ["id", "name", "position"]
        read_only_fields = fields


class WardrobeHashtagSerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = WardrobeHashtag
        fields = [
            "id",
            "name",
            "position",
            "item_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_item_count(self, obj) -> int:
        annotated = getattr(obj, "item_count", None)
        if annotated is not None:
            return annotated
        return obj.item_links.filter(
            wardrobe_item__user_id=obj.user_id,
            wardrobe_item__added_to_closet_at__isnull=False,
        ).count()


class WardrobeViewPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = WardrobeViewPreference
        fields = ["group_mode", "item_sort", "updated_at"]
        read_only_fields = ["updated_at"]

# ── 업로드 ────────────────────────────────────────────────
class WardrobeUploadSerializer(serializers.Serializer):
    # Pillow의 ImageField는 환경에 따라 HEIC를 이미지로 인식하지 못한다.
    # 파일 헤더를 직접 검사해 웹 브라우저가 부정확한 MIME을 보내도 동일하게 처리한다.
    image = serializers.FileField()

    def validate_image(self, image):
        if image.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise serializers.ValidationError(
                f"이미지는 {MAX_UPLOAD_MB}MB 이하여야 합니다."
            )
        position = image.tell()
        image.seek(0)
        detected = storage._image_type(image.read(16))
        image.seek(position)
        if detected is None or detected[0] not in ALLOWED_CONTENT_TYPES:
            raise serializers.ValidationError(
                "지원하지 않는 이미지 형식입니다 (jpeg/png/webp/heic)."
            )
        image.content_type = detected[0]
        return image


class WardrobeBatchItemSerializer(serializers.Serializer):
    image_link = serializers.URLField(max_length=2048)
    item_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    category_large = serializers.ChoiceField(
        choices=[""] + T.CATEGORY_LARGE, required=False, allow_blank=True, default="",
    )
    category_small = serializers.ChoiceField(
        choices=[""] + T.ALL_SMALL, required=False, allow_blank=True, default="",
    )
    season = serializers.ListField(
        child=serializers.ChoiceField(choices=T.SEASONS), required=False, default=list,
    )
    style = serializers.ListField(
        child=serializers.ChoiceField(choices=T.STYLES), required=False, default=list,
    )
    color = serializers.ChoiceField(choices=[""] + T.COLORS, required=False, allow_blank=True, default="")
    pattern = serializers.ChoiceField(choices=[""] + T.PATTERNS, required=False, allow_blank=True, default="")
    fit = serializers.ChoiceField(choices=[""] + T.FITS, required=False, allow_blank=True, default="")
    material = serializers.ChoiceField(choices=[""] + T.MATERIALS, required=False, allow_blank=True, default="")
    sleeve = serializers.ChoiceField(choices=[""] + T.SLEEVES, required=False, allow_blank=True, default="")
    length = serializers.ChoiceField(choices=[""] + T.LENGTHS, required=False, allow_blank=True, default="")
    usage = serializers.ListField(child=serializers.CharField(max_length=20), required=False, default=list)
    layer_role = serializers.ChoiceField(
        choices=[""] + T.LAYER_ROLES, required=False, allow_blank=True, default="",
    )
    layer_order = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=3)
    confirmed = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        large, small = attrs.get("category_large", ""), attrs.get("category_small", "")
        if small and (not large or not T.is_valid_pair(large, small)):
            raise serializers.ValidationError({"category_small": "대분류와 맞지 않는 소분류입니다."})
        return attrs


class WardrobeBatchCreateSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=WardrobeBatchItemSerializer(), allow_empty=False, max_length=MAX_BATCH_ITEMS,
    )
    source = serializers.RegexField(
        r"^[a-z][a-z0-9_-]{0,19}$", required=False, default="in_app_browser",
    )


# ── 아이템 조회/수정 ──────────────────────────────────────
class WardrobeItemSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    wardrobe_hashtags = serializers.SerializerMethodField()

    class Meta:
        model = WardrobeItem
        fields = [
            "id", "job", "s3_key", "image_url", "item_name",
            "category_large", "category_small", "season", "style", "color",
            "pattern", "fit", "material", "sleeve", "length", "usage",
            "layer_role", "layer_order", "seg_meta", "confirmed", "created_at",
            # 즐겨찾기(별) — 옷장에서 자주 입는 옷만 모아 보는 데 쓴다.
            "is_favorite",
            # NULL 이면 아직 옷장 밖 — 룩 상세가 '옷장에 추가' 버튼을 그릴지 판단한다.
            "added_to_closet_at",
            # 확정하면 이 방에 공유된다는 예약. 상세 화면이 "확정 시 OO방에 공유" 안내를
            # 그릴 수 있어야 사용자가 등록할 때 켠 토글을 확정 직전에 다시 확인할 수 있다.
            "pending_share_room",
            "wardrobe_hashtags",
        ]
        read_only_fields = [
            "id", "job", "s3_key", "seg_meta", "created_at", "added_to_closet_at",
            "pending_share_room",
        ]

    def get_image_url(self, obj) -> str:
        return storage.presigned_get(obj.s3_key)

    @extend_schema_field(WardrobeHashtagSummarySerializer(many=True))
    def get_wardrobe_hashtags(self, obj) -> list[dict]:
        """개인 정리 정보는 요청 사용자 본인의 아이템 응답에만 포함한다."""

        request = self.context.get("request")
        if request is None or obj.user_id != getattr(request.user, "pk", None):
            return []
        return WardrobeHashtagSummarySerializer(
            obj.wardrobe_hashtags.all(),
            many=True,
        ).data


class WardrobeItemUpdateSerializer(serializers.ModelSerializer):
    """PATCH /wardrobe/items/{id}/ — 태깅 수정 + 확정."""

    class Meta:
        model = WardrobeItem
        fields = [
            "item_name", "category_large", "category_small", "season", "style",
            "color", "pattern", "fit", "material", "sleeve", "length", "usage",
            "layer_role", "layer_order", "confirmed", "is_favorite",
        ]

    def validate(self, attrs):
        large = attrs.get("category_large", self.instance.category_large)
        small = attrs.get("category_small", self.instance.category_small)
        if large not in T.CATEGORY_LARGE:
            raise serializers.ValidationError({"category_large": "유효하지 않은 대분류입니다."})
        if small and not T.is_valid_pair(large, small):
            raise serializers.ValidationError(
                {"category_small": f"'{large}'에 속하지 않는 소분류입니다."}
            )
        return attrs


# ── job 상태 조회 ─────────────────────────────────────────
class WardrobeJobSerializer(serializers.ModelSerializer):
    job_id = serializers.UUIDField(source="id", read_only=True)
    file_name = serializers.CharField(source="original_file_name", read_only=True)
    items = WardrobeItemSerializer(many=True, read_only=True)

    class Meta:
        model = WardrobeUploadJob
        fields = ["id", "job_id", "file_name", "status", "error_message",
                  "created_at", "finished_at", "items"]


# ── 이미지 프로세서 콜백 ──────────────────────────────────
class CallbackItemSerializer(serializers.Serializer):
    """콜백 페이로드의 아이템 1건. 벡터는 DB가 아닌 Qdrant로만 간다."""

    s3_key = serializers.CharField(max_length=512)
    item_name = serializers.CharField(max_length=120, allow_blank=True, default="")
    category_large = serializers.ChoiceField(choices=T.CATEGORY_LARGE)
    category_small = serializers.CharField(allow_blank=True, default="")
    season = serializers.ListField(
        child=serializers.ChoiceField(choices=T.SEASONS), default=list
    )
    style = serializers.ListField(
        child=serializers.ChoiceField(choices=T.STYLES), default=list
    )
    color = serializers.CharField(allow_blank=True, default="")
    pattern = serializers.CharField(allow_blank=True, default="")
    fit = serializers.CharField(allow_blank=True, allow_null=True, default="")
    material = serializers.CharField(allow_blank=True, allow_null=True, default="")
    sleeve = serializers.CharField(allow_blank=True, allow_null=True, default="")
    length = serializers.CharField(allow_blank=True, allow_null=True, default="")
    usage = serializers.ListField(child=serializers.CharField(), default=list)
    layer_role = serializers.CharField(allow_blank=True, allow_null=True, default="")
    layer_order = serializers.IntegerField(allow_null=True, default=None)
    seg_meta = serializers.JSONField(default=dict)
    image_vector = serializers.ListField(
        child=serializers.FloatField(), allow_empty=True, default=list
    )
    text_vector = serializers.ListField(
        child=serializers.FloatField(), allow_empty=True, default=list
    )

    def validate(self, attrs):
        # 소분류가 오면 대분류와의 짝만 검사 (미지정은 허용 — 사용자 확인 단계에서 보정)
        small = attrs.get("category_small") or ""
        if small and not T.is_valid_pair(attrs["category_large"], small):
            raise serializers.ValidationError(
                {"category_small": f"'{attrs['category_large']}'에 속하지 않는 소분류입니다."}
            )
        # null 허용 필드를 저장용 빈 문자열로 정규화
        for f in ("fit", "material", "sleeve", "length", "layer_role"):
            if attrs.get(f) is None:
                attrs[f] = ""
        return attrs


class CallbackSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["processing", "success", "failed"])
    error = serializers.CharField(allow_blank=True, default="")
    items = CallbackItemSerializer(many=True, default=list)


class WardrobeReindexCallbackSerializer(serializers.Serializer):
    """GPU 재인덱싱 워커가 돌려주는 기존 아이템 벡터."""

    item_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["success", "failed"])
    source_updated_at = serializers.DateTimeField()
    embedding_version = serializers.CharField(
        max_length=40,
        allow_blank=True,
        default="",
    )
    error = serializers.CharField(
        max_length=2000,
        allow_blank=True,
        default="",
    )
    image_vector = serializers.ListField(
        child=serializers.FloatField(),
        allow_empty=True,
        default=list,
    )
    text_vector = serializers.ListField(
        child=serializers.FloatField(),
        allow_empty=True,
        default=list,
    )

    def validate(self, attrs):
        if attrs["status"] != "success":
            return attrs

        expected_sizes = {
            "image_vector": settings.QDRANT_IMAGE_VECTOR_DIM,
            "text_vector": settings.QDRANT_TEXT_VECTOR_DIM,
        }
        errors = {}
        for field, expected_size in expected_sizes.items():
            vector = attrs[field]
            if len(vector) != expected_size:
                errors[field] = f"벡터 차원은 {expected_size}이어야 합니다."
            elif not all(math.isfinite(value) for value in vector):
                errors[field] = "벡터 값은 모두 유한한 숫자여야 합니다."
        if not attrs["embedding_version"]:
            errors["embedding_version"] = "성공 콜백에는 임베딩 버전이 필요합니다."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


# ── 공유 옷장 (Shared Wardrobe) 시리얼라이저 ─────────────────
from django.contrib.auth import get_user_model
from .models import (
    SharedWardrobeCategory,
    SharedWardrobeItem,
    SharedWardrobeMember,
    SharedWardrobeRoom,
)
from .services.reference_eligibility import (
    REFERENCE_UNAVAILABLE_REASON_CHOICES,
    evaluate_reference_eligibility,
)

User = get_user_model()

#: 로그인 방식이 만들어 준 내부 식별자. 사람 이름이 아니라 화면에 쓰면 안 된다.
#: 이메일 가입 `email_<uuid>` · 소셜 `<provider>_<id>`.
AUTO_USERNAME_RE = re.compile(r"^(email|naver|kakao|google|apple)_")


class UserSimpleSerializer(serializers.ModelSerializer):
    """공유 옷장에서 '누구인지' 보여줄 때 쓰는 최소 사용자 정보.

    `username`을 그대로 화면에 쓰면 이메일 가입자 아바타가 전부 'e'로,
    카카오 가입자는 전부 'k'로 보인다 — 첫 글자가 로그인 방식이기 때문이다.
    그래서 표시용 이름을 서버가 정해 `display_name`으로 내려준다.

    규칙은 앱 프로필 화면(`mobile/src/app/edit-profile.tsx` `accountName`)과 **같다**:
    별명(자동 생성 제외) → 이메일 아이디 → 그래도 없으면 '멤버'.
    두 곳이 어긋나면 "마이에서 보이는 내 이름"과 "공유방에서 남에게 보이는 내 이름"이
    달라진다.
    """

    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "nickname", "display_name", "email"]

    def get_display_name(self, obj) -> str:
        nickname = (obj.nickname or "").strip()
        if nickname and not AUTO_USERNAME_RE.match(nickname):
            return nickname

        email = (obj.email or "").strip()
        if email:
            return email.split("@")[0]

        username = (obj.username or "").strip()
        # 자동 생성 식별자면 이름 대신 중립 라벨을 준다 — 'e' 아바타보다 낫다.
        if username and not AUTO_USERNAME_RE.match(username):
            return username

        return "멤버"


class SharedWardrobeRoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = SharedWardrobeRoom
        fields = ["id", "title", "invite_code", "code_expires_at", "created_at"]
        read_only_fields = ["id", "invite_code", "code_expires_at", "created_at"]


class SharedWardrobeMemberSerializer(serializers.ModelSerializer):
    user = UserSimpleSerializer(read_only=True)

    class Meta:
        model = SharedWardrobeMember
        fields = ["id", "user", "role", "joined_at"]


class SharedWardrobeItemSerializer(serializers.ModelSerializer):
    wardrobe_item = WardrobeItemSerializer(read_only=True)
    registered_by = UserSimpleSerializer(read_only=True)
    reference_eligible = serializers.SerializerMethodField(
        help_text=(
            "현재 채팅 추천의 공유 옷 레퍼런스로 선택할 수 있는지 여부. "
            "전송 시 서버가 권한과 준비 상태를 다시 검증함"
        ),
    )
    reference_unavailable_reason = serializers.SerializerMethodField(
        help_text=(
            "선택 불가 사유 코드. NOT_CONFIRMED, VECTOR_NOT_READY 중 "
            "하나이며 선택 가능하면 null"
        ),
    )

    class Meta:
        model = SharedWardrobeItem
        fields = [
            "id",
            "registered_by",
            "wardrobe_item",
            "reference_eligible",
            "reference_unavailable_reason",
            "created_at",
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_reference_eligible(self, obj) -> bool:
        return self._reference_eligibility(obj).eligible

    @extend_schema_field(
        serializers.ChoiceField(
            choices=REFERENCE_UNAVAILABLE_REASON_CHOICES,
            allow_null=True,
        )
    )
    def get_reference_unavailable_reason(self, obj) -> str | None:
        return self._reference_eligibility(obj).unavailable_reason

    def _reference_eligibility(self, obj):
        resolved = self.context.get("reference_eligibilities", {})
        eligibility = resolved.get(str(obj.pk))
        if eligibility is not None:
            return eligibility
        return evaluate_reference_eligibility(obj)


class SharedWardrobeJoinSerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=6, min_length=6, write_only=True)


class SharedWardrobeLeaveSerializer(serializers.Serializer):
    delete_my_items = serializers.BooleanField(default=True)


class SharedWardrobeItemRegisterSerializer(serializers.Serializer):
    wardrobe_item_id = serializers.UUIDField()


class SharedWardrobeCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SharedWardrobeCategory
        fields = ["id", "name", "created_by", "created_at"]
        read_only_fields = ["id", "created_by", "created_at"]

    def validate_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("카테고리 이름을 입력해 주세요.")
        if name in T.CATEGORY_LARGE:
            raise serializers.ValidationError("기본 카테고리는 다시 추가할 수 없습니다.")
        return name


class SharedWardrobeCategoryDeleteSerializer(serializers.Serializer):
    category_id = serializers.UUIDField()


# ── 비로그인 초대 미리보기 (구경 모드) ─────────────────────
#
# 초대 링크만 있으면 로그인 없이 방을 둘러볼 수 있다. 열람 전용이며 서버에
# 아무 레코드도 남기지 않는다 (익명 User·멤버십 생성 금지 — 정원 6명 카운트와
# 방장 위임 대상이 오염된다).
#
# 소유자는 실명 대신 가입 순서 기반 라벨로 치환해서 내린다. 인덱스가 프론트의
# MEMBER_COLORS와 1:1로 맞으므로(0=노랑 … 5=주황) 이름과 색이 같은 순서를
# 공유하게 되고, 로그인 화면과 구경 화면의 아바타가 어긋나지 않는다.
ANON_MEMBER_LABELS = ["다람쥐", "고래", "여우", "판다", "펭귄", "너구리"]


def anon_member_label(index: int) -> str:
    return ANON_MEMBER_LABELS[index % len(ANON_MEMBER_LABELS)]


class SharedWardrobePreviewMemberSerializer(serializers.Serializer):
    """비로그인용 멤버 표시. PK·실명·이메일을 의도적으로 제외한다."""

    index = serializers.IntegerField(help_text="가입 순서(0-base). 아바타 색상 인덱스")
    label = serializers.CharField(help_text="방 안에서만 쓰는 익명 라벨")
    role = serializers.CharField(help_text="owner / member")


class SharedWardrobePreviewItemSerializer(serializers.Serializer):
    """비로그인용 아이템 표시. 옷 UUID를 안 내려 쓰기 경로를 원천 차단한다."""

    image_url = serializers.CharField()
    item_name = serializers.CharField(allow_null=True)
    category_large = serializers.CharField(allow_null=True)
    color = serializers.CharField(allow_null=True)
    owner_index = serializers.IntegerField(allow_null=True)
    owner_label = serializers.CharField(allow_null=True)


class SharedWardrobePreviewSerializer(serializers.Serializer):
    """GET /shared-wardrobes/preview/?code= 응답 (문서화용).

    방 UUID를 내리지 않는다 — 익명 사용자가 멤버 전용 엔드포인트
    (/shared-wardrobes/{id}/items/ 등)의 주소를 알 이유가 없다.
    """

    title = serializers.CharField()
    member_count = serializers.IntegerField()
    capacity = serializers.IntegerField()
    can_join = serializers.BooleanField(help_text="정원이 남아 있고 만료되지 않았는가")
    expired = serializers.BooleanField()
    members = SharedWardrobePreviewMemberSerializer(many=True)
    items = SharedWardrobePreviewItemSerializer(many=True)
