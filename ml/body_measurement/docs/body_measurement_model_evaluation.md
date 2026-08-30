# 신체측정 모델 평가

평가 기준은 현재 서빙하는 14개 저장 항목이다. 과거 `(키-샅높이)/샅높이` 비율식 결과와
아래 §6의 legacy 11-target 실험은 현재 평가에 포함하지 않는다.

## 1. 서빙 조합 — 모델 3개를 합쳐 14개를 만든다

`estimate_from_basic()`은 모델 하나가 아니라 **세 개를 순서대로 호출해 합친다.**
학습 데이터가 서로 다르기 때문이며, 정확도를 인용할 때 **어느 모델의 숫자인지 반드시 밝혀야 한다.**

| 출력 | 개수 | 모델 artifact | 학습 모집단 | 지표 파일 |
|---|---:|---|---|---|
| `chest` `waist` `hip` `shoulder` | 4 | `hist_gradient_boosting_181.joblib` | SizeKorea 8차 **직접측정** 181명 프로필 (학습 172행) | `metrics_181.json` |
| `thigh` `calf` `arm` | 3 | `hist_gradient_boosting_circumference.joblib` | 위와 같은 직접측정 계열 (학습 178행) | `metrics_circumference.json` |
| `thigh_length` `calf_length` `torso_length` `leg_length` `neck_length` | 5 | `hist_gradient_boosting_exact_lengths_v2.joblib` | SizeKorea 8차 **3D 측정** (원천 4,545행 → 정제 4,485행) | `metrics_exact_lengths_v2.json` |
| `thigh_calf_ratio` `torso_leg_ratio` | 2 | 모델 없음 — 서버 후처리 나눗셈 | — | 위 파일의 `cv5_postprocess` |

✅ 근거: `src/inference.py:326~356` (`estimate_from_basic`) · `manifest_181.json`의
`serving_status: "core_measurements_only"`, `serving_targets: [chest, waist, hip, shoulder]` ·
`docs/api_mobile_change_notes.md:19`

⚠️ **직접측정 계열과 3D 계열은 같은 사람이 아니다.** 3D 원천 4,545명과 이미지 181명은
`subject_id` 교집합이 0명이다 (8개 항목 최근접 L1거리 최소 4.46cm, 0거리 0명).
**둘레 정확도와 길이 정확도를 하나의 근거로 묶어 말하면 안 된다.**

⚠️ 행 수 172 / 178 / 181은 같은 모집단의 서로 다른 카운트다(프로필 181, 각 타깃의 결측
제외 후 학습 행). 임의로 하나로 통일하지 말고 **인용하는 지표 파일이 적은 수를 그대로 쓴다.**

## 2. 서빙 모델 지표 (전부 shuffled 5-fold CV)

### 2.1 코어 둘레 4개 — `metrics_181.json` (172행)

| 항목 | MAE (cm) | RMSE | R² |
|---|---:|---:|---:|
| shoulder | 1.541 | 1.862 | 0.593 |
| hip | 2.306 | 2.958 | 0.726 |
| chest | 3.043 | 3.845 | 0.727 |
| waist | 3.406 | 4.657 | 0.761 |

> `metrics_181.json`에는 12개 타깃이 모두 들어 있지만, **서빙에 쓰는 것은 위 4개뿐이다.**
> 같은 파일의 `thigh`/`calf`/`arm` 행(2.379 / 1.294 / 1.247)은 서빙 값이 아니므로 인용하지
> 않는다 — 서빙 값은 §2.2다. 같은 파일의 길이 5개 행도 서빙하지 않는다(§4 참조).

### 2.2 부가 둘레 3개 — `metrics_circumference.json` (178행)

| 항목 | MAE (cm) | RMSE | R² |
|---|---:|---:|---:|
| arm | 1.170 | 1.446 | 0.775 |
| calf | 1.183 | 1.528 | 0.619 |
| thigh | 2.223 | 2.826 | 0.645 |

