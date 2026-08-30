# 개인 옷장 해시태그 요구사항·API 계약

- 문서 상태: 확정
- 확정일: 2026-08-18
- 적용 범위: 개인 옷장 해시태그, 필터·묶기·정렬, 개인 해시태그 채팅 추천
- 제외 범위: 룩북·캘린더 해시태그

## 1. 도메인 경계

1. `상의`, `하의` 등 기본 카테고리는 기존 taxonomy 8종과 고정 순서를 유지한다.
2. 사용자가 추가·수정·삭제하는 사용자 카테고리는 제공하지 않는다.
3. 사용자가 옷에 입력하는 정리 기준은 개인 옷장 전용 해시태그다.
4. 한 옷은 여러 해시태그에 속할 수 있다.
5. 해시태그는 한 벌 이상과 연결되면서 생성되고, 마지막 연결이 사라지면 자동 삭제된다.
6. 해시태그 이름 변경·독립 삭제 API는 제공하지 않는다.
7. 룩북·캘린더의 JSON 문자열 `hashtags`와 모델·API·자동완성 목록을 공유하지 않는다.
8. 공유 옷장에는 사용자 정의 카테고리·해시태그 기능을 제공하지 않는다. 남아 있는
   `SharedWardrobeCategory` 모델과 API는 레거시 호환용이며 신규 기능에서 사용하지 않는다.
9. 해시태그 연결 변경은 이미지·패션 태그가 아니므로 Qdrant를 재임베딩하지 않는다.

## 2. 모델

```text
WardrobeHashtag
- id: UUID PK
- user: 사용자 FK
- name: 정규화 후 표시 이름
- normalized_name: 사용자별 중복 판정값
- position: 사용자 표시 순서
- created_at
- updated_at

WardrobeItemHashtag
- id: UUID PK
- wardrobe_item: 개인 옷 FK
- hashtag: 옷장 해시태그 FK
- created_at
```

제약:

- 사용자별 `normalized_name` unique
- 옷–해시태그 연결 unique
- 옷과 해시태그의 소유 사용자 일치
- 새 해시태그는 사용자의 마지막 `position + 1`
- 정렬은 `position`, `created_at`, `id`
- 고아 해시태그 삭제 후 position을 0부터 연속으로 재배정

기존 `WardrobeCategory`, `WardrobeItemCategory`는 데이터 마이그레이션으로 모델·테이블을
이름 변경한다. UUID, position, 생성 시각과 옷 연결은 보존한다. 새 정규화 규칙에서 충돌하면
먼저 생성된 행을 유지하고 연결을 병합한다. 연결이 없는 기존 사용자 카테고리는 제거한다.

## 3. 이름 정규화

입력 순서:

1. 앞뒤 공백 제거
2. 맨 앞의 `#` 문자와 그 뒤 공백 제거
3. 연속 공백을 한 칸으로 축소
4. 비어 있으면 거부
5. 표시 이름은 정리된 입력값, 중복키는 casefold 값

`# 출근룩`, `출근룩`, `  출근룩  `은 같은 해시태그다. 최대 길이는 30자다. UI는 저장
이름 앞에 `#`을 붙여 표시하고 DB 이름에는 `#`을 저장하지 않는다.

## 4. 조회·연결 API

### `GET /api/v1/wardrobe/categories/`

기본 카테고리와 개인 해시태그를 함께 반환한다.

```json
{
  "system_categories": [
    {"id":"system:상의","type":"SYSTEM","name":"상의","position":0,"item_count":12,"mutable":false}
  ],
  "hashtags": [
    {"id":"uuid","name":"출근룩","position":0,"item_count":7}
  ]
}
```

### `POST /api/v1/wardrobe/hashtags/`

새 해시태그를 한 벌 이상의 옷과 함께 생성한다. 같은 정규화 이름이 있으면 기존 태그에
옷을 멱등 연결한다.

```json
{"name":"# 출근룩","item_ids":["wardrobe-item-uuid"]}
```

