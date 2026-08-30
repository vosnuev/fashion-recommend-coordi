"""기상청 수집 데이터 모델.

스키마 소유권은 Django migration에 있다. collector/weather는 raw SQL(psycopg2)로
upsert만 하므로, 모델 변경 시 weather_collector_db.py의 INSERT 컬럼도 함께 갱신한다.

collector가 INSERT 시 생략하는 컬럼(created_at/updated_at)은 db_default로
DB 기본값을 유지한다 (기존 DDL의 DEFAULT NOW()와 동일 동작).

테이블·컬럼 comment는 db_table_comment/db_comment로 모델이 소유한다
(새 필드 추가 시 반드시 db_comment 지정).
"""

from django.db import models
from django.db.models import Q
from django.db.models.functions import Now


class WeatherArea(models.Model):
    """전국 수집 대상 격자(GRID) / 중기 예보구역(MID_LAND, MID_TEMP) 마스터."""

    area_type = models.CharField(
        max_length=20, db_comment="구역 유형 (GRID: 단기 격자 / MID_LAND: 중기 육상 / MID_TEMP: 중기 기온)"
    )
    name = models.CharField(max_length=200, db_comment="구역 이름 (행정동/예보구역명)")
    nx = models.SmallIntegerField(null=True, blank=True, db_comment="기상청 격자 X 좌표 (GRID 전용)")
    ny = models.SmallIntegerField(null=True, blank=True, db_comment="기상청 격자 Y 좌표 (GRID 전용)")
    latitude = models.DecimalField(
        max_digits=10, decimal_places=6, null=True, blank=True, db_comment="위도"
    )
    longitude = models.DecimalField(
        max_digits=10, decimal_places=6, null=True, blank=True, db_comment="경도"
    )
    sido = models.CharField(max_length=60, null=True, blank=True, db_comment="시/도")
    sigungu = models.CharField(max_length=100, null=True, blank=True, db_comment="시/군/구")
    eupmyeondong = models.CharField(
        max_length=100, null=True, blank=True, db_comment="읍/면/동"
    )
    address_label = models.CharField(
        max_length=255, null=True, blank=True, db_comment="표시용 전체 주소 라벨"
    )
    reg_id = models.CharField(
        max_length=30, null=True, blank=True, db_comment="기상청 중기예보 구역 코드 (MID_* 전용)"
    )
    is_active = models.BooleanField(
        default=True, db_default=True, db_comment="수집 대상 여부 (false면 수집 제외)"
    )
    created_at = models.DateTimeField(db_default=Now(), db_comment="행 생성 시각")
    updated_at = models.DateTimeField(db_default=Now(), db_comment="행 수정 시각")

    class Meta:
        db_table = "weather_area"
        db_table_comment = "기상청 수집 대상 구역 마스터 (단기 격자 + 중기 예보구역)"
        verbose_name = "예보 구역"
        verbose_name_plural = "예보 구역"
        constraints = [
            # 기존 DDL의 부분 unique 인덱스와 동일 (collector upsert의 ON CONFLICT 대상)
            models.UniqueConstraint(
                fields=["area_type", "nx", "ny"],
                condition=Q(area_type="GRID"),
                name="ux_weather_area_grid",
            ),
            models.UniqueConstraint(
                fields=["area_type", "reg_id"],
                condition=Q(area_type__in=["MID_LAND", "MID_TEMP"]),
                name="ux_weather_area_mid",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.area_type}] {self.name}"


class WeatherNowcastRaw(models.Model):
    """실황 Raw."""

    area = models.ForeignKey(
        WeatherArea,
        on_delete=models.CASCADE,
        related_name="nowcasts",
        db_comment="예보 구역 FK (weather_area.id)",
    )
    base_datetime = models.DateTimeField(db_comment="발표 기준 시각 (base_date+base_time)")
    collected_at = models.DateTimeField(db_comment="수집 시각")
    temperature = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="기온 (℃, T1H)"
    )
    precipitation_type_code = models.CharField(
        max_length=20, null=True, blank=True, db_comment="강수 형태 코드 (PTY)"
    )
    precipitation_type_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="강수 형태 라벨 (없음/비/눈 등)"
    )
    precipitation_amount = models.CharField(
        max_length=50, null=True, blank=True, db_comment="1시간 강수량 (RN1, 범주 문자열)"
    )
    humidity = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="습도 (REH, 백분율)"
    )
    wind_speed = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="풍속 (m/s, WSD)"
    )
    wind_direction_deg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="풍향 (deg, VEC)"
    )
    wind_direction_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="풍향 라벨 (N/NE 등)"
    )
    raw_data = models.JSONField(default=dict, blank=True, db_comment="기상청 API 원본 응답 JSON")
    created_at = models.DateTimeField(db_default=Now(), db_comment="행 생성 시각")
    updated_at = models.DateTimeField(db_default=Now(), db_comment="행 수정 시각")

    class Meta:
        db_table = "weather_nowcast_raw"
        db_table_comment = "기상청 초단기실황 원본 (구역×발표시각당 1행)"
        constraints = [
            models.UniqueConstraint(
                fields=["area", "base_datetime"], name="uq_weather_nowcast"
            )
        ]


