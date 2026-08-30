# 신체측정 데이터 정의 (14개 저장 항목)

## 1. 데이터 출처와 보관 위치

| 구분 | 위치 | 용도 |
|---|---|---|
| 원본 3D 추출 CSV | `ml/body_measurement/data/raw/sizekorea_8th_3d_source.csv` | `(1~2차년도) 3D 측정` 시트의 학습 관련 원천 컬럼 |
| 정확 길이 전처리 | `ml/body_measurement/data/preprocessed/sizekorea_8th_exact_lengths_v2.csv` | 사용자 랜드마크 정의로 계산한 로컬 학습 데이터(Git 제외) |
| 정확 길이 Hist | `ml/body_measurement/data/hist/models/hist_gradient_boosting_exact_lengths_v2.joblib` | 성별·키·몸무게 → 길이 5개 |
| 학습 계약·분포 | `ml/body_measurement/data/hist/manifest_exact_lengths_v2.json` | 정의, 표본, 성별 분포, 모델 해시 |
| Hist 행별 결과 | `ml/body_measurement/data/hist/predictions/*.csv` | 실제값·예측값·오차를 행 단위로 저장 |
| VLM | `ml/body_measurement/data/vlm/` | 사진 프롬프트 실행 결과를 저장할 위치 |
| 과거 7개/둘레 기준 자료 | `ml/body_measurement/data/archive/legacy_7target_vlm/` | 현재 계약에는 사용하지 않는 참고용 보관 |

원본 3D 추출값은 길이·둘레가 mm, 몸무게가 kg이므로 학습 데이터에서는 cm/kg로 변환한다.

## 2. 모델 입력과 14개 저장 항목 (상세 14개)

모델 입력은 `gender`, `height(cm)`, `weight(kg)` 3개다. API/mobile의 성별은 `male/female`, ML 내부 학습 인코딩은 `M/F`다. 기존 하위 호환성을 위해 둘레 3개(`thigh`, `calf`, `arm`)를 유지하고, 체형 비율 분석용 보조 지표로 길이 5개 및 비율 2개를 서비스한다 (총 14개 필드).

| API/모델 필드 | 단위 | 8차 원본 컬럼 또는 계산식 |
|---|---:|---|
| `shoulder` | cm | `298. 어깨사이너비` / 10 |
| `chest` | cm | `460. 젖가슴둘레` / 10 |
| `waist` | cm | `463. 허리둘레` / 10 |
| `hip` | cm | `465. 엉덩이둘레` / 10 |
| `thigh` | cm | `넙다리둘레` / 10 |
| `calf` | cm | `장딴지둘레` / 10 |
| `arm` | cm | `편위팔둘레` / 10 |
| `thigh_length` | cm | 샅선/인심 라인 → 무릎뼈/무릎 중심 |
| `calf_length` | cm | `(무릎뼈가운데높이 - 가쪽복사높이) / 10` |
| `torso_length` | cm | 어깨선 → 골반점 |
| `leg_length` | cm | `(위앞엉덩뼈가시높이 - 가쪽복사높이) / 10` |
| `neck_length` | cm | `(턱끝높이 - 목앞높이) / 10`; 사진에서는 턱 아래→쇄골선 |
| `thigh_calf_ratio` | 비율 | `thigh_length / calf_length` |
| `torso_leg_ratio` | 비율 | `torso_length / leg_length` |

## 3. 최종 측정 기준

| 지표 | 확정 기준 |
|---|---|
| 허벅지 길이 | 샅선/인심 라인 → 무릎뼈/무릎 중심 |
| 종아리 길이 | 무릎뼈/무릎 중심 → 복사뼈/발목 |
| 상체 길이 | 어깨선/어깨높이 → 골반점 |
| 하체 길이 | 골반점/위앞엉덩뼈가시 → 복사뼈/발목 |
| 목길이 | 턱 아래/턱끝 → 목앞/쇄골선. `머리→골반 - 어깨→골반 - 실제 얼굴길이`는 같은 길이를 구성하는 보조식이다. |

정확한 3D 랜드마크가 모두 있는 SizeKorea 4,485명(F 2,510/M 1,975)의 참고 분포다. p01~p99는 프롬프트와 운영 진단의 soft 범위이며 값을 자르거나 저장을 거절하는 하드 제한이 아니다.

| 지표 | 평균 | p01 | p99 |
|---|---:|---:|---:|
| `thigh_length` | 31.160 | 25.667 | 36.452 |
| `calf_length` | 38.006 | 32.348 | 44.610 |
| `torso_length` | 45.031 | 38.984 | 52.100 |
| `leg_length` | 82.615 | 71.744 | 94.732 |
| `neck_length` | 7.052 | 4.025 | 10.362 |
| `thigh_calf_ratio` | 0.823 | 0.652 | 0.970 |
| `torso_leg_ratio` | 0.546 | 0.466 | 0.637 |

## 4. HistGradientBoosting 재현

`train_hist_exact_lengths_v2.py`가 정확 길이 5개를 계산하고 5-fold 교차검증 후 기존 모델과 다른 파일명으로 학습한다.

`source_row_id`, `subject_id`, 입력 3개, `actual_<target>`, `predicted_<target>`, `error_<target>`

따라서 평균 MAE만 보지 않고 각 사람·각 항목의 실제값과 예측값을 확인할 수 있다.

## 5. VLM 기준

기존 VLM 결과는 허벅지·종아리·팔뚝을 둘레로 요청한 실험이 섞여 있었기 때문에 새 길이 정의와 직접 비교하지 않는다. 과거 split/label 자료는 `data/archive/legacy_7target_vlm/`에 분리했다.

VLM은 비율을 직접 반환하지 않는다. 응답에는 `thigh_length_cm`, `calf_length_cm`, `torso_length_cm`, `leg_length_cm`를 포함하고, 서빙/벤치마크 코드가 아래처럼 계산한다.

| 저장 필드 | 계산식 |
|---|---|
| `thigh_calf_ratio` | `thigh_length_cm / calf_length_cm` |
| `torso_leg_ratio` | `torso_length_cm / leg_length_cm` |

비율이 SizeKorea 참고 분포를 벗어나도 실패 처리하지 않는다. 사진 추정값은 사용자가 결과 화면에서 수정할 수 있으므로, 필수 키 누락·숫자 변환 실패·0 이하 분모 같은 실제 계산 불가 상황만 실패로 본다.