### `PATCH /api/v1/wardrobe/hashtags/{hashtag_uuid}/items/`

```json
{"add_item_ids":["uuid-1"],"remove_item_ids":["uuid-2"]}
```

제거 후 연결이 0개면 해시태그를 자동 삭제하고 응답의 `deleted`를 true로 반환한다.

### `PUT /api/v1/wardrobe/items/{item_uuid}/hashtags/`

아이템의 최종 해시태그 이름 집합을 전달한다. 서버가 정규화 후 기존 태그를 재사용하거나
새로 만들고, 빠진 연결을 제거한다.

```json
{"names":["출근룩","자주 입음"]}
```

응답은 `item_id`와 최종 `wardrobe_hashtags` 요약 배열이다.

### `PUT /api/v1/wardrobe/hashtags/order/`

```json
{"hashtag_ids":["uuid-2","uuid-1"]}
```

현재 사용자의 전체 해시태그 UUID를 중복·누락 없이 보내야 한다. 서버는 트랜잭션에서
position을 배열 인덱스로 갱신하고 최종 `hashtags` 배열을 반환한다. 같은 요청은 멱등하다.

독립적인 rename·delete 엔드포인트는 없다.

## 5. 아이템 응답

개인 옷장 목록·상세에 다음 필드를 포함한다.

```json
{
  "wardrobe_hashtags": [
    {"id":"uuid","name":"출근룩","position":0}
  ]
}
```

본인 아이템에만 개인 해시태그를 노출하고 목록은 prefetch로 N+1을 방지한다.

## 6. 필터·그룹·정렬

별도 선택 상태:

```text
selected_system_categories
selected_hashtag_ids
```

규칙:

```text
(기본 카테고리 선택값 OR, 미선택이면 전체)
AND
(해시태그 선택값 OR, 미선택이면 전체)
AND
검색어
```

- `상의 + 하의 + #출근룩`은 출근룩 옷 중 상의 또는 하의다.
- `전체 + #출근룩`은 출근룩에 속한 모든 기본 카테고리다.
- 기본 카테고리 그룹은 taxonomy 고정 순서다.
- 해시태그 그룹은 사용자 position 순서다.
- 다중 해시태그 옷은 각 해시태그 섹션에 표시한다.
- 해시태그가 없는 옷은 마지막 `해시태그 없음` 가상 섹션에 표시한다.
- 아이템 정렬은 `ADDED_DESC`, `COLOR_NAME_ASC`를 유지한다.

## 7. 모바일 화면

```text
[설정] [전체] [상의] [하의] [아우터] ...
[#출근룩] [#여행] [#자주입음] ...
```

- 설정 버튼은 묶기, 아이템 정렬, 해시태그 순서 편집을 연다.
- 그룹 모드는 `SYSTEM_CATEGORY`, `HASHTAG`다.
- 해시태그 드롭 즉시 화면에 반영하고 전체 순서를 서버에 저장한다.
- 실패하면 마지막 서버 확정 순서로 원복한다.
- 기존 카테고리 CRUD 시트는 해시태그 입력과 옷 선택 시트로 교체한다.
- 아이템 상세에서도 해시태그 입력·제거가 가능하다.
- 섹션은 여백, 얇은 구분선, 옅은 헤더 배경과 작은 포인트로 구분한다.

## 8. 보기 설정

서버 원본:

```text
GET/PATCH /api/v1/wardrobe/view-preferences/
group_mode: SYSTEM_CATEGORY | HASHTAG
item_sort: ADDED_DESC | COLOR_NAME_ASC
```

사용자별 로컬 캐시를 먼저 표시하고 서버와 동기화한다. 기존 로컬 `CUSTOM_CATEGORY` 값은
`HASHTAG`로 이전한다. 마지막 기본 카테고리·해시태그 필터와 검색어는 기억하지 않는다.

## 9. 채팅 요청