class WeatherVeryShortRaw(models.Model):
    """초단기예보 Raw."""

    area = models.ForeignKey(
        WeatherArea,
        on_delete=models.CASCADE,
        related_name="very_shorts",
        db_comment="예보 구역 FK (weather_area.id)",
    )
    base_datetime = models.DateTimeField(db_comment="발표 기준 시각 (base_date+base_time)")
    collected_at = models.DateTimeField(db_comment="수집 시각")
    forecast_date = models.DateField(db_comment="예보 대상 날짜 (fcstDate)")
    forecast_time = models.TimeField(db_comment="예보 대상 시각 (fcstTime)")
    temperature = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="기온 (℃, T1H)"
    )
    sky_code = models.CharField(max_length=20, null=True, blank=True, db_comment="하늘 상태 코드 (SKY)")
    sky_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="하늘 상태 라벨 (맑음/구름많음/흐림)"
    )
    precipitation_type_code = models.CharField(
        max_length=20, null=True, blank=True, db_comment="강수 형태 코드 (PTY)"
    )
    precipitation_type_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="강수 형태 라벨"
    )
    precipitation_amount = models.CharField(
        max_length=50, null=True, blank=True, db_comment="1시간 강수량 (RN1, 범주 문자열)"
    )
    humidity = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="습도 (REH, 백분율)"
    )
    wind_speed = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="풍속 (m/s, WSD)"
    )
    wind_direction_deg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="풍향 (deg, VEC)"
    )
    wind_direction_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="풍향 라벨"
    )
    raw_data = models.JSONField(default=dict, blank=True, db_comment="기상청 API 원본 응답 JSON")
    created_at = models.DateTimeField(db_default=Now(), db_comment="행 생성 시각")
    updated_at = models.DateTimeField(db_default=Now(), db_comment="행 수정 시각")

    class Meta:
        db_table = "weather_very_short_raw"
        db_table_comment = "기상청 초단기예보 원본 (구역×발표시각×예보시각당 1행)"
        constraints = [
            models.UniqueConstraint(
                fields=["area", "base_datetime", "forecast_date", "forecast_time"],
                name="uq_weather_very_short",
            )
        ]


class WeatherShortRaw(models.Model):
    """단기예보 Raw."""

    area = models.ForeignKey(
        WeatherArea,
        on_delete=models.CASCADE,
        related_name="shorts",
        db_comment="예보 구역 FK (weather_area.id)",
    )
    base_datetime = models.DateTimeField(db_comment="발표 기준 시각 (base_date+base_time)")
    collected_at = models.DateTimeField(db_comment="수집 시각")
    forecast_date = models.DateField(db_comment="예보 대상 날짜 (fcstDate)")
    forecast_time = models.TimeField(db_comment="예보 대상 시각 (fcstTime)")
    temperature = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="기온 (℃, TMP)"
    )
    min_temperature = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="일 최저기온 (℃, TMN)"
    )
    max_temperature = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="일 최고기온 (℃, TMX)"
    )
    sky_code = models.CharField(max_length=20, null=True, blank=True, db_comment="하늘 상태 코드 (SKY)")
    sky_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="하늘 상태 라벨 (맑음/구름많음/흐림)"
    )
    precipitation_type_code = models.CharField(
        max_length=20, null=True, blank=True, db_comment="강수 형태 코드 (PTY)"
    )
    precipitation_type_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="강수 형태 라벨"
    )
    precipitation_probability = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="강수 확률 (POP, 백분율)"
    )
    precipitation_amount = models.CharField(
        max_length=50, null=True, blank=True, db_comment="1시간 강수량 (PCP, 범주 문자열)"
    )
    humidity = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="습도 (REH, 백분율)"
    )
    wind_speed = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="풍속 (m/s, WSD)"
    )
    wind_direction_deg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="풍향 (deg, VEC)"
    )
    wind_direction_label = models.CharField(
        max_length=50, null=True, blank=True, db_comment="풍향 라벨"
    )
    raw_data = models.JSONField(default=dict, blank=True, db_comment="기상청 API 원본 응답 JSON")
    created_at = models.DateTimeField(db_default=Now(), db_comment="행 생성 시각")
    updated_at = models.DateTimeField(db_default=Now(), db_comment="행 수정 시각")

    class Meta:
        db_table = "weather_short_raw"
        db_table_comment = "기상청 단기예보 원본 (구역×발표시각×예보시각당 1행)"
        constraints = [
            models.UniqueConstraint(
                fields=["area", "base_datetime", "forecast_date", "forecast_time"],
                name="uq_weather_short",
            )
        ]