⚠️ 이 모델만 **학습 스크립트가 `scripts/`에 없다.** artifact와 지표·행별 예측
(`predictions/circumference_cv_predictions.csv`, 178행, 컬럼 = thigh/calf/arm)은 서로 일치하지만
재현 스크립트가 없어 **재학습 경로가 끊겨 있다.** 재학습이 필요해지면 스크립트부터 복원해야 한다.

### 2.3 길이 5개 + 비율 2개 — `metrics_exact_lengths_v2.json` (4,485행)

| 항목 | MAE | RMSE | R² |
|---|---:|---:|---:|
| neck_length | 0.917 cm | 1.159 | 0.256 |
| calf_length | 1.065 cm | 1.347 | 0.776 |
| thigh_length | 1.396 cm | 1.804 | 0.405 |
| leg_length | 1.537 cm | 1.988 | 0.857 |
| torso_length | 1.660 cm | 2.169 | 0.439 |
| thigh_calf_ratio | 0.046 | 0.058 | 0.292 |
| torso_leg_ratio | 0.028 | 0.037 | 0.020 |

### 2.4 해석 — 숫자를 인용할 때의 전제

1. **한 범위로 묶지 않는다.** 길이는 0.92~1.66cm, 둘레는 1.17~3.41cm다. "MAE 1~1.6cm"처럼
   말하면 허리(3.406)에서 2배 이상 틀린 주장이 된다.
2. **가장 어려운 부위가 가장 중요한 부위다.** 허리 3.406 / 가슴 3.043으로 오차가 가장 큰데,
   상·하의 사이즈를 결정하는 것이 바로 이 두 부위다. 사진(VLM) 경로를 둔 이유가 여기 있다.
3. `neck_length`는 키·몸무게로 예측되지 않는다(R² 0.256). 정의를 4가지로 바꿔 재봐도
   5-fold R²가 -0.36~0.10이라 사실상 집단 평균이다. 실제 값은 사진 경로로만 얻을 수 있다.
4. `torso_leg_ratio`는 MAE 0.028로 작지만 R² 0.020이다. 개인별 미세 차이는 잡지 못하므로
   사진 측정값 또는 사용자 수정값을 우선한다.

## 3. VLM 실행 기준

새 실행부터 VLM은 ratio를 직접 반환하지 않고 `thigh_length_cm`, `calf_length_cm`,
`torso_length_cm`, `leg_length_cm`를 반환한다. 정렬 스크립트는 `thigh_length/calf_length`,
`torso_length/leg_length`로 ratio를 재계산한다. 기존 실행분처럼 support length가 없으면
기존 ratio 컬럼을 fallback으로 사용하고, 없는 길이 컬럼은 비워 둔다.

서빙 길이 랜드마크 계약(= `manifest_exact_lengths_v2.json`의 `length_definitions`)은 다음과 같다.

| 필드 | 정의 |
|---|---|
| `thigh_length` | 샅높이 - 무릎뼈가운데높이 |
| `calf_length` | 무릎뼈가운데높이 - 가쪽복사높이 |
| `torso_length` | 어깨높이 - 위앞엉덩뼈가시높이(골반점) |
| `leg_length` | 위앞엉덩뼈가시높이(골반점) - 가쪽복사높이 |
| `neck_length` | 턱끝높이 - 목앞높이. 사진에서 7~12cm는 soft plausibility guide이며 clipping 또는 실패 조건이 아니다. |

⚠️ `manifest_181.json`에도 `length_definitions`가 있지만 **옛 정의다**
(`leg_length` = 샅높이, `calf_length` = 무릎높이). 그 모델의 길이 출력은 서빙하지 않으므로
(§1) **위 표만 유효하다.** 두 매니페스트의 정의가 다른 것은 오류가 아니라 세대 차이다.
`comparison_exact_lengths_v2.json`이 그 차이를 정량화해 둔다 — 옛 정의를 3D 정확 정의 정답에
직접 대면 `calf_length` MAE가 2.423cm로, v2(1.065cm)의 2배 이상이다.

### 3.1 VLM 재학습·재평가를 하지 않는 이유