```json
{
  "content":"이 옷들로 출근 코디를 추천해줘",
  "client_message_id":"mobile-id",
  "wardrobe_scope": {
    "system_categories":["상의","하의"],
    "hashtag_ids":["hashtag-uuid"],
    "match_mode":"ANY",
    "match_policy":"REQUIRED"
  }
}
```

- `system_categories`와 `hashtag_ids`는 화면과 같은 AND 규칙이다.
- 여러 해시태그는 합집합이고 후보 UUID는 중복 제거한다.
- `REQUIRED`는 해시태그 후보 옷을 최소 한 벌 pinned anchor로 포함한다.
- `PREFERRED`는 후보에 가산점을 주되 포함을 강제하지 않는다.
- 자연어 이름만으로 해시태그 UUID를 추정하지 않는다.
- 공유 옷 참조 `reference`와 같은 요청에는 사용하지 않는다.

## 10. 추천과 보충

- 후보와 내 옷 보충은 현재 사용자 소유, 확정, 옷장 편입 완료 아이템만 사용한다.
- 기존 `accessible_item_ids`는 공유 옷을 포함하므로 해시태그 범위에서는 사용하지 않는다.
- `WARDROBE_BASED` 부족 슬롯은 다른 내 옷으로 보충한다.
- `NEW_ITEM` 부족 슬롯은 예산 내 판매·태깅 가능 상품으로 보충한다.
- `NEW_ITEM`에서도 pinned anchor는 기존 내 옷이며 나머지 슬롯만 상품이다.
- Validator는 REQUIRED 포함, 모드별 보충 출처와 타 사용자·공유 옷 누출을 검증한다.

## 11. 저장·복원

`ChatRun.wardrobe_scope_snapshot`에 다음을 저장한다.

- 기본 카테고리 조건
- 해시태그 UUID와 당시 이름·position
- `ANY`, `REQUIRED/PREFERRED`
- 접수 당시 후보 옷 UUID
- 선택 anchor UUID
- 보충 출처와 슬롯

`ChatRunSerializer`가 스냅샷을 반환하며 기본·스타일리스트 응답이 같은 계약을 사용한다.
추천 결과는 `HASHTAG_MATCH`, `WARDROBE_FALLBACK`, `NEW_ITEM_FALLBACK` 근거를 제공한다.

## 12. 오류 코드

| code | 의미 |
| --- | --- |
| `HASHTAG_NAME_REQUIRED` | 정규화 후 이름 없음 |
| `HASHTAG_NAME_TOO_LONG` | 30자 초과 |
| `HASHTAG_ITEM_REQUIRED` | 새 태그 생성에 연결할 옷 없음 |
| `HASHTAG_IDS_DUPLICATE` | UUID 중복 |
| `HASHTAG_ORDER_SET_MISMATCH` | 전체 순서 집합 불일치 |
| `HASHTAG_NOT_FOUND` | 태그 없음 |
| `HASHTAG_FORBIDDEN` | 다른 사용자 태그 접근 |
| `WARDROBE_SCOPE_INVALID` | 채팅 범위 형식 오류 |
| `HASHTAG_SCOPE_EMPTY` | REQUIRED 유효 후보 없음 |
| `HASHTAG_REQUIRED_MATCH_FAILED` | REQUIRED 결과에 anchor 없음 |
| `HASHTAG_FALLBACK_SOURCE_INVALID` | 모드와 보충 출처 불일치 |

## 13. 완료 기준

- 기존 사용자 카테고리 데이터가 해시태그로 자동 이전된다.
- 기본 카테고리와 해시태그가 2축 필터로 동작한다.
- 해시태그 입력·연결·고아 삭제·사용자 순서가 영속화된다.
- 룩북·캘린더·공유 옷장 도메인이 영향을 받지 않는다.
- 옷장·아이템 상세·채팅에서 같은 해시태그 UUID 계약을 사용한다.
- 해시태그 채팅 추천에서 공유 옷이나 다른 사용자 옷이 누출되지 않는다.