class WeatherMidLandRaw(models.Model):
    """중기 육상예보 Raw."""

    class ForecastPeriod(models.TextChoices):
        AM = "AM"
        PM = "PM"

    area = models.ForeignKey(
        WeatherArea,
        on_delete=models.CASCADE,
        related_name="mid_lands",
        db_comment="예보 구역 FK (weather_area.id)",
    )
    base_datetime = models.DateTimeField(db_comment="발표 기준 시각 (tmFc)")
    collected_at = models.DateTimeField(db_comment="수집 시각")
    forecast_date = models.DateField(db_comment="예보 대상 날짜 (발표일 +4~+10일)")
    forecast_period = models.CharField(
        max_length=2, choices=ForecastPeriod.choices, db_comment="예보 구간 (AM: 오전 / PM: 오후)"
    )
    sky_label = models.CharField(
        max_length=100, null=True, blank=True, db_comment="날씨 예보 문구 (맑음/구름많고 비 등)"
    )
    precipitation_probability = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="강수 확률 (백분율)"
    )
    raw_data = models.JSONField(default=dict, blank=True, db_comment="기상청 API 원본 응답 JSON")
    created_at = models.DateTimeField(db_default=Now(), db_comment="행 생성 시각")
    updated_at = models.DateTimeField(db_default=Now(), db_comment="행 수정 시각")

    class Meta:
        db_table = "weather_mid_land_raw"
        db_table_comment = "기상청 중기 육상예보 원본 (구역×발표시각×예보일×오전/오후당 1행)"
        constraints = [
            models.UniqueConstraint(
                fields=["area", "base_datetime", "forecast_date", "forecast_period"],
                name="uq_weather_mid_land",
            ),
            models.CheckConstraint(
                condition=Q(forecast_period__in=["AM", "PM"]),
                name="ck_weather_mid_land_period",
            ),
        ]


class WeatherMidTempRaw(models.Model):
    """중기 기온예보 Raw."""

    area = models.ForeignKey(
        WeatherArea,
        on_delete=models.CASCADE,
        related_name="mid_temps",
        db_comment="예보 구역 FK (weather_area.id)",
    )
    base_datetime = models.DateTimeField(db_comment="발표 기준 시각 (tmFc)")
    collected_at = models.DateTimeField(db_comment="수집 시각")
    forecast_date = models.DateField(db_comment="예보 대상 날짜 (발표일 +4~+10일)")
    min_temperature = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="일 최저기온 (℃)"
    )
    max_temperature = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, db_comment="일 최고기온 (℃)"
    )
    raw_data = models.JSONField(default=dict, blank=True, db_comment="기상청 API 원본 응답 JSON")
    created_at = models.DateTimeField(db_default=Now(), db_comment="행 생성 시각")
    updated_at = models.DateTimeField(db_default=Now(), db_comment="행 수정 시각")

    class Meta:
        db_table = "weather_mid_temp_raw"
        db_table_comment = "기상청 중기 기온예보 원본 (구역×발표시각×예보일당 1행)"
        constraints = [
            models.UniqueConstraint(
                fields=["area", "base_datetime", "forecast_date"],
                name="uq_weather_mid_temp",
            )
        ]
