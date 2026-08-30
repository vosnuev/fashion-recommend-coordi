"""Development settings with OpenAPI schema and Swagger UI enabled."""

from .dev import *  # noqa: F401,F403

INSTALLED_APPS = [  # noqa: F405
    *INSTALLED_APPS,  # noqa: F405
    "drf_spectacular",
    "apps.api_docs",
]

ROOT_URLCONF = "config.urls_swagger"

REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

API_DESCRIPTION = """SKN28 개인화 패션 추천 서비스 API 문서

## 인증 구조 (JWT Bearer)

이 API는 헤더 기반 JWT 인증을 사용합니다.

1. **토큰 발급**: `POST /api/v1/auth/{provider}/login/` (소셜 로그인)이 서비스 자체
   JWT인 `access`/`refresh` 토큰을 반환합니다.
2. **인증 요청**: 보호된 엔드포인트는 `Authorization: Bearer <access>` 헤더를
   요구합니다. Swagger UI에서는 우측 상단 **Authorize** 버튼에 access 토큰을
   입력하면 요청에 자동으로 헤더가 붙습니다.
3. **토큰 갱신**: access 토큰 만료(기본 30분) 시 `POST /api/v1/auth/token/refresh/`로
   재발급합니다. refresh 토큰(기본 14일)은 회전 방식이라 갱신할 때마다 새
   refresh 토큰이 함께 발급되며, 이전 refresh 토큰은 블랙리스트 처리됩니다.

자물쇠 아이콘이 있는 엔드포인트가 인증 필수이고, 소셜 로그인·토큰 갱신은 인증
없이 호출할 수 있습니다.

## 채팅 API를 Swagger에서 테스트하는 순서

Swagger의 **채팅** 카테고리에 채팅 세션, 메시지, 사진 무드, 추천 결과, 피드백,
코디 이미지 API가 함께 정리돼 있습니다.

1. 비회원은 `POST /api/v1/chat/guest/`를 먼저 호출합니다. 응답의 HttpOnly 쿠키는
   같은 Swagger 브라우저의 다음 요청에 자동으로 포함됩니다. 회원은 우측 상단
   **Authorize**에 access JWT를 입력합니다.
2. `POST /api/v1/chat/sessions/`에서 추천 모드를 고르고 응답의 `id`를 이후
   `session_id` 경로 변수에 복사합니다.
3. `GET .../messages/`로 자동 저장된 첫 인사를 확인한 뒤 `POST .../messages/`로
   질문을 보냅니다. 응답의 `run.id`는 실행 상태 조회에 사용합니다.
4. `GET /api/v1/chat/runs/{run_id}/`를 반복 호출해 `SUCCEEDED`,
   `NEEDS_CLARIFICATION`, `FAILED` 중 하나가 됐는지 확인합니다.
5. 사진은 업로드 응답의 `attachment.id`, 추천은 이력 응답의 `result_id`와
   `card_id`, 이미지 생성은 응답의 `job_id`를 다음 API에 차례로 사용합니다.

문서에 보이는 UUID는 형식 예시이므로 그대로 호출하면 404가 정상입니다. 반드시
현재 Swagger 세션에서 앞선 API가 반환한 실제 UUID로 교체하세요. Redis worker,
S3, OpenAI·이미지 모델이 필요한 비동기 API는 로컬 인프라와 환경변수가 준비돼야
완료되며 실제 외부 모델 API 비용이 발생할 수 있습니다.

## 선택형 스타일리스트 API를 Swagger에서 테스트하는 순서

이 기능은 로그인 회원 전용입니다. 우측 상단 **Authorize**에 회원 access JWT를
입력한 뒤 **선택형 스타일리스트** 카테고리에서 다음 순서로 테스트합니다.

1. `GET /api/v1/chat/stylists/`로 선택 가능한 스타일리스트와 회원의 마지막 선택을
   확인합니다.
2. 아직 세션이 없다면 **채팅** 카테고리의 `POST /api/v1/chat/sessions/`를 호출하고
   응답의 `id`를 복사합니다.
3. `PATCH /api/v1/chat/sessions/{session_id}/response-mode/`의 `session_id`에 복사한
   값을 넣고 `STYLIST` 요청 예시를 실행합니다.
4. **채팅** 카테고리의 `POST .../messages/`로 메시지를 보내면 접수 시점의 모드와
   선택값이 `ChatRun`에 고정됩니다.
5. 다시 `PATCH .../response-mode/`에서 `DEFAULT` 예시를 실행해도 저장된 선택값과
   이미 접수된 실행 스냅샷은 유지됩니다.
"""

SPECTACULAR_SETTINGS = {
    "TITLE": "SKN28 Fashion Recommendation API",
    "DESCRIPTION": API_DESCRIPTION,
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "TAGS": [
        {
            "name": "채팅",
            "description": (
                "회원·비회원 채팅, 세션·메시지, 사진 무드, 추천 카드·피드백, "
                "최종 코디 이미지 생성 API"
            ),
        },
        {
            "name": "선택형 스타일리스트",
            "description": (
                "회원 전용 스타일리스트 목록 조회와 채팅 세션의 "
                "DEFAULT/STYLIST 응답 모드 변경 API"
            ),
        },
    ],
}
