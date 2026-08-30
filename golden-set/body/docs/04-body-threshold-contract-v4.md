# 체형 임계값 계약 v4

1. 단일 원천

| 항목 | 계약 |
| --- | --- |
| 가로 체형 원천 | 이미지가 연결된 SizeKorea 직접측정 프로필 172명(F 81/M 91) |
| 길이·목 원천 | 정확 3D 랜드마크 SizeKorea 4,485명(F 2,510/M 1,975) |
| 길이 계약 | `manifest_exact_lengths_v2.json`의 계산식·성별 분포 |
| 재생성 | `python golden-set/body/tools/derive_body_thresholds.py` |

생성기는 골든 파일과 `api/apps/recommend/rules/body_shape_thresholds.json`을 같은 문자열로 함께 기록한다. 두 파일의 해시는 항상 같아야 한다. 처방 규칙은 스키마가 달라 byte 복사하지 않고, 5종 taxonomy와 버전 일치 여부를 테스트한다.

2. 가로 체형 판정

| 순서 | 조건 | 결과 |
| --- | --- | --- |
| 1 | `waist_definition >= 성별 p90` | `round` |
| 2 | `upper_lower >= 성별 p67` | `inverted_triangle` |
| 3 | `upper_lower <= 성별 p33` | `triangle` |
| 4 | `waist_definition <= 성별 p33` | `hourglass` |
| 5 | 그 외 | `rectangle` |

- 입력은 `gender`, `shoulder`, `chest`, `waist`, `hip`이며 **shoulder는 필수**다.
- 성별 안에서 `shoulder`, `chest`, `hip`을 각각 경험 백분위 `S`, `C`, `H`로 바꾼다.
- `upper_lower = 0.6*S + 0.4*C - H`다.
- `waist_definition = waist / ((chest + hip) / 2)`다.
- 출력은 위 5개뿐이다. 과거 `standard`는 `rectangle`에 흡수한다.

3. 세로 비율

`thigh_calf_ratio`, `torso_leg_ratio`, `neck_length`는 정확 3D 랜드마크 4,485명의 성별별 p33/p67을 사용한다. `thigh_calf_ratio`의 UI 참고 범위는 `0.7~1.3`이며 하드 실패 조건이 아니다.

| 지표 | 여성 p33 / p67 | 남성 p33 / p67 |
| --- | --- | --- |
| `thigh_calf_ratio` | 0.832943 / 0.880689 | 0.755608 / 0.808266 |
| `torso_leg_ratio` | 0.536645 / 0.568055 | 0.523461 / 0.551797 |
| `neck_length` | 6.06 / 7.05cm | 7.13 / 8.2958cm |

4. 한계

- 가로 체형은 이미지 연결 172명 내부 기준이고 길이 축은 3D 4,485명 기준이다.
- 원천 성별은 M/F만 제공한다. 그 밖의 성별에 적용할 검증 근거가 없다.
- 어깨는 너비이고 가슴·허리·엉덩이는 둘레다. 그래서 원시 cm를 직접 빼지 않고 성별 경험 백분위를 쓴다.
- 표본이나 계측 정의가 바뀌면 CSV와 JSON을 생성기로 함께 갱신해야 한다. JSON 수동 수정은 금지한다.
