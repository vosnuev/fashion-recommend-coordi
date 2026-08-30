# 골든셋 CSV 검수 요청 가이드

각 `*.template.csv`를 검수자 이름으로 복사하고, **이미 채워진 값은 수정하지 말고 빈칸만
아래 표의 순서대로 입력**해 주세요. 선택값은 표에 적힌 영어값을 그대로 사용합니다.

- 검수자명: 파일 전체에서 `reviewer-a` 또는 `reviewer-b` 중 하나로 통일
- 저장 형식: `CSV UTF-8`
- 빈칸 허용: `판정 불가인 Q 점수`, `EDIT가 아닌 경우의 수정 문장`, `선택 메모`
- 모르겠으면 빈칸 대신 `UNSURE` 또는 `unassessable` 사용

## 1. claim 검수표 — 필수

파일: `claim_reviews.template.csv`

행 맨 앞의 빈칸 1개와 행 끝의 빈칸 8개를 다음 순서로 채웁니다.

| 순서 | 열 이름 | 대답 형식 | 입력값 또는 작성 내용 |
|---:|---|---|---|
| 1 | `reviewer_label` | 짧은 텍스트 | 할당받은 검수자명: `reviewer-a` 또는 `reviewer-b` |
| 2 | `evidence_correct` | 정해진 영어값 | `YES`=이미지에서 확인됨 / `NO`=이미지와 다름 / `UNSURE`=판단 어려움 |
| 3 | `human_judgment` | 정해진 영어값 | `CONTRIBUTES`=좋은 코디에 기여 / `CONTEXT_DEPENDENT`=특정 스타일·상황에서만 기여 / `DESCRIPTIVE_ONLY`=보이는 사실일 뿐 좋은 이유는 아님 / `UNSUPPORTED`=근거 부족 / `INCORRECT`=설명이 틀림 |
| 4 | `verdict` | 정해진 영어값 | `APPROVE`=그대로 승인 / `EDIT`=문장 수정 후 사용 / `REJECT`=기각 / `UNSURE`=보류 |
| 5 | `human_confidence_1_3` | 숫자 1~3 | `1`=확신 낮음 / `2`=보통 / `3`=확신 높음 |
| 6 | `overgeneralization_risk` | 정해진 영어값 | `YES`=한 이미지 특징을 일반 법칙처럼 표현함 / `NO`=그렇지 않음 / `UNSURE`=판단 어려움 |
| 7 | `stereotype_risk` | 정해진 영어값 | `YES`=성별·체형·연령 고정관념이 있음 / `NO`=없음 / `UNSURE`=판단 어려움 |
| 8 | `edited_statement` | 자유 입력 | `verdict=EDIT`일 때만 이미지에서 확인되는 범위로 고친 문장 작성. 그 외에는 빈칸 |
| 9 | `notes` | 자유 입력 | `REJECT`, `UNSURE` 또는 특이사항의 이유를 한 문장으로 작성. 없으면 빈칸 |

### 실제 입력 예시

입력 전:

```csv
,pilot-m-classic-01,E:\images\man_classiclook_5.jpg,C3,A6_STYLE_COHESION,블랙 컬러의 첼시 부츠가 상·하의의 밝은 뉴트럴 톤과 대비되어 시각적 무게 중심을 하단으로 이동시킴,boots;coat;slacks,NEUTRAL,CONTEXT_DEPENDENT,,,,,,,,
```

입력 후:

```csv
reviewer-a,pilot-m-classic-01,E:\images\man_classiclook_5.jpg,C3,A6_STYLE_COHESION,블랙 컬러의 첼시 부츠가 상·하의의 밝은 뉴트럴 톤과 대비되어 시각적 무게 중심을 하단으로 이동시킴,boots;coat;slacks,NEUTRAL,CONTEXT_DEPENDENT,YES,CONTEXT_DEPENDENT,APPROVE,2,NO,NO,,색 대비는 보이지만 완성도 기여 여부는 스타일 의존
```

마지막 빈칸 8개에 들어간 값:

```text
YES,CONTEXT_DEPENDENT,APPROVE,2,NO,NO,,색 대비는 보이지만 완성도 기여 여부는 스타일 의존
```

`edited_statement`를 입력하지 않았으므로 `NO,NO,,메모`처럼 쉼표 두 개가 연속으로
남습니다.

## 2. 이미지 관찰 검수표 — 필수

파일: `image_observation_reviews.template.csv`

