import uuid

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from apps.recommend.services.body_profile import (
    SILHOUETTE_LABELS,
    UNKNOWN,
    build_profile,
)
from apps.users.constants import (
    BUDGET_CATEGORIES,
    PREFERENCE_CATEGORIES,
    category_keys,
    effective_category_budgets,
)
from apps.users.services import profile_image as profile_image_service
from apps.users.models import (
    BodyMeasurement,
    BodyPhotoTransaction,
    PreferenceOption,
    Pursuit,
    SocialAccount,
    User,
)


class SocialLoginSerializer(serializers.Serializer):
    """POST /auth/{provider}/login 요청 바디.

    code(인가 코드 방식) 또는 access_token(토큰 방식, 카카오 네이티브 앱 SDK 전용)
    중 하나는 반드시 있어야 한다.
    """

    code = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="인가 코드 (code 방식). naver/google/kakao 웹 로그인에서 필수.",
    )
    # token 방식: 네이티브 앱 SDK는 인가 코드를 노출하지 않고 access_token을
    # 직접 반환하므로, 앱은 이 값을 백엔드로 전달한다.
    # 주의: naver는 발급 앱 검증이 불가해 토큰 유효성·사용자 식별만 수행한다.
    access_token = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="token 방식 전용. 네이티브 앱 SDK가 발급한 제공사 access token.",
    )
    # 카카오/구글은 토큰 교환 시 인가 요청과 동일한 redirect_uri가 필요하다.
    redirect_uri = serializers.URLField(
        required=False,
        allow_blank=True,
        help_text="kakao(code 방식)/google 필수. 인가 요청 시 사용한 값과 동일해야 함.",
    )
    # 네이버는 state 검증을 사용한다.
    state = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="naver 필수. 인가 요청 시 보낸 CSRF 방지용 state 값.",
    )
    # 애플 전용: 최초 로그인 시 Apple SDK가 전달하는 사용자 이름 (이후 로그인엔 빈값).
    user_name = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="apple 전용 (구현 보류). 최초 로그인 시 Apple이 전달하는 사용자 이름.",
    )

    def validate(self, attrs):
        if not attrs.get("code") and not attrs.get("access_token"):
            raise serializers.ValidationError("code 또는 access_token 중 하나가 필요합니다.")
        return attrs


class EmailSignupSerializer(serializers.Serializer):
    """이메일·비밀번호 회원가입 요청."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value: str) -> str:
        email = value.strip().lower()
        # 소셜 계정은 unusable password를 사용하므로 같은 이메일의 이메일 계정과
        # 자동 연결하지 않는다. 실제 비밀번호 계정의 중복만 차단한다.
        if User.objects.filter(email__iexact=email).exclude(password__startswith="!").exists():
            raise serializers.ValidationError("이미 가입된 이메일입니다.")
        return email

    def validate_password(self, value: str) -> str:
        """이메일과 비슷한 비밀번호까지 걸러낸다.

        `validate_password(value)` 만 부르면 user=None 이라 UserAttributeSimilarityValidator가
        비교할 대상이 없어 **이메일을 그대로 비밀번호로 써도 통과**한다(앱 가입 화면은
        "이메일과 비슷하지 않을 것"이라 안내하고 있어 말과 동작이 달랐다).
        가입 시점에는 아직 저장된 인스턴스가 없으므로, 제출된 이메일만 담은 임시 User로
        대신한다 — 저장하지 않고 검증에만 쓴다.
        """
        email = str(self.initial_data.get("email") or "").strip().lower()
        validate_password(value, User(email=email) if email else None)
        return value

    @transaction.atomic
    def create(self, validated_data: dict) -> User:
        return User.objects.create_user(
            username=f"email_{uuid.uuid4().hex}",
            email=validated_data["email"],
            password=validated_data["password"],
            is_active=False,
        )


class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "6자리 숫자 인증 코드를 입력해 주세요."})

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class EmailVerificationResendSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class EmailLoginSerializer(serializers.Serializer):
    """이메일·비밀번호 로그인 요청."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        email = attrs["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).exclude(password__startswith="!").first()
        if user is None or not user.check_password(attrs["password"]):
            raise serializers.ValidationError("이메일 또는 비밀번호가 올바르지 않습니다.")
        if not user.is_active:
            raise serializers.ValidationError("이메일 인증을 완료해 주세요.")
        attrs["user"] = user
        return attrs


class SocialAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = SocialAccount
        fields = ["provider", "email", "connected_at"]


