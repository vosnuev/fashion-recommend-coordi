# 멀티모달 모델 선정 기준

이 문서는 현재 신체측정 기준에 맞춘 최신 요약이다. 과거의 7개/10개/11개 실험 기준은 사용하지 않는다.

## 1. 현재 출력 계약

| 구분 | 개수 | 필드 | 담당 모델 |
|---|---:|---|---|
| 코어 둘레·너비 | 4 | `shoulder`, `chest`, `waist`, `hip` | `hist_gradient_boosting_181` |
| 부가 둘레 | 3 | `thigh`, `calf`, `arm` | `hist_gradient_boosting_circumference` |
| 패션용 길이감 | 5 | `thigh_length`, `calf_length`, `torso_length`, `leg_length`, `neck_length` | `hist_gradient_boosting_exact_lengths_v2` |
| 패션용 비율 | 2 | `thigh_calf_ratio`, `torso_leg_ratio` | 모델 없음 (서버 후처리) |

저장·응답 계약은 **총 14개**다 (`api/apps/users/serializers.py`의 `BODY_DETAIL_FIELDS`).
무사진 경로는 모델 3개를 조합해 이 14개를 채운다 — 조합 표와 모델별 지표는
`ml/body_measurement/docs/body_measurement_model_evaluation.md` §1~2를 본다.

입력은 `gender`, `height(cm)`, `weight(kg)`이며, 사진 경로는 정면/측면 전신 사진을 추가로 사용한다.

## 2. 선정 기준

| 기준 | 현재 판단 |
|---|---|
| 무사진 기준선 | HistGradientBoosting 모델 3개 조합(코어 둘레 4 + 부가 둘레 3 + 길이 5)을 기준선으로 둔다. |
| 사진 모델 | VLM은 정면/측면 사진에서 패션용 길이감을 추정한다. |
| 비율 계산 | VLM이 비율을 직접 반환하지 않고, `thigh_length / calf_length`, `torso_length / leg_length`로 후처리 계산한다. |
| 평균 비율 | SizeKorea 기준 `thigh_calf_ratio` 평균 0.823, `torso_leg_ratio` 평균 **0.546**을 해석 기준으로 둔다. 참고범위는 `inference.py`의 `RATIO_REFERENCE_RANGES`와 같다 (thigh_calf 0.652~0.970, torso_leg 0.466~0.637). |
| 정량 비교 | 같은 subject의 이미지와 실측 데이터가 연결된 행만 평가한다. |
| 개인정보 | 실제 서비스 전에는 얼굴 블러/저장 정책을 별도로 확정해야 한다. |

## 3. 현재 평가 문서

- 데이터 정의: `ml/body_measurement/docs/body_measurement_data_definition.md`
- **모델 평가 (서빙 조합표 + 모델별 지표)**: `ml/body_measurement/docs/body_measurement_model_evaluation.md`
- 사진 vs 무사진 A/B: `docs/body-measurement-api-design.md` §7.2
- VLM 이미지 정답 생성: `ml/body_measurement/scripts/build_vlm_image_ground_truth.py`
- Hist 재학습: 코어 둘레 `scripts/train_hist_181.py` · 길이 `scripts/train_hist_exact_lengths_v2.py`
  (부가 둘레 모델은 학습 스크립트가 없다 — 평가 문서 §2.2)
  ⚠️ `scripts/retrain_11targets.py`는 서빙하지 않는 legacy 11-target 모델용이다.
