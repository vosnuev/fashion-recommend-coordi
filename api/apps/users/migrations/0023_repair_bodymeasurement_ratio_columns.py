"""0020과 같은 스키마 어긋남을 나머지 3개 컬럼에 대해서도 복구한다.

0020이 만들어진 뒤 body_measurements 를 백업본으로 되돌린 환경에서는
migration 이력상 0014/0019 가 적용됨([X])인데도 neck_length·thigh_calf_ratio·
torso_leg_ratio 컬럼이 실제로는 없어 `GET /api/v1/users/me/body/` 가
ProgrammingError(column does not exist)로 500 이 된다.

0020과 동일하게 IF NOT EXISTS 로만 손대고 migration state 는 바꾸지 않는다 —
정상인 환경에서는 아무 일도 일어나지 않는다.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0022_merge_category_budgets_body_measurement"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE body_measurements
                    ADD COLUMN IF NOT EXISTS neck_length numeric(4, 1) NULL,
                    ADD COLUMN IF NOT EXISTS thigh_calf_ratio numeric(5, 3) NULL,
                    ADD COLUMN IF NOT EXISTS torso_leg_ratio numeric(5, 3) NULL;

                COMMENT ON COLUMN body_measurements.neck_length
                    IS '패션용 목 길이감(cm, 정면 기준 턱밑/턱끝 라인→목앞/쇄골 라인)';
                COMMENT ON COLUMN body_measurements.thigh_calf_ratio
                    IS '패션용 허벅지 길이감 / 종아리 길이감 비율';
                COMMENT ON COLUMN body_measurements.torso_leg_ratio
                    IS '패션용 상체 길이감 / 하체 길이감 비율';
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