class BudgetSerializer(serializers.ModelSerializer):
    """GET/PUT /users/me/budget/ — 대분류별 상품 1개 최대 가격."""

    category_budgets = serializers.DictField(
        child=serializers.IntegerField(
            min_value=10_000,
            max_value=2_147_480_000,
        ),
        required=True,
        help_text=(
            "대분류별 상품 1개 최대 가격(원). 값은 1만원 단위이며, "
            "미설정 카테고리는 키를 생략합니다. 빈 객체는 모든 예산을 기본값으로 되돌립니다."
        ),
    )
    effective_category_budgets = serializers.SerializerMethodField(
        help_text="시스템 기본값과 사용자 설정을 합친 실제 추천 가격 상한"
    )

    class Meta:
        model = User
        fields = ["category_budgets", "effective_category_budgets"]
        read_only_fields = ["effective_category_budgets"]

    def get_effective_category_budgets(self, obj: User) -> dict[str, int]:
        return effective_category_budgets(obj.category_budgets)

    def validate_category_budgets(self, value: dict[str, int]) -> dict[str, int]:
        unknown = set(value) - set(BUDGET_CATEGORIES)
        if unknown:
            raise serializers.ValidationError(
                f"지원하지 않는 대분류입니다: {', '.join(sorted(unknown))}"
            )
        if any(amount % 10_000 != 0 for amount in value.values()):
            raise serializers.ValidationError("예산은 1만원 단위로 입력해주세요.")
        return value


class UserSerializer(serializers.ModelSerializer):
    social_accounts = SocialAccountSerializer(many=True, read_only=True)
    # 직접 올린 사진(S3 key)이 있으면 그것을, 없으면 소셜 provider 가 준 URL 을 준다.
    # 프론트는 예전과 똑같이 profile_image 한 자리만 읽으면 된다.
    profile_image = serializers.SerializerMethodField()
    # 프론트가 '기본으로 되돌리기' 를 보여줄지 정하려면 소셜 사진과 구분해야 한다.
    # presigned URL 을 문자열로 넘겨짚는 것보다 서버가 사실대로 알려주는 편이 정확하다.
    profile_image_uploaded = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "nickname",
            "profile_image", "profile_image_uploaded", "social_accounts",
        ]
        read_only_fields = ["id", "username", "email", "social_accounts"]

    def get_profile_image(self, obj: User) -> str:
        if obj.profile_image_key:
            # presigned URL 은 만료된다(기본 1시간). 오래 캐시하지 말 것.
            try:
                return profile_image_service.presigned_get(obj.profile_image_key)
            except profile_image_service.ProfileImageConfigurationError:
                # S3 가 설정되지 않은 환경(로컬 등)에서 프로필 조회 자체가 실패하면 안 된다.
                return obj.profile_image or ""
        return obj.profile_image or ""

    def get_profile_image_uploaded(self, obj: User) -> bool:
        return bool(obj.profile_image_key)


BODY_BASIC_FIELDS = ["gender", "height", "weight"]
# 2026-08-12: 기존 둘레 7개는 유지하고 길이 4개를 **추가**했다 (상세 14개).
# 앞 7개만 화면에 보이고, *_length 는 응답에만 실린다 — 프론트는 아직 쓰지 않는다.
BODY_DETAIL_FIELDS = [
    "chest", "waist", "hip", "thigh", "calf", "arm", "shoulder",
    "thigh_length", "calf_length", "torso_length", "leg_length",
    "neck_length", "thigh_calf_ratio", "torso_leg_ratio"
]


class BodyMeasurementSerializer(serializers.ModelSerializer):
    """신체치수 조회/저장 결과 응답 (기본 + 상세 전체).

    gender는 미입력 상태(기존 행)면 빈 문자열 대신 null로 내려 다른 미입력
    필드(height 등)와 표현을 통일한다.
    """

    gender = serializers.SerializerMethodField()
    body_type = serializers.SerializerMethodField()
    body_type_label = serializers.SerializerMethodField()

    class Meta:
        model = BodyMeasurement
        fields = [
            *BODY_BASIC_FIELDS,
            *BODY_DETAIL_FIELDS,
            "body_type",
            "body_type_label",
            "updated_at",
        ]
        read_only_fields = ["body_type", "body_type_label", "updated_at"]

    def get_gender(self, obj) -> str | None:
        return obj.gender or None

    def _body_profile(self, obj) -> object:
        cache = getattr(self, "_body_profile_cache", {})
        key = id(obj)
        if key in cache:
            return cache[key]
        profile = build_profile(
            {
                name: getattr(obj, name, None)
                for name in [*BODY_BASIC_FIELDS, *BODY_DETAIL_FIELDS]
            }
        )
        cache[key] = profile
        self._body_profile_cache = cache
        return profile

    def get_body_type(self, obj) -> str | None:
        silhouette = self._body_profile(obj).silhouette
        return None if silhouette == UNKNOWN else silhouette

    def get_body_type_label(self, obj) -> str | None:
        silhouette = self._body_profile(obj).silhouette
        return None if silhouette == UNKNOWN else SILHOUETTE_LABELS[silhouette]