| 순서 | 열 이름 | 대답 형식 | 입력값 또는 작성 내용 |
|---:|---|---|---|
| 1 | `reviewer_label` | 짧은 텍스트 | 할당받은 검수자명: `reviewer-a` 또는 `reviewer-b` |
| 2 | `image_assessable` | 정해진 영어값 | `YES`=코디 판단 가능 / `NO`=잘림·가림·저화질로 판단 불가 / `UNSURE`=판단 어려움 |
| 3 | `items_complete` | 정해진 영어값 | `YES`=주요 옷·신발·가방·액세서리가 모두 잡힘 / `NO`=누락 또는 오인식 있음 / `UNSURE`=판단 어려움 |
| 4 | `bbox_grounding_1_3` | 숫자 1~3 | `1`=아이템 영역이 틀림 / `2`=일부 오차가 있지만 식별 가능 / `3`=아이템 영역이 정확함 |
| 5 | `unassessable_complete` | 정해진 영어값 | `YES`=사진으로 모르는 소재·TPO·착용자 정보 등을 잘 보류함 / `NO`=보이지 않는 내용까지 추정함 / `UNSURE`=판단 어려움 |
| 6 | `q_color_1_5` | 숫자 1~5 또는 빈칸 | 색 조화가 스타일 의도를 얼마나 잘 살리는지 평가 |
| 7 | `q_silhouette_proportion_1_5` | 숫자 1~5 또는 빈칸 | 실루엣·길이·볼륨 비율이 스타일 의도를 얼마나 잘 살리는지 평가 |
| 8 | `q_material_pattern_1_5` | 숫자 1~5 또는 빈칸 | 보이는 소재·패턴 조합이 스타일 의도를 얼마나 잘 살리는지 평가 |
| 9 | `q_style_cohesion_1_5` | 숫자 1~5 또는 빈칸 | 아이템들의 스타일·격식이 얼마나 일관적인지 평가 |
| 10 | `q_completeness_detail_1_5` | 숫자 1~5 또는 빈칸 | 신발·가방·액세서리 등 마무리가 얼마나 완성도 있게 작동하는지 평가 |
| 11 | `observation_verdict` | 정해진 영어값 | `APPROVE`=그대로 승인 / `EDIT`=누락·수정 필요 / `REJECT`=사용 불가 / `UNSURE`=보류 |
| 12 | `human_confidence_1_3` | 숫자 1~3 | `1`=확신 낮음 / `2`=보통 / `3`=확신 높음 |
| 13 | `missing_observations` | 자유 입력 | 빠진 항목을 `영문ID:한글명`으로 작성. 예: `belt:벨트 누락`. 없으면 빈칸 |
| 14 | `notes` | 자유 입력 | 수정·기각·보류 이유를 한 문장으로 작성. 없으면 빈칸 |

### Q 점수 1~5 공통 의미

| 숫자 | 의미 |
|---:|---|
| `1` | 해당 요소가 스타일 의도를 명확히 방해함 |
| `2` | 약점이 눈에 띄며 일부 방해함 |
| `3` | 무난하거나 중립적임 |
| `4` | 스타일 의도를 분명히 잘 살림 |
| `5` | 코디의 핵심 강점으로 작동함 |

사진만으로 판단할 수 없는 Q 항목은 숫자를 추측하지 말고 빈칸으로 둡니다.

### 실제 입력 예시

입력 전:

```csv
,pilot-m-classic-01,E:\images\man_classiclook_5.jpg,클래식,coat:롱 코트;turtleneck:터틀넥 니트;slacks:슬랙스;boots:첼시 부츠,,,,,,,,,,,,,
```

입력 후:

```csv
reviewer-a,pilot-m-classic-01,E:\images\man_classiclook_5.jpg,클래식,coat:롱 코트;turtleneck:터틀넥 니트;slacks:슬랙스;boots:첼시 부츠,YES,NO,3,YES,4,4,,5,4,EDIT,3,belt:벨트 누락,주요 의류와 영역은 맞지만 벨트를 추가해야 함
```

## 3. 쌍대 비교 검수표 — 필수

파일: `pairwise_reviews.template.csv`

행 중간의 `reviewer_label` 빈칸과 행 끝의 빈칸 4개를 채웁니다.

