from django.apps import AppConfig


class GoldensetConfig(AppConfig):
    """골든셋(판단 지식 구축) 전용 앱.

    apps.recommend와 분리해 둔 이유가 있다. recommend는 사용자 요청을 처리하는
    런타임 앱이고, 골든셋은 오프라인 파이프라인(ml/golden_set)이 만든 산출물을
    받아 적재·검수하는 데이터 앱이다. 두 앱의 마이그레이션 번호가 섞이면 서로의
    배포를 막는다 — 실제로 한 번 그 사고가 났다.

    app label이 곧 PostgreSQL 스키마 이름(goldenset)과 같다. models._table()이
    모든 테이블을 그 스키마에 두므로 public과 이름이 부딪히지 않는다.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.goldenset"
    label = "goldenset"
    verbose_name = "골든셋"