class BodyBasicInputSerializer(serializers.ModelSerializer):
    """PUT /users/me/body/basic — 성별·키·몸무게. 셋 다 필수.

    gender는 male|female. 모델은 기존 행 호환으로 빈 값을 허용하지만
    API 입력에서는 필수·비어있음 불가로 강제한다.
    """

    class Meta:
        model = BodyMeasurement
        fields = BODY_BASIC_FIELDS
        extra_kwargs = {
            "gender": {"required": True, "allow_blank": False},
            "height": {"required": True, "allow_null": False},
            "weight": {"required": True, "allow_null": False},
        }


class BodyDetailInputSerializer(serializers.ModelSerializer):
    """PATCH /users/me/body/detail — 상세 치수·체형 지표. 전부 선택 입력.

    보낸 필드만 갱신하며(partial), null을 보내면 해당 값을 지운다.
    """

    class Meta:
        model = BodyMeasurement
        fields = BODY_DETAIL_FIELDS


class BodyPhotoUploadSerializer(serializers.Serializer):
    """POST /users/me/body/photos — 정면/측면 사진 + 기본 정보 (multipart/form-data).

    사진은 디스크·DB에 저장하지 않고 추론에만 쓴다. 성별·키·몸무게는 생략 가능하며,
    생략하면 저장된 기본 신체치수를 사용한다 (무사진 추정 API와 동일한 규칙).
    """

    MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB

    front_image = serializers.ImageField(help_text="정면 전신 사진 (10MB 이하)")
    side_image = serializers.ImageField(help_text="측면 전신 사진 (10MB 이하)")
    gender = serializers.ChoiceField(
        choices=BodyMeasurement.Gender.choices,
        required=False,
        help_text="male 또는 female. 생략 시 저장된 값 사용.",
    )
    height = serializers.DecimalField(
        max_digits=4, decimal_places=1, min_value=100, max_value=230,
        required=False, help_text="키(cm). 생략 시 저장된 값 사용.",
    )
    weight = serializers.DecimalField(
        max_digits=4, decimal_places=1, min_value=25, max_value=300,
        required=False, help_text="몸무게(kg). 생략 시 저장된 값 사용.",
    )

    def _validate_size(self, image):
        if image.size > self.MAX_UPLOAD_SIZE:
            raise serializers.ValidationError("사진은 10MB 이하여야 합니다.")
        return image

    def validate_front_image(self, image):
        return self._validate_size(image)

    def validate_side_image(self, image):
        return self._validate_size(image)


class BodyEstimateInputSerializer(serializers.Serializer):
    """POST /users/me/body/estimate — 성별·키·몸무게로 상세 치수 추정.

    세 값 모두 생략 가능하다. 생략하면 이미 저장된 기본 신체치수
    (PUT /users/me/body/basic 으로 입력한 값)를 사용한다. 값을 보내면
    그 값으로 추정하고, 저장된 기본 치수도 함께 갱신한다.
    """

    gender = serializers.ChoiceField(
        choices=BodyMeasurement.Gender.choices,
        required=False,
        help_text="male 또는 female. 생략 시 저장된 값 사용.",
    )
    height = serializers.DecimalField(
        max_digits=4, decimal_places=1, min_value=100, max_value=230,
        required=False, help_text="키(cm). 생략 시 저장된 값 사용.",
    )
    weight = serializers.DecimalField(
        max_digits=4, decimal_places=1, min_value=25, max_value=300,
        required=False, help_text="몸무게(kg). 생략 시 저장된 값 사용.",
    )


class BodyEstimationResultSerializer(serializers.Serializer):
    """두 추정 API가 공유하는 결과 형식.

    사진 유무와 무관하게 프론트가 같은 파서로 처리할 수 있도록, 무사진 추정
    응답과 사진 측정 트랜잭션 조회 응답이 모두 이 형태를 사용한다.
    POST 자체는 동기(200)/비동기(202)로 다를 수밖에 없어 '결과'만 통일한다.
    """

    status = serializers.ChoiceField(
        choices=BodyPhotoTransaction.Status.choices,
        help_text="in_progress | succeeded | failed",
    )
    source = serializers.ChoiceField(
        choices=[("basic_info", "기본 정보"), ("photo", "사진")],
        help_text="추정에 사용한 입력 (basic_info | photo)",
    )
    transaction_id = serializers.UUIDField(
        allow_null=True, help_text="사진 측정일 때만 값이 있다. 무사진 추정은 null."
    )
    measurement = BodyMeasurementSerializer(
        help_text="추정된 패션용 체형 지표 11개 전체."
    )
    error_message = serializers.CharField(
        allow_null=True, help_text="실패했을 때만 사유가 들어간다."
    )
    error_code = serializers.CharField(
        allow_null=True,
        help_text="클라이언트 분기용 실패 코드 (사진 품질 실패: photo_quality_failed).",
    )


