# 모델로 커버할 수 없는 테이블·컬럼의 comment (raw SQL).
#
# 대상:
#   ① users 테이블의 AbstractUser 상속 컬럼 (모델에 재선언하지 않으므로 db_comment 불가)
#   ② User M2M 자동 생성 through 테이블 (users_groups, users_permissions)
#   ③ Django 기본/3rd-party 테이블 (auth_*, django_*, token_blacklist_*)
#
# 나머지 서비스 테이블·컬럼의 comment는 각 앱 모델의 db_table_comment/db_comment가
# 소유한다 (users 0009 / catalog 0003 / weather 0002 / wardrobe 0003).
# comment는 스키마 메타데이터일 뿐이라 Django 모델 state에는 영향이 없다.

from django.db import migrations


def _comments_sql(spec: dict[str, tuple[str, dict[str, str]]]) -> tuple[list, list]:
    """{테이블: (테이블 comment, {컬럼: comment})} → (적용 SQL, 롤백 SQL)."""
    sql: list[str] = []
    reverse: list[str] = []
    for table, (table_comment, columns) in spec.items():
        sql.append(f"COMMENT ON TABLE {table} IS '{table_comment}';")
        reverse.append(f"COMMENT ON TABLE {table} IS NULL;")
        for column, comment in columns.items():
            sql.append(f"COMMENT ON COLUMN {table}.{column} IS '{comment}';")
            reverse.append(f"COMMENT ON COLUMN {table}.{column} IS NULL;")
    return sql, reverse


# ① users의 AbstractUser 상속 컬럼 (테이블 comment는 0009의 db_table_comment가 소유)
_USER_INHERITED_COLUMNS = {
    "password": "비밀번호 해시 (소셜 로그인 전용이라 unusable password 고정)",
    "last_login": "마지막 로그인 시각 (Django 기본)",
    "is_superuser": "슈퍼유저 여부 (Django 권한)",
    "username": "내부 로그인 ID (provider_고유ID 형태로 자동 생성)",
    "first_name": "이름 (Django 기본, 미사용)",
    "last_name": "성 (Django 기본, 미사용)",
    "email": "이메일 (소셜 프로필에서 초기화)",
    "is_staff": "관리자 사이트 접근 가능 여부",
    "is_active": "계정 활성 여부 (false면 로그인 불가)",
    "date_joined": "가입(계정 생성) 시각",
}

# ②·③ 테이블 전체 (테이블 comment + 컬럼 comment)
_SYSTEM_TABLES: dict[str, tuple[str, dict[str, str]]] = {
    "users_groups": (
        "사용자-그룹 연결 (Django 권한 M2M, 자동 생성 through)",
        {
            "id": "연결 PK (자동 증가)",
            "user_id": "사용자 FK (users.id)",
            "group_id": "그룹 FK (auth_group.id)",
        },
    ),
    "users_permissions": (
        "사용자-개별권한 연결 (Django 권한 M2M, 자동 생성 through)",
        {
            "id": "연결 PK (자동 증가)",
            "user_id": "사용자 FK (users.id)",
            "permission_id": "권한 FK (auth_permission.id)",
        },
    ),
    "auth_group": (
        "Django 권한 그룹",
        {"id": "그룹 PK (자동 증가)", "name": "그룹 이름 (유일)"},
    ),
    "auth_group_permissions": (
        "그룹-권한 연결 (Django 권한 M2M)",
        {
            "id": "연결 PK (자동 증가)",
            "group_id": "그룹 FK (auth_group.id)",
            "permission_id": "권한 FK (auth_permission.id)",
        },
    ),
    "auth_permission": (
        "Django 개별 권한 (모델별 add/change/delete/view 자동 생성)",
        {
            "id": "권한 PK (자동 증가)",
            "name": "권한 표시 이름",
            "content_type_id": "대상 모델 FK (django_content_type.id)",
            "codename": "권한 코드명 (add_xxx 등)",
        },
    ),
    "django_content_type": (
        "Django 모델 레지스트리 (앱·모델명 매핑)",
        {
            "id": "콘텐츠타입 PK (자동 증가)",
            "app_label": "Django 앱 라벨",
            "model": "모델명 (소문자)",
        },
    ),
    "django_admin_log": (
        "Django 관리자 사이트 조작 이력",
        {
            "id": "로그 PK (자동 증가)",
            "action_time": "조작 시각",
            "object_id": "대상 객체 PK (문자열)",
            "object_repr": "대상 객체 표시 문자열",
            "action_flag": "조작 유형 (1: 추가 / 2: 수정 / 3: 삭제)",
            "change_message": "변경 내용 JSON",
            "content_type_id": "대상 모델 FK (django_content_type.id)",
            "user_id": "조작한 관리자 FK (users.id)",
        },
    ),
    "django_migrations": (
        "Django 마이그레이션 적용 이력",
        {
            "id": "이력 PK (자동 증가)",
            "app": "Django 앱 라벨",
            "name": "마이그레이션 파일명",
            "applied": "적용 시각",
        },
    ),
    "django_session": (
        "Django 세션 저장소 (관리자 사이트 로그인 등)",
        {
            "session_key": "세션 키 (PK)",
            "session_data": "직렬화된 세션 데이터",
            "expire_date": "세션 만료 시각",
        },
    ),
    "token_blacklist_outstandingtoken": (
        "발급된 JWT refresh 토큰 대장 (simplejwt token_blacklist)",
        {
            "id": "토큰 PK (자동 증가)",
            "user_id": "발급 대상 사용자 FK (users.id)",
            "jti": "JWT 고유 ID (jti 클레임, 유일)",
            "token": "refresh 토큰 원문",
            "created_at": "발급 시각",
            "expires_at": "만료 시각",
        },
    ),
    "token_blacklist_blacklistedtoken": (
        "차단된 refresh 토큰 (회전 후 재사용 차단 목록)",
        {
            "id": "차단 PK (자동 증가)",
            "token_id": "대상 토큰 FK (token_blacklist_outstandingtoken.id)",
            "blacklisted_at": "차단 시각",
        },
    ),
}


def _build() -> tuple[list, list]:
    sql = [
        f"COMMENT ON COLUMN users.{column} IS '{comment}';"
        for column, comment in _USER_INHERITED_COLUMNS.items()
    ]
    reverse = [
        f"COMMENT ON COLUMN users.{column} IS NULL;"
        for column in _USER_INHERITED_COLUMNS
    ]
    system_sql, system_reverse = _comments_sql(_SYSTEM_TABLES)
    return sql + system_sql, reverse + system_reverse


_SQL, _REVERSE_SQL = _build()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0009_table_column_comments"),
        # comment 대상 시스템 테이블이 먼저 생성돼 있어야 한다.
        ("auth", "0001_initial"),
        ("contenttypes", "0001_initial"),
        ("admin", "0001_initial"),
        ("sessions", "0001_initial"),
        ("token_blacklist", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=_SQL, reverse_sql=_REVERSE_SQL),
    ]
