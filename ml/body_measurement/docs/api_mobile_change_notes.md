# 신체측정 API/mobile 변경 메모

나중에 main 병합 충돌이 날 때 기준을 빠르게 잡기 위한 메모다.

## 1. API / DB

| main 기준 기존 | 현재 확정 변경 |
|---|---|
| `BodyMeasurement`에 `thigh`, `calf`, `arm` 둘레 컬럼 사용 | `thigh`, `calf`, `arm` 둘레 유지 + 길이 기반 `thigh_length`, `calf_length`, `torso_length`, `leg_length`, `neck_length` 추가 (상세 14개 필드) |
| 상세 7개 + 체형 지표 3개처럼 설명 | 총 14개 상세 저장 항목 (기본 둘레 7개 + 보조 길이 5개 + 비율 2개) |
| `torso_leg_ratio`가 서로 다른 하체 기준과 섞임 | `torso_length / leg_length`로 통일 (`어깨선→골반점` / `골반점→복사뼈`) |
| `neck_length` 정의 모호성 | 정면 기준 턱끝→목앞/쇄골선. 여성 중앙값 6.52cm(p10~p90 5.19~7.91), 남성 중앙값 7.72cm(p10~p90 6.04~9.35)를 soft guide로 사용 |
| 비율 범위를 벗어나면 사진 측정 실패 처리 | 참고 분포는 문서/캡션에만 쓰고, 계산 가능한 양수 비율은 저장 |

## 2. ML contract

| main 기준 기존 | 현재 확정 변경 |
|---|---|
| 모델 artifact가 한 파일에 의존 | 코어 4개는 181 모델, 길이 5개는 `hist_gradient_boosting_exact_lengths_v2.joblib`, 둘레 3개는 circumference 모델을 조합 |
| 학습 target에 과거 `thigh/calf/arm` 둘레 포함 여부 혼선 | 14개 필드 전체 지원 (`chest`, `waist`, `hip`, `thigh`, `calf`, `arm`, `shoulder`, `thigh_length`, `calf_length`, `torso_length`, `leg_length`, `neck_length`, 비율 2개) |
| VLM 프롬프트가 M/F 또는 과거 ratio 기준과 섞일 수 있음 | VLM 프롬프트 노출 성별은 `male/female`, 성별 목길이 분포는 강제 보정하지 않는 참고값으로만 제공, 비율은 서버/후처리에서 길이값으로 계산 |

## 3. mobile

| main 기준 기존 | 현재 확정 변경 |
|---|---|
| 결과 화면/가이드에 `thigh`, `calf`, `arm` 둘레 설명 존재 | main 기존 둘레(가슴·허리·엉덩이·어깨·허벅지·종아리·팔뚝) 유지 + 길이 지표는 보조 수치로 표시 |
| 성별 미선택 값이 서버로 갈 수 있음 | `male/female`만 보내고, 미선택은 필드 생략 |
| 사진 분석 로딩 문구가 “몇 분”처럼 모호 | “최대 약 5분”으로 표시 |

## 4. 충돌 해결 우선순위

1. **DB/API 계약은 main 기존 둘레 필드(`thigh`, `calf`, `arm`)를 유지하면서 `*_length` 5개 지표를 추가한 상세 14개 필드 구조를 우선한다.**
2. `gender`는 API/Swagger/mobile에서는 `male/female`을 우선한다.
3. ML 내부 학습 인코딩에서만 `M/F`를 허용한다.
4. 목길이는 턱끝→목앞/쇄골선으로 통일하고 성별 p10~p90은 VLM soft guide, p33/p67은 체형 판정에 사용한다.