class BodyPhotoTransactionSerializer(serializers.ModelSerializer):
    """사진 측정 트랜잭션 상태 응답 (GET /users/me/body/photos/{transaction_id})."""

    transaction_id = serializers.UUIDField(source="id", read_only=True)

    class Meta:
        model = BodyPhotoTransaction
        fields = [
            "transaction_id", "status", "error_message", "error_code", "created_at", "updated_at"
        ]
        read_only_fields = fields



# 추구미 (Pursuit) — 옵션 마스터 / 사용자 선택

class PreferenceOptionItemSerializer(serializers.Serializer):
    """개별 옵션 1개. DB 모델과 1:1 매핑되지 않는 가벼운 serializer."""

    code = serializers.CharField()
    label = serializers.CharField()
    meta = serializers.JSONField(required=False, default=dict)


class PreferenceCategorySerializer(serializers.Serializer):
    """카테고리 1개 — key/label/options 묶음.

    GET /api/v1/preference-options/ 응답에 들어가는 한 카테고리 단위.
    """

    key = serializers.CharField()
    label = serializers.CharField()
    options = PreferenceOptionItemSerializer(many=True)


def _build_pursuit_payload_field(*, required: bool) -> serializers.DictField:
    """preferred/avoided 페이로드 필드 빌더.

    PREFERENCE_CATEGORIES 11개 키에 대해 각 키가 ListField(str, allow_empty=True).
    required 인자에 따라 입력 검증(required) 또는 응답(read_only) 형태로 사용.
    """
    child = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        allow_empty=True,
    )
    fields = {key: child for key, _ in PREFERENCE_CATEGORIES}
    # DRF는 `required=True` + `default=...` 동시 지정 불가.
    # → required 케이스에서 default 키워드 자체를 넘기지 말아야 함.
    if required:
        return serializers.DictField(child=child, required=True)
    return serializers.DictField(child=child, required=False, default=dict)


class PursuitPayloadInputSerializer(serializers.Serializer):
    """PUT /api/v1/users/me/pursuit/ 요청 바디.

    preferred/avoided 두 그룹.
    각 그룹은 PREFERENCE_CATEGORIES 11개 키 모두 가져야 함.
    각 값은 선택된 옵션 code 배열 (빈 배열 허용).
    """

    preferred = _build_pursuit_payload_field(required=True)
    avoided = _build_pursuit_payload_field(required=True)

    def validate_preferred(self, value):
        # 11개 카테고리 키 모두 있어야 함
        return self._validate_payload_group(value, "preferred")

    def validate_avoided(self, value):
        return self._validate_payload_group(value, "avoided")

    @staticmethod
    def _validate_payload_group(value: dict, group_name: str) -> dict:
        expected = set(category_keys())
        given = set(value.keys() if isinstance(value, dict) else [])
        missing = expected - given
        extra = given - expected
        if missing:
            raise serializers.ValidationError(
                f"{group_name}에 누락된 카테고리: {sorted(missing)}"
            )
        if extra:
            # 알 수 없는 카테고리는 무시보다 명시적 에러가 안전
            raise serializers.ValidationError(
                f"{group_name}에 알 수 없는 카테고리: {sorted(extra)}"
            )
        # 각 카테고리는 배열 + 문자열만
        cleaned: dict = {}
        for key in category_keys():
            arr = value.get(key, [])
            if not isinstance(arr, list):
                raise serializers.ValidationError(
                    f"{group_name}.{key} 은(는) 배열이어야 합니다."
                )
            for v in arr:
                if not isinstance(v, str):
                    raise serializers.ValidationError(
                        f"{group_name}.{key} 의 원소는 문자열이어야 합니다: {v!r}"
                    )
            # 중복 제거 + 입력 순서 유지
            cleaned[key] = list(dict.fromkeys(arr))
        return cleaned


class PursuitPayloadResponseSerializer(serializers.Serializer):
    """GET /api/v1/users/me/style-preferences/ 응답.

    preferred/avoided 두 그룹. 
    각 그룹은 PREFERENCE_CATEGORIES 11개 키 모두 포함
    (없는 카테고리는 빈 배열로 채워짐 — 응답 일관성).
    """

    preferred = _build_pursuit_payload_field(required=False)
    avoided = _build_pursuit_payload_field(required=False)
