# 일일 작업 보고서 (Daily Work Report)

> **역사 자료**: 2026-08-10 작업 시점의 정의와 수치를 그대로 보존했다. 최종 모델과 현재 수치는 최신 코드와 [`README.md`](../../../README.md)를 우선한다.

- **작성일**: 2026-08-10
- **작업 브랜치**: `body-measurement` / `goldenset`
- **목적**: 신체 세로축 3대 비율 지표(목길이, 허벅지/종아리 비율, 상하체 비율)의 시각적 정의 교정 및 API/DB 연동 정합성 검증 완료

---

## 1. 신체 세로축 3대 비율 지표 시각적 정의 교정

기존 의류학 학술용 다리 정의('샅높이' 안쪽선 기준)로 인해 발생하던 시각적 하체 비율 왜곡 현상을 골반 외측 기준의 **시각적 아웃심(Outseam) 분할선**으로 교정하여 정렬했습니다.

| 신체 지표 | 기존 정의 (왜곡) | 교정된 시각적 정의 | 기대 효과 및 정상 수치 |
|---|---|---|---|
| **허벅지/종아리 비율**<br/>(`thigh_calf_ratio`) | 샅높이 기준<br/>(남 0.73 / 여 1.35) | **골반(엉덩이둘레높이)~무릎** / 무릎~발목 | 남녀 성별 편차가 해소된 해부학적 표준 시각 비율 수렴 (**`1.08 ~ 1.11`**) |
| **상하체 비율**<br/>(`torso_leg_ratio`) | 상체 / 샅높이 하체<br/>(남 1.19 / 여 1.05) | **어깨선~골반** / 골반~바닥 | 다리가 상체보다 시각적으로 길게 인지되는 안정적 비례 정착 (**`0.78` 내외**) |
| **목 길이**<br/>(`neck_length`) | 목뒤 7번 경추뼈 기준 (1.8cm) | **턱끝 아래 ~ 어깨 가쪽선 높이** | 목폴라, 터틀넥, V넥 추천에 대응하는 시각적 목길이 현실화 (**`8.3 ~ 9.5` cm**) |

- **회귀 분석 재모델링**: SizeKorea 5,092명 직접 측정 표본을 새 시각 비율 공식으로 재피팅하여 `ratio_regression_models.json` 및 clean CSV 데이터셋 갱신 완료.

---

## 2. VLM(Kimi) 사진 판독 지침 고도화

VLM이 사진 이미지를 판독할 때도 수식 모델과 동일한 아웃심 기준의 시각적 분할선을 인지하도록 프롬프트 지침 및 JSON 스키마를 고도화했습니다.

- **`body_measurement_prompt_full.j2`**:
  - `thigh_calf_ratio`: "visual thigh length from outer-hip/pelvis to knee divided by calf length from knee to ankle. Typically ranges from 1.00 to 1.15." (지침 상세화)
  - `torso_leg_ratio`: "visual torso length from shoulder to outer-hip divided by visual leg length from outer-hip to floor. Typically ranges from 0.70 to 0.85." (지침 상세화)
- **`body_measurement_schema_full.json`**:
  - VLM이 엉뚱한 수치를 뱉는 사고를 원천 방어하기 위해 허벅지/종아리 비율은 `0.8 ~ 1.3`, 상하체 비율은 `0.6 ~ 1.0` 내외로 JSON 스키마 상에 수치 바운더리 제한 제약 주입.

---

## 3. 체형 판정 규칙(Rules) 및 기획 문서 동기화

- **임계값 JSON 갱신 (`body_shape_thresholds.json`)**: 5,092명 실측에 기반해 3대 세로축 비율의 성별 백분위수 임계치(하위 33% 짧음, 상위 33% 길음 등)를 자동으로 계산하여 수록하도록 `derive_body_thresholds.py` 스크립트를 확장 및 실행 완료.
- **기획 문서 정비 및 번호 정렬**:
  - `01-body-measurement-api-design.md`: 10대 상세 측정 API 입출력 규격으로 갱신.
  - `02-body-proportion-rules.md`: 세로축 비율 3종이 `⛔ 미측정` 블로커 상태에서 `✅ 10대 지표 측정 연동 완료` 상태로 갱신.
  - `03-body-proportion-matrix.md`: 골반 기준으로 교정된 3대 비율 판정 수치선(여성 목길이 하위 33% 7.2cm 이하 등)으로 최신화.
  - 읽는 순서와 계층 구조에 맞춰 번호 접두사를 부여하여 일관적인 파일명으로 정돈 완료.

---

## 4. Swagger E2E 통합 테스트 검증 및 원복

- 우회 로그인(`swagger_noauth`) 환경에서 남성(M021)/여성(F010) 이미지와 키/몸무게 데이터셋 대입 연동 테스트 진행.
- **최종 검증**:
  - Kimi VLM 및 수식 추정 결과, 남녀 피측정자 모두 골반 기준의 시각적 비율(허벅지/종아리 `1.1` 내외, 상하체 `0.78` 내외, 목길이 `9.5`cm 등)이 데이터베이스(`BodyMeasurement` 테이블)에 성공적으로 적재 및 조회됨을 최종 크로스 체크 완료.
  - 검증 완료 후, 로컬 개발용 도커 컨테이너 정리 및 `docker-compose.yml` 원상 복구 완료.