`data/people`의 앞·옆 사진 181명은 복구된 same-image profile과는 연결되지만, 3D 원천
4,545명과 `subject_id` 교집합이 없다. same-image profile에는 가쪽복사·어깨선·골반점·턱끝·목앞
랜드마크가 없으므로 새 정의의 `calf_length`, `torso_length`, `leg_length`, `neck_length`
정답을 만들 수 없다. `build_vlm_image_ground_truth.py`는 이 값을 proxy로 채우지 않고
`NULL`로 보존한다.

따라서 VLM 자체 재학습은 하지 않는다. 프롬프트 변경 후에는 성별과 키·몸무게 구간을 층화한
소규모 표본으로 새 응답의 안정성을 먼저 확인하고, 정확도 평가는 새 랜드마크를 가진 동일 인물
사진을 확보한 뒤 수행한다. 현재 181명으로는 둘레·어깨와 `thigh_length`만 정량 평가할 수 있고,
나머지 길이는 응답률·물리적 일관성·반복 호출 변동만 검증할 수 있다.

새 `leg_length`가 기존 샅점 기준 결과보다 커지는 것은 정의 변경에 따른 정상 변화다.
비율이 참고 분포 밖이어도 실패 처리하지 않는다. 필수 키 누락, 숫자 변환 실패,
0 이하 분모만 실패로 본다.

⚠️ 이전 판에는 층화 표본 생성 명령
(`python ml/body_measurement/scripts/select_vlm_landmark_v2_sample.py --per-gender 6`)이
적혀 있었으나 **해당 스크립트는 저장소에 없다.** 층화 표본이 필요하면 스크립트를 먼저 작성한다.

## 4. 사진 vs 무사진 A/B

사진 경로를 왜 두는지에 대한 정량 근거는 이 문서가 아니라
`docs/body-measurement-api-design.md` §7.2에 있다 (143명, 부위별 MAE).
결론만 옮기면, **사진은 가슴·허리에서만 확실히 낫고 나머지 5개 부위는 무사진이 낫다.**
집계 기준을 3개(가슴·허리·엉덩이)로 잡으면 사진이, 7개 전체로 잡으면 무사진이 이긴다.
**성능을 인용할 때는 3개 기준인지 7개 기준인지 반드시 함께 밝힌다.**

## 5. 재현 파일

| 용도 | 경로 | 상태 |
|---|---|---|
| 코어 둘레 4개 학습 | `scripts/train_hist_181.py` | ✅ |
| 길이 5개 학습 | `scripts/train_hist_exact_lengths_v2.py` | ✅ |
| 부가 둘레 3개 학습 | — | ⚠️ **없음** (§2.2) |
| 옛/새 길이 정의 비교 | `scripts/compare_hist_exact_lengths_v2.py` | ✅ |
| 사진 필터 | `scripts/filter_predictions_to_people.py` | ✅ |
| VLM 컬럼 정렬 | `scripts/align_vlm_predictions.py` | ✅ |
| same-image profile 복구/평가 | `scripts/build_vlm_image_ground_truth.py` | ✅ |
| VLM 호출 | `scripts/run_openrouter.py` | ✅ |
| 행별 예측 (코어 둘레, 172행) | `data/hist/predictions/cv_predictions_181.csv` | ✅ |
| 행별 예측 (부가 둘레, 178행) | `data/hist/predictions/circumference_cv_predictions.csv` | ✅ |

## 6. Legacy — 참고용, 인용 금지

`metrics.json`과 `manifest.json`은 **서빙하지 않는 옛 11-target 모델**의 산출물이다
(train 3,588 / validation 448 / test 449, holdout 방식). 타깃에 `thigh`·`calf`·`arm`이
아예 없어 현재 14개 계약을 만들 수 없으므로 **현재 성능으로 인용하면 안 된다.**

⚠️ 이 모델의 행별 예측 파일 4종(`validation_predictions.csv`, `test_predictions.csv`,
`vlm_validation_inputs_hist_predictions.csv`, `vlm_test_inputs_hist_predictions.csv`)은
이전 판 문서에 적혀 있었으나 **저장소에 없다.** 학습 스크립트 `scripts/retrain_11targets.py`만 남아 있다.

과거 7개/둘레 기준 실험: `ml/body_measurement/legacy/`,
`ml/body_measurement/data/archive/legacy_7target_vlm/`