| 순서 | 열 이름 | 대답 형식 | 입력값 또는 작성 내용 |
|---:|---|---|---|
| 1 | `reviewer_label` | 짧은 텍스트 | 할당받은 검수자명: `reviewer-a` 또는 `reviewer-b` |
| 2 | `winner` | 정해진 영어값 | `left`=왼쪽이 더 좋음 / `right`=오른쪽이 더 좋음 / `tie`=비슷함 / `context_dependent`=상황이 달라 직접 비교하기 어려움 / `unassessable`=이미지 문제 등으로 판단 불가 |
| 3 | `confidence_1_3` | 숫자 1~3 | `1`=확신 낮음 / `2`=보통 / `3`=확신 높음 |
| 4 | `reason_axis` | 정해진 영어값 | `A1_COLOR_HARMONY`=색 조화 / `A2_SILHOUETTE_PROPORTION`=실루엣·비율 / `A5_MATERIAL_PATTERN`=소재·패턴 / `A6_STYLE_COHESION`=스타일 통일감 / `A7_COMPLETENESS_DETAIL`=마무리·디테일 / `MIXED`=여러 이유 |
| 5 | `notes` | 자유 입력 | 선택한 쪽이 더 낫거나 비교할 수 없는 이유를 한 문장으로 작성 |

### 실제 입력 예시

입력 전:

```csv
pair-001,,MATCHED_STYLE,Q_OVERALL_STYLE_EXECUTION,style:미니멀,pilot-m-minimal-01,E:\images\man_minimalist_3.jpg,미니멀,pilot-w-minimal-01,E:\images\woman_minimalist_5.jpg,미니멀,DETERMINISTIC_RANDOM_V1,,,,
```

입력 후:

```csv
pair-001,reviewer-a,MATCHED_STYLE,Q_OVERALL_STYLE_EXECUTION,style:미니멀,pilot-m-minimal-01,E:\images\man_minimalist_3.jpg,미니멀,pilot-w-minimal-01,E:\images\woman_minimalist_5.jpg,미니멀,DETERMINISTIC_RANDOM_V1,left,2,A6_STYLE_COHESION,두 코디 모두 미니멀이지만 왼쪽의 아이템 구성이 더 절제되어 보임
```

## 4. 최소 수정 가설 검수표 — 선택

파일: `minimum_edit_reviews.template.csv`

| 순서 | 열 이름 | 대답 형식 | 입력값 또는 작성 내용 |
|---:|---|---|---|
| 1 | `reviewer_label` | 짧은 텍스트 | 할당받은 검수자명: `reviewer-a` 또는 `reviewer-b` |
| 2 | `single_variable_change` | 정해진 영어값 | `YES`=한 가지 속성만 변경 / `NO`=여러 요소가 함께 변경 / `UNSURE`=판단 어려움 |
| 3 | `preserves_style_intent` | 정해진 영어값 | `YES`=기존 스타일 의도 유지 / `NO`=기존 의도가 달라짐 / `UNSURE`=판단 어려움 |
| 4 | `verdict` | 정해진 영어값 | `PLAUSIBLE_HYPOTHESIS`=실제 변형으로 시험할 만함 / `TASTE_DEPENDENT`=취향에 따라 달라짐 / `INCORRECT`=가설이 잘못됨 / `UNSURE`=보류 |
| 5 | `human_confidence_1_3` | 숫자 1~3 | `1`=확신 낮음 / `2`=보통 / `3`=확신 높음 |
| 6 | `notes` | 자유 입력 | 가설을 승인·보류·기각한 이유를 한 문장으로 작성. 없으면 빈칸 |

### 실제 입력 예시

입력 전:

```csv
,pilot-m-classic-01,E:\images\man_classiclook_5.jpg,boots,색상,블랙에서 다크 브라운으로 변경,A1_COLOR_HARMONY,전체적인 톤온톤 배색의 연속성이 강화됨,,,,,
```

입력 후:

```csv
reviewer-a,pilot-m-classic-01,E:\images\man_classiclook_5.jpg,boots,색상,블랙에서 다크 브라운으로 변경,A1_COLOR_HARMONY,전체적인 톤온톤 배색의 연속성이 강화됨,YES,YES,PLAUSIBLE_HYPOTHESIS,2,실제 변형 이미지로 확인하기 전까지는 가설로만 사용
```

## 5. 제출 파일

검수자별 필수 제출:

```text
image_observation_reviews.reviewer-a.csv
claim_reviews.reviewer-a.csv
pairwise_reviews.reviewer-a.csv
```

선택 제출:

```text
minimum_edit_reviews.reviewer-a.csv
```

검수자 B는 파일명의 `reviewer-a`를 `reviewer-b`로 바꾸면 됩니다.
