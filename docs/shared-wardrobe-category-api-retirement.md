# 공유 옷장 사용자 정의 카테고리 API 폐기 계획

- 문서 상태: 확정
- 확정일: 2026-08-19
- 대상: `SharedWardrobeCategory`, `SharedWardrobeItemCategory`, 공유방 `categories` API

## 결정

공유 옷장에는 사용자 정의 카테고리나 해시태그 기능을 제공하지 않는다. 이 기능은
후속 기능 후보가 아니라 제품 범위에서 삭제된 요구사항이다.

- 공유 옷장 화면의 `상의`, `하의` 등 필터는 공유 아이템 원본의
  `wardrobe_item.category_large`를 사용한다.
- 개인 옷장 해시태그는 사용자 개인 데이터이며 공유방으로 복사하거나 연결하지 않는다.
- 공유 옷 레퍼런스 추천은 공유 카테고리가 아니라 `SharedWardrobeItem.id` 한 개를 입력으로
  사용한다.
- 룩북·캘린더 해시태그와도 모델·API·자동완성 목록을 공유하지 않는다.

## 현재 API 처리

다음 API는 현재 모바일에서 호출하지 않는다.

```http
GET    /api/v1/shared-wardrobes/{id}/categories/
POST   /api/v1/shared-wardrobes/{id}/categories/
DELETE /api/v1/shared-wardrobes/{id}/categories/?category_id={uuid}
```

프론트의 미사용 API 함수는 제거하고, OpenAPI에는 세 동작을 `deprecated`로 표시한다.
신규 프론트·채팅·추천 기능은 이 API에 의존하면 안 된다.

DB 모델과 API 구현은 기존 배포 데이터 및 다른 브랜치의 호출 여부를 확인하기 전까지
호환 목적으로 유지한다. 유지 기간에도 신규 데이터 생성을 권장하지 않는다.

## 제거 순서

1. 운영 액세스 로그와 전체 클라이언트에서 세 API 호출이 없는지 확인한다.
2. 기존 카테고리와 아이템 연결 데이터의 보관 필요 여부를 결정하고 백업한다.
3. URL action, serializer, service/test, admin 등록을 제거한다.
4. 별도 Django migration으로 M2M 필드와 두 레거시 테이블을 제거한다.
5. OpenAPI 경로와 이 폐기 문서에서 호환 기간 종료를 기록한다.

모델·테이블 삭제는 데이터 파괴가 수반되므로 이 작업 9에서는 수행하지 않는다.
