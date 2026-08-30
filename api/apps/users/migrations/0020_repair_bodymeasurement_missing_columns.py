"""적용 이력과 실제 body_measurements 스키마가 어긋난 환경을 복구한다.

과거 migration 파일이 적용 후 변경된 환경에서는 Django migration state에는
필드가 있지만 실제 PostgreSQL 컬럼은 없을 수 있다. 이미 정상인 환경에서도
안전하도록 IF NOT EXISTS를 사용하고 state는 변경하지 않는다.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0019_alter_bodymeasurement_thigh_calf_ratio_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE body_measurements
                    ADD COLUMN IF NOT EXISTS thigh numeric(4, 1) NULL,
                    ADD COLUMN IF NOT EXISTS calf numeric(4, 1) NULL,
                    ADD COLUMN IF NOT EXISTS arm numeric(4, 1) NULL,
                    ADD COLUMN IF NOT EXISTS torso_length numeric(4, 1) NULL,
                    ADD COLUMN IF NOT EXISTS leg_length numeric(4, 1) NULL;

                COMMENT ON COLUMN body_measurements.thigh IS '허벅지둘레(cm)';
                COMMENT ON COLUMN body_measurements.calf IS '종아리둘레(cm)';
                COMMENT ON COLUMN body_measurements.arm IS '팔뚝둘레(cm)';
                COMMENT ON COLUMN body_measurements.torso_length
                    IS '패션용 상체 길이감(cm, 어깨선→골반점)';
                COMMENT ON COLUMN body_measurements.leg_length
                    IS '패션용 하체 길이감(cm, 샅선/인심 라인→복사뼈/발목)';
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
