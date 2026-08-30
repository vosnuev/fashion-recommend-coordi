# 공유 옷 레퍼런스 추천 운영 지표

- 문서 상태: 운영 계약
- 기준 이벤트 버전: `REFERENCE_RECOMMENDATION_RESULT` / `1.0`
- 적용 범위: 공유 옷을 참고 입력으로 사용한 기본·스타일리스트 추천

## 이벤트 계약

추천 실행 한 번당 성공 또는 실패 이벤트 한 건을 JSON 로그로 남긴다.

| 필드 | 값·의미 |
|---|---|
| `event` | `REFERENCE_RECOMMENDATION_RESULT` |
| `event_schema_version` | `1.0` |
| `run_id` | 내부 실행 추적 ID |
| `status` | `SUCCEEDED` 또는 `FAILED` |
| `recommendation_mode` | `WARDROBE_BASED` 또는 `NEW_ITEM` |
| `match_result` | `VISUAL_SIMILAR`, `STYLE_SIMILAR`, `NO_CANDIDATE` |
| `selected_similarity` | 선택 후보 유사도, 선택 후보가 없으면 `null` |
| `fallback` | 스타일 fallback 사용 여부 |
| `stage_durations_ms` | 단계별 누적 처리 시간(ms) |
| `failure_code` | 성공이면 `null`, 실패면 안정적인 오류 코드 |
| `is_stylist` | 스타일리스트 응답 여부 |
| `duration_ms` | 전체 처리 시간(ms) |

단계 키는 `SNAPSHOT_VALIDATION`, `VECTOR_LOADING`, `SIMILAR_SEARCH`, `COMPOSER`,
`VALIDATOR`로 고정한다. 진입하지 않은 단계는 `0`이다.

친구 이름, 사용자 질문, 레퍼런스 스냅샷, 옷 태그, 원본 이미지·텍스트 벡터, 외부 시스템
주소와 예외 메시지는 이벤트에 저장하지 않는다.

## 집계 정의

조회 기간은 `start <= timestamp < end`인 반개구간이다. 모든 비율의 기본 분모는 해당
기간·필터에 포함된 전체 레퍼런스 추천 실행 수다.

| 지표 | 계산 |
|---|---|
| 성공률 | `SUCCEEDED 수 / 전체 실행 수` |
| 시각 유사 성공률 | `SUCCEEDED && VISUAL_SIMILAR 수 / 전체 실행 수` |
| 스타일 fallback 비율 | `STYLE_SIMILAR 수 / 전체 실행 수` |
| 후보 없음 비율 | `NO_CANDIDATE 수 / 전체 실행 수` |
| 평균 유사도 | `selected_similarity`가 있는 이벤트의 산술 평균 |
| 평균 전체 처리 시간 | `duration_ms`가 있는 이벤트의 산술 평균 |
| 평균 단계별 처리 시간 | 해당 단계 시간이 0보다 큰 이벤트만 산술 평균 |
| 공유 아이템 권한 오류 수 | `REFERENCE_ITEM_FORBIDDEN` 수 |
| 벡터 없음 수 | `REFERENCE_VECTOR_NOT_FOUND`, `REFERENCE_VECTOR_MISSING` 수 |
| 인덱스 불일치 수 | `REFERENCE_INDEX_MISMATCH` 수 |

결과는 전체(`overall`), 추천 모드별(`by_mode`), 응답 유형별
(`by_response_mode.DEFAULT/STYLIST`)로 나눈다. 조회 시 추천 모드와 응답 유형을 추가로
필터링할 수 있다. 이벤트가 없는 비율·평균은 `null`이다.

## 조회 방법

CloudWatch Logs Insights 조회에는 다음 환경변수가 필요하다.

```text
AWS_REGION=ap-northeast-2
REFERENCE_RECOMMENDATION_LOG_GROUP=<로그 그룹 이름>
REFERENCE_RECOMMENDATION_QUERY_LIMIT=10000
```

프로젝트의 `final` Conda 환경에서 실행한다.

```bash
python manage.py reference_recommendation_metrics \
  --start 2026-08-19T00:00:00+09:00 \
  --end 2026-08-20T00:00:00+09:00
```

모드·응답 유형 필터 예시:

```bash
python manage.py reference_recommendation_metrics \
  --start 2026-08-19T00:00:00+09:00 \
  --end 2026-08-20T00:00:00+09:00 \
  --mode WARDROBE_BASED \
  --response-mode STYLIST
```

로컬 JSONL 검증은 `--input <경로>`를 사용하고 표준 입력은 `--input -`를 사용한다.
CloudWatch 한 번의 조회가 `REFERENCE_RECOMMENDATION_QUERY_LIMIT`에 도달하면 일부 결과로
지표를 만들지 않고 실패한다. 이 경우 기간을 더 짧게 나눠 다시 조회한다.

## 운영 해석

- `NO_CANDIDATE`에는 검색 결과 부족뿐 아니라 매칭 전에 실패한 실행도 포함될 수 있으므로
  `status`와 `failure_code`를 함께 본다.
- 스타일 fallback 비율 상승은 시각 검색 기준, 벡터 품질, 보유 옷 수 변화를 함께 점검한다.
- 기본/스타일리스트 성공률 차이는 Composer 이후 단계 시간을 우선 비교한다.
- 알림 임계치는 트래픽 기준선이 쌓인 뒤 별도 운영 설정으로 정하며 코드 상수로 고정하지 않는다.
