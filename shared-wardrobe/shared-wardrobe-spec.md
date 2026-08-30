### 공유 옷장 (Shared Wardrobe) 설계 · 구현 명세서

- **작성일**: 2026-08-10 (초안) / **갱신**: 2026-08-16 (최신 구현 코드 대조 완료 `443aa13`)
- **작성자**: 전하영 (Jira SCRUM-282/283 관련)
- **브랜치**: `feature/shared-wardrobe`
- **검증 기준**: 아래 내용은 전부 `443aa13` 시점의 **실제 코드를 읽고 대조**했다.

---

## 1. 비즈니스 정책

### 1.1 구현된 룰

| #  | 룰                            | 상세                                                                                                                                  | 근거                                                               |
| -- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 1  | 방 생성                       | 개설자가 자동으로 `owner`. 방 개수 제한 없음. 방 이름 10자 이내 검증                                                                 | `shared_wardrobe.py` `create_shared_room`                          |
| 2  | 초대코드                      | 영대문자+숫자 6자리 난수. 중복 검사 최대 50회 재시도                                                                                  | `shared_wardrobe.py:11-18`                                         |
| 3  | 코드 유효기간                 | 24시간 (`code_expires_at`). 만료 시 가입 차단                                                                                       | `shared_wardrobe.py:25, 89-90`                                     |
| 4  | 코드 재발급                   | `owner`만 가능. 기존 코드 무효화 후 새 24시간 코드 발급                                                                             | `shared_wardrobe.py:57-70`                                         |
| 5  | 정원                          | **최대 6명**. 초과 시 가입 거부                                                                                                 | `shared_wardrobe.py:98`                                            |
| 6  | 중복 가입                     | 이미 멤버면 에러 대신 기존 방으로 정상 진입 (레코드 추가 안 함)                                                                       | `shared_wardrobe.py` `join_shared_room`                          |
| 7  | 동시성                        | 가입/탈퇴 시 `select_for_update()` 행 잠금 → 동시 요청 시 정원 6명 우회 및 유령 방 남김 차단                                          | `shared_wardrobe.py:83, 124`                                       |
| 8  | 멤버 색상                     | 가입 순서(배열 인덱스) 기반 고정 6색 (§4.2)                                                                                          | `shared-space-flow.tsx:85-112`                                     |
| 9  | 아이템 공유                   | 개인 옷장 원본은 유지한 채, 방과의 관계만 생성/삭제. **한 옷을 여러 방에 동시 공유 가능**                                             | `SharedWardrobeItem` 모델, `item-detail.tsx`                       |
| 10 | **공유 예약 DB화**            | 이미지 업로드 시 선택한 공유 방/상태를 백엔드 DB(`pending_share_room`, `pending_share_status`)에 보관 후 확정(PATCH) 시 자동 소진   | `models.py`, `shared_wardrobe.py:redeem_pending_share`             |
| 11 | **방장 탈퇴 위임**            | owner가 나가도 방을 폭파하지 않는다. 남은 멤버 중 **`joined_at`이 가장 빠른 사람에게 owner 자동 위임**. 남은 인원 0명일 때만 방 삭제 | `shared_wardrobe.py` `leave_shared_room`                         |
| 12 | **탈퇴 시 아이템 처리**       | `delete_my_items=True` → 내가 등록한 공유 아이템 일괄 삭제 / `False` → 옷은 방에 남기고 `registered_by`만 `NULL` 처리(기부)         | `shared_wardrobe.py` `leave_shared_room`, `views.py:316-323`       |
| 13 | **방 삭제 시 원본 옷 보호**   | 공유 방 삭제 시 해당 방의 공유 레코드만 삭제되며 **개인 원본 옷장(`WardrobeItem`)은 절대 삭제되지 않음**                             | `views.py` `SharedWardrobeViewSet.destroy`                        |

### 1.2 아직 구현 안 된 것

1. **앱 설치/미설치 분기 딥링크** — ✅ 2026-08-13 구현 (실기기 검증은 미실행)
   - 카카오 피드 템플릿 링크에 `iosExecutionParams`/`androidExecutionParams`로 `code`를 실었다
     → 앱이 있으면 카카오톡이 `kakao{네이티브앱키}://kakaolink?code=XXXXXX`로 **앱을 직접 실행**
   - 앱이 없으면 기존대로 `mobileWebUrl`(`/invite?code=...`) 웹 초대장으로 간다 — 카드 하나로 갈린다
   - 앱으로 들어온 URL은 expo-router가 매칭하지 못해서 `mobile/src/hooks/use-kakao-link.ts`가
     직접 파싱해 `/invite?code=`로 라우팅한다. `_layout.tsx` 최상위에서 1회 마운트
   - 앱이 꺼져 있다 켜진 경우(`getInitialURL`)와 떠 있는 채로 링크가 온 경우(`addEventListener`)를 **모두** 받는다
   - Android는 `app.json`의 core 플러그인에 `forwardKakaoLinkIntentFilterToMainActivity: true` 필요 (추가 완료).
     iOS는 `handleKakaoOpenUrl: true`가 이미 켜져 있어 추가 설정 없음
   - 🔲 남은 것: 앱스토어로 유도하는 안내 배너 (지금은 웹 초대장에서 코드 복사만 제공)

---

## 2. 데이터베이스 스키마 (Django Models)

전 테이블·컬럼에 `db_comment`가 붙어 있다 (프로젝트 CLAUDE.md §5 규칙 준수).

### 2.1 `SharedWardrobeRoom` — 공유 옷장 방

| 필드                | 타입           | 제약              | 설명                                         |
| ------------------- | -------------- | ----------------- | -------------------------------------------- |
| `id`              | UUIDField      | PK, default=uuid4 | 방 식별자 (외부 노출용)                      |
| `title`           | CharField(100) | Not Null          | 방 이름. 생성 시 사용자 입력, 이후 수정 가능 |
| `invite_code`     | CharField(6)   | Unique, Nullable  | 6자리 초대코드                               |
| `code_expires_at` | DateTimeField  | Nullable          | 코드 만료 시각 (발급 + 24h)                  |
| `created_at`      | DateTimeField  | auto_now_add      | 방 생성 일시                                 |

### 2.2 `SharedWardrobeMember` — 방 참여 멤버십

| 필드          | 타입                     | 제약                       | 설명                                                         |
| ------------- | ------------------------ | -------------------------- | ------------------------------------------------------------ |
| `id`        | BigAutoField             | PK                         |                                                              |
| `room`      | FK → SharedWardrobeRoom | CASCADE                    | 소속 방 (`related_name='members'`)                         |
| `user`      | FK → User               | CASCADE                    | 참여자 (`related_name='shared_rooms'`)                     |
| `role`      | CharField(10)            | choices, default`member` | `owner` / `member`                                       |
| `joined_at` | DateTimeField            | auto_now_add               | 참여 일시.**아바타 색상 순서 + 방장 위임 순서의 기준** |

### 2.3 `SharedWardrobeItem` — 방 ↔ 옷 연결

- **DB 테이블명**: `shared_wardrobe_item`
- **정렬**: `ordering = ["-created_at"]`

| 필드              | 타입                     | 제약                                        | 설명                                                                |
| ----------------- | ------------------------ | ------------------------------------------- | ------------------------------------------------------------------- |
| `id`            | UUIDField                | PK, default=uuid4                           |                                                                     |
| `room`          | FK → SharedWardrobeRoom | CASCADE,`related_name='items'`            | 소속 방                                                             |
| `wardrobe_item` | FK → WardrobeItem       | CASCADE,`related_name='shared_instances'` | **원본 옷 참조 (복사 아님)**                                  |
| `registered_by` | FK → User               | **SET_NULL**, Nullable                | 공유한 사람. 탈퇴 후 기부 시 NULL이 된다 (§1.1-11)                 |
| `status`        | CharField(15)            | choices, default`available`               | `available` 공유가능 / `borrowed` 대여중 / `private` 나만보기 |
| `created_at`    | DateTimeField            | auto_now_add                                | 공유 등록 일시                                                      |

- 이미지 주소는 `WardrobeItem.s3_key`에서 시리얼라이저가 계산해 내려준다. 이 테이블에 URL을 저장하지 않는다.
- 공유 해제 = 이 레코드 `DELETE`. 원본 `WardrobeItem`은 건드리지 않는다.
- ⚠️ `status`의 `borrowed`(대여중) 상태를 실제로 바꾸는 API·UI는 **아직 없다.** 스키마만 준비된 상태.

---

## 3. API 명세

기본 접두사 **`/api/v1/`** — 프론트에서 이 접두사가 빠져 `404`가 났던 이슈가 있었다 (§9).

| #  | 기능             | 메서드 · 경로                                            | 비고                                       |
| -- | ---------------- | --------------------------------------------------------- | ------------------------------------------ |
| 1  | 방 목록          | `GET /api/v1/shared-wardrobes/`                         | 내가 가입한 방 전체                        |
| 2  | 방 개설          | `POST /api/v1/shared-wardrobes/`                        | body`{title}` → 초대코드 동시 발급      |
| 3  | 방 이름 수정     | `PATCH /api/v1/shared-wardrobes/{room_id}/`             | ViewSet 기본 제공                          |
| 4  | 코드 재발급      | `POST /api/v1/shared-wardrobes/{room_id}/refresh-code/` | owner 전용                                 |
| 5  | 방 참여          | `POST /api/v1/shared-wardrobes/join/`                   | body`{invite_code}`                      |
| 6  | 멤버 조회        | `GET /api/v1/shared-wardrobes/{room_id}/members/`       | 색상 순서의 원천                           |
| 7  | 공유 아이템 목록 | `GET /api/v1/shared-wardrobes/{room_id}/items/`         |                                            |
| 8  | 아이템 공유 등록 | `POST /api/v1/shared-wardrobes/{room_id}/items/`        |                                            |
| 9  | 아이템 공유 해제 | `DELETE /api/v1/shared-wardrobes/{room_id}/items/...`   | 원본 보존                                  |
| 10 | 방 탈퇴          | `DELETE /api/v1/shared-wardrobes/{room_id}/leave/`      | body`{delete_my_items}` — §1.1-11 분기 |

### 3.1 에러 메시지 (백엔드 → 400 → 프론트 토스트)

| 상황             | 메시지                                                                                       |
| ---------------- | -------------------------------------------------------------------------------------------- |
| 없는 코드        | `유효하지 않은 초대코드입니다.`                                                            |
| 24시간 만료      | `초대코드가 24시간 만료 시간을 초과하여 사용할 수 없습니다. 방장에게 재발급을 요청하세요.` |
| 정원 초과        | `공유 옷장 정원(최대 6명)이 초과되어 가입할 수 없습니다.`                                  |
| 재발급 권한 없음 | `초대코드 재발급 권한이 없습니다. 방장만 재발급할 수 있습니다.`                            |
| 미참여 방 탈퇴   | `참여하고 있지 않은 공유 옷장 방입니다.`                                                   |
| 이미 멤버        | 에러 없이 기존 방으로 진입                                                                   |

---

## 4. 프론트엔드 (Expo / React Native Web)

### 4.1 화면 · 파일 맵

| 화면           | 파일                                                                         | 역할                                                                |
| -------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| 옷장 메인      | `mobile/src/app/(tabs)/closet.tsx`                                         | 내 옷장 / 공유 옷장 서브탭, 방 칩 목록, 아이템 그리드, 분석 진행률  |
| 공유방 플로우  | `mobile/src/components/closet/shared-space-flow.tsx`                       | 멤버 아바타, 초대 링크 생성, 카카오 공유                            |
| 초대 수락      | `mobile/src/app/invite.tsx` **(신규)**                               | `?code=` 파싱 → 수락 → `/(tabs)/closet?tab=shared` 리다이렉트 |
| 아이템 등록    | `mobile/src/app/item-add.tsx`                                              | 앨범형 3슬롯 그리드, 공유 토글 + 방 드롭다운, 등록 시작             |
| 아이템 상세    | `mobile/src/app/(tabs)/item-detail.tsx`                                    | 색상 카드 위에 공유 토글 + 드롭다운                                 |
| 태그 수정      | `mobile/src/components/closet/item-tag-sheet.tsx`                          | 분류·세부분류·색·패턴·핏 등 +`[+ 직접 입력]`                  |
| 사진 소스      | `mobile/src/components/closet/photo-source-sheet.tsx`                      | 카메라 / 앨범 /**임의 이미지(테스트용)**                      |
| 업로드 큐      | `mobile/src/state/upload-jobs.ts`                                          | 직렬 큐, 배치 진행률                                                |
| 초안 스토어    | `mobile/src/state/draft-item.ts`                                           | 다중 사진 배열로 개편됨                                             |
| 채팅 옷장 선택 | `mobile/src/components/chat/closet-item-select-sheet.tsx` **(신규)** | 내 옷장/공유 옷장 탭, 다중 선택                                     |
| 채팅 대화      | `mobile/src/components/chat/chat-conversation.tsx`                         | 드롭존, 옷장 아이콘 버튼                                            |
| 웹 탭 셸       | `mobile/src/components/app-tabs.web.tsx`                                   | 하단 바 높이 측정 (§7 무한루프 관련)                               |
| API 클라이언트 | `mobile/src/lib/wardrobeApi.ts`                                            | 공유 옷장 API 함수 전부                                             |
| AI 분석        | `api/apps/wardrobe/services/gemini.py` **(신규)**                    | Gemini 이미지 분석 (§5)                                            |

### 4.2 멤버 색상 — 가입 순서 기반 고정 매핑

해시 방식은 한글 유니코드가 몰려서 색이 겹쳤다. 해시를 걷어내고 **인덱스 순서 고정**(`MEMBER_COLORS[i % 6]`)으로 바꿨다.

| 순서 | 색   | HEX         |
| ---- | ---- | ----------- |
| 1    | 노랑 | `#FFD54F` |
| 2    | 하늘 | `#4FC3F7` |
| 3    | 연두 | `#81C784` |
| 4    | 핑크 | `#F06292` |
| 5    | 보라 | `#BA68C8` |
| 6    | 주황 | `#FFB74D` |

- 아바타 원과 아이템 카드 좌측 상단 소유자 배지(`나님`, `철수님`)에 **같은 색**을 쓴다.
- 1번(노랑)은 배경이 밝아 글자를 검정(`#1C1917`)으로 바꿔 대비를 확보했다.
- ⚠️ 인덱스 기준이 **API가 내려주는 배열 순서**다. 서버 정렬이 `joined_at`이 아니면 색이 뒤바뀐다.

### 4.3 아이템 등록 흐름

1. `[+ 아이템 추가]` → 사진 소스 선택 (카메라 / 앨범 / 임의 이미지)
2. 앨범형 **3칸 슬롯 그리드**. 좌우 여백을 음수 마진(`marginHorizontal: -20`)으로 상쇄해 모달 폭을 꽉 채운다. 슬롯 간격 2px
3. **최대 3장** 동시 선택. 초과 시 토스트 경고 + 추가 버튼 숨김
4. 헤더 우측: `[등록 시작]` 버튼이 `[X]` 닫기 바로 옆
5. 공유 컨트롤: 좌측 `[공유 ON/OFF]` 스위치 + 우측 `[방 선택]` 드롭다운을 **5:5 고정 배치**. OFF면 드롭다운 회색 비활성, ON이면 활성
6. `[등록 시작]` → 모달 즉시 닫힘 → 백그라운드 **직렬 큐**로 1장씩 처리
7. 옷장 상단에 `옷장 분석중 (1/3) → (2/3) → (3/3)` 진행률 표시
8. 공유 ON이면 개인 옷장 등록과 **동시에** 선택한 공유방에도 등록

### 4.4 초대 링크

- 형식: `{origin}/invite?code=XXXXXX`
- `makeInviteLink()`가 웹에서 `window.location.origin`을 읽어 동적 생성 (로컬이면 `http://localhost:8081`, 배포면 `https://skn-1st-mobile.expo.app`)
- **보안 판단**: 쿼리스트링에 코드를 싣는 건 Slack·Notion과 같은 표준 방식. 6자리(약 21억 조합) + 24시간 만료 + owner 재발급으로 방어한다
- **카카오 공유** (2026-08-13 개편, `mobile/src/lib/kakaoShare.ts`로 분리):

| 플랫폼        | 경로                                                                                     | 키                                                             |
| ------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| iOS / Android | `@react-native-kakao/share` `shareFeedTemplate` → 카카오톡이 열려 친구·채팅방 선택 | **네이티브 앱 키** (`initSocialSDKs`에서 초기화)       |
| 웹            | Kakao JS SDK`Share.sendDefault` → PC는 카카오 공유 팝업, 모바일 웹은 카카오톡 앱      | **JavaScript 키** (`EXPO_PUBLIC_KAKAO_JAVASCRIPT_KEY`) |

- ⚠️ **웹에서 네이티브 앱 키로 `Kakao.init`을 부르면 예외 없이 조용히 실패한다.** 개편 전 코드가 정확히 이 상태였다 (`shared-space-flow.tsx`에서 `KAKAO_NATIVE_APP_KEY`로 init). 상수를 `KAKAO_JAVASCRIPT_KEY`로 분리해 다시 못 섞게 했다
- 웹은 카카오 개발자센터 > 플랫폼 > Web 에 **도메인 등록이 선행**되어야 한다 (`http://localhost:8081` 포함)
- 카드 본문(description)에 참여 코드를 적고, 공유를 누르는 순간 **초대 문구 전체를 클립보드에도 복사**한다 (`expo-clipboard`). 카톡이 안 열려도 사용자가 직접 붙여넣을 수 있게 하는 폴백
- 폴백 순서: 카카오 SDK → OS 공유 시트 → 클립보드. 어디서 끝났는지를 `KakaoShareResult`로 돌려주고 토스트 문구를 다르게 띄운다
- iOS `LSApplicationQueriesSchemes`의 `kakaolink`는 `@react-native-kakao/core` 플러그인이 이미 넣어 준다 — app.json 추가 설정 불필요. 다만 **네이티브 공유는 dev client 재빌드가 필요**하다 (Expo Go 불가)

### 4.5 채팅 ↔ 옷장 연동

이미 등록한 옷을 물어볼 때마다 사진을 다시 올리게 하지 않으려고 만든 기능.

- **데스크톱 웹**: 옷 카드를 마우스로 잡아 코지 채팅 패널에 **드래그 앤 드롭** (HTML5 `draggable` + `onDragStart` / `onDragOver` + `onDrop`)
- **모바일/태블릿**: 채팅 입력바의 옷 아이콘 → 선택 모달(내 옷장 / 공유 옷장 탭) → 다중 선택 → `[N개 옷 선택 완료]`
- 공유 옷장 탭에서는 **누가 올린 옷인지 아바타 이름도 함께** 보인다
- 첨부된 옷 조합을 Cozy가 읽고 룩북 코디를 제안

---

## 5. Gemini AI 의류 분석

| 항목        | 내용                                                                      |
| ----------- | ------------------------------------------------------------------------- |
| 모델        | `gemini-2.5-flash` (멀티모달)                                           |
| 서비스 파일 | `api/apps/wardrobe/services/gemini.py` (신규, 93줄)                     |
| 진입점      | `gemini.analyze_clothing_image(local_path)` — `views.py:78`에서 호출 |
| 입력        | 업로드된 옷 이미지 (로컬 경로)                                            |
| 출력        | 구조화 JSON — 이름, 대분류, 소분류, 색상                                 |
| 저장        | `WardrobeItem`의 `s3_key`, `color` 등 개별 컬럼에 매핑              |
| 키 관리     | `GEMINI_API_KEY` — 루트 `.env` → Docker 컨테이너 주입               |

### 5.1 429 Rate Limit 대응

3장을 동시에 던지면 프리티어 동시성 한도에 걸려 `429 Too Many Requests`가 났다.

- 1차 시도: 1.5초 간격 지연 전송 → 불충분
- 최종: **직렬 큐**. 1장이 완전히 끝날 때까지(`await`) 대기한 뒤 다음 장 시작. 동시 요청이 원천적으로 0이 된다
- 부수 효과: `(N/3)` 진행률이 실제 완료 시점과 정확히 동기화됨

### 5.2 키 취급 — 확인 완료

- ✅ 루트 `.env`는 `.gitignore`에 들어있다 (`.env` / `*.env` / `.env.*`, 예외는 `.env.example` 계열만). `git ls-files .env`도 미추적 확인. **키 유출 위험 없음**
- ⚠️ 단, Postgres 비밀번호가 2026-08-10 맥 세션 대화 로그에 평문으로 남아 있다. 해당 transcript 파일(iCloud `Antigravity/`)을 공유하거나 커밋하지 말 것

---

---

## 6. 개발 및 인프라 설정 참고사항

### 6.0 환경 설정 원칙

`DJANGO_SETTINGS_MODULE`은 **Infisical 주입이 원칙**이다. `swagger_noauth`는 로컬 개발/테스트용 모드이며 compose 파일에 리터럴로 박지 않는다.

로컬에서 인증 우회가 필요할 때만 주입값 위에 한 번 덮어쓴다.

```bash
infisical run --env=dev -- env DJANGO_SETTINGS_MODULE=config.settings.swagger_noauth \
  docker compose up -d db qdrant api outfit-worker redis
```

---

### 6.1 다중 사용자 미검증

로컬은 `swagger_noauth` 인증 우회 모드라 **모든 요청이 `dev_autologin` 단일 계정**으로 들어온다. 내가 만든 방에 내가 초대 링크로 들어가면 중복 가입 방지(§1.1-6)에 걸려 멤버가 1명에서 안 늘어난다.

→ **정원 6명 제한, 가입 순서 색상 매핑, `select_for_update` 동시성 락, 방장 위임(§1.1-10)은 실제 다중 계정으로 검증된 적이 없다.** 실계정 2개 이상으로 재현 테스트가 필요하다.

---

## 7. 미해결 · 미검증

| # | 항목                                      | 상태                                                                                        | 근거                                                                                                                                                                                                                                                                                                                                                  |
| - | ----------------------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | 옷장 탭 무한 렌더링                       | **코드 수정 확인 · 런타임 미검증**                                                   | 원인 2개 모두 코드에서 수정 확인됨: ①`app-tabs.web.tsx:116-121`에 `if (h !== barHeight)` 가드 존재 ② `upload-jobs.ts:173/177`이 원시 숫자 반환 훅(`useBatchTotal`/`useBatchCompletedCount`)이고 `closet.tsx:128-129`가 이걸 쓴다. `useBatchProgress`는 export되지 않는다. **다만 실제로 앱을 띄워 재현 테스트를 하지는 않았다** |
| 2 | `SharedWardrobeItem.status` 대여 플로우 | 스키마만 존재                                                                               | `borrowed`로 바꾸는 API·UI 없음 (§2.3)                                                                                                                                                                                                                                                                                                            |
| 3 | 카카오톡 카드 공유 모바일 실기기 동작     | 미검증                                                                                      | PC 웹에서만 테스트                                                                                                                                                                                                                                                                                                                                    |
| 4 | 앱 설치/미설치 분기 딥링크                | **코드 구현 완료 · 실기기 미검증** — 시뮬레이터로는 카카오톡 앱 실행 경로를 못 탄다 | §1.2                                                                                                                                                                                                                                                                                                                                                 |
| 5 | 다중 계정 시나리오 전반                   | 미검증                                                                                      | §6.1                                                                                                                                                                                                                                                                                                                                                 |
| 6 | 비동기 워커 경로 (Celery/Redis)           | 미검증                                                                                      | 로컬은 §6-5로 우회 중                                                                                                                                                                                                                                                                                                                                |

**1번 확정 방법**: `npx expo start` → `옷장` 탭 클릭 → 에러 팝업이 안 뜨면 해결 확정.

`getBatchProgress()`(`upload-jobs.ts:65`)는 여전히 객체를 반환하지만 **어떤 훅도 이걸 `useSyncExternalStore`에 넘기지 않는다.** 나중에 누가 이걸 훅으로 감싸면 같은 무한루프가 재발하니, 쓰지 않는다면 지우는 편이 안전하다.

---

## 8. 로컬 실행 절차

```bash
git checkout feature/shared-wardrobe
git pull

# 백엔드 (프로젝트 루트) — 필요한 코어 서비스만
infisical run --env=dev -- docker compose up -d --build db qdrant api outfit-worker redis
# weather-collector / naver-collector는 띄우지 않는다 (이 기능과 무관, 리소스 낭비)

# 프론트엔드
cd mobile
npm install
npx expo start
```

- 접속: **`http://localhost:8081/closet`**
  - ⚠️ `https://skn-1st-mobile.expo.app/closet`은 **원격 배포본**이라 로컬 백엔드를 안 본다. 로컬 변경사항이 하나도 안 보인다
- DB 직접 확인: `localhost:5432` / DB `fashion_db` / user `postgres` (비밀번호는 Infisical에서 조회)
- 백엔드 코드는 볼륨 마운트가 아니라 **이미지에 빌드**된다. `api/` 수정 후에는 반드시 `--build`로 재기동
- `GEMINI_API_KEY`를 루트 `.env`에 넣어야 AI 분석이 동작한다
- `docker compose down`도 yml 전체를 파싱하므로 앞에 `infisical run --`를 붙여야 한다

---

## 9. 트러블슈팅 기록 (같은 함정 재발 방지)

| 증상                         | 원인                                                                                                                                        | 해결                                                                                                               |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 공유 옷장 개설 실패`404`   | 프론트 API 경로에`/api/v1` 접두사 누락                                                                                                    | `wardrobeApi.ts` 전체 경로 수정                                                                                  |
| 모든 요청`401`             | `DJANGO_SETTINGS_MODULE=config.settings.swagger` (인증 요구)                                                                              | `swagger_noauth`로 전환                                                                                          |
| `.env` 고쳐도 계속 `401` | Infisical 주입값이 로컬`.env`보다 우선한다 (**정상 동작**)                                                                          | 당시엔 compose에 하드코딩으로 막았으나 원복함. 지금은`env DJANGO_SETTINGS_MODULE=...`로 한 번만 덮어쓴다 (§6.0) |
| `Failed to fetch`          | CORS. 컨테이너 안`CORS_ALLOWED_ORIGINS`가 `[]`                                                                                          | `swagger_noauth.py`에 `CORS_ALLOW_ALL_ORIGINS = True`                                                          |
| `docker compose down` 실패 | down도 yml 전체를 파싱해서`REDIS_PASSWORD` 미주입 에러                                                                                    | down 앞에도`infisical run --` 붙이기                                                                             |
| 초대 링크`Unmatched Route` | `/invite` 라우트 파일 자체가 없었음                                                                                                       | `mobile/src/app/invite.tsx` 신규 생성                                                                            |
| 수락했는데 내 옷장이 뜸      | 리다이렉트가 기본 탭(`mine`)으로 감                                                                                                       | `?tab=shared` 파라미터 + `useLocalSearchParams` 감지                                                           |
| 멤버가 계속 1명              | `dev_autologin` 단일 계정 + 프론트 `members: ['나']` 하드코딩                                                                           | `listSharedRoomMembers` API 연동 + DEBUG 목 멤버 (§6-2)                                                         |
| 아바타 색이 노란색으로 몰림  | 한글 유니코드 해시가 고르게 분산되지 않음                                                                                                   | 해시 제거, 인덱스 순서 고정 매핑 (§4.2)                                                                           |
| 아이템 등록`500`           | `WardrobeItem(image_url=...)` — 모델에 없는 필드                                                                                         | `s3_key=key`로 정정                                                                                              |
| 이미지 업로드 실패           | S3 자격증명 없음                                                                                                                            | `IS_LOCAL` 조건부 로컬 `media/` 저장 (§6-4)                                                                   |
| Gemini`429`                | 3장 동시 요청                                                                                                                               | 직렬 큐 (§5.1)                                                                                                    |
| `Platform is not defined`  | `item-tag-sheet.tsx`에서 `Platform` import 누락                                                                                         | import 추가                                                                                                        |
| 옷장 탭 무한 렌더링          | ①`onLayout`이 매 렌더마다 `setBarHeight` 호출 ② `useSyncExternalStore`에 매번 새 객체 `{total, completed}` 반환 → 참조 비교 실패 | ① 높이 변화 가드 ② 원시 숫자 훅 2개로 분리 (§7-1)                                                               |

---

## 10. Qdrant 코디 추천 검색 격리 & 아이템 상태 관리 구현

### 10.1 공유 옷장 추천 검색 격리 (Retrieval Search Isolation)

- **개념**: Qdrant 옷장 추천 검색 시 단일 `user_id` 조건을 **"접근 가능한 아이템 id 화이트리스트"** (`allowed_item_ids`)로 일반화
- **접근 범위 (`accessible_item_ids`)**:
  - 내 옷: `user=user, confirmed=True`
  - 공유 옷: `room__members__user=user, status=AVAILABLE, wardrobe_item__confirmed=True`
  - `created_at` 내림차순 정렬 및 `RETRIEVER_WARDROBE_ID_CAP` (기본 1000건) 상한 제한
- **유출 방지 가드**: `allowed_item_ids=[]` 전달 시 Qdrant 조회를 수행하지 않고 `[]`를 즉시 반환하여 타인 옷 유출 원천 차단
- **컬렉션 상수화**: `WARDROBE_ITEM_COLLECTION` (`os.getenv("QDRANT_WARDROBE_COLLECTION", "wardrobe_items")`) 사용

### 10.2 공유 아이템 상태 변경 API 및 클라이언트 함수

- **API 엔드포인트**: `PATCH /api/v1/shared-wardrobes/{room_id}/items/` (`item_id` 또는 `wardrobe_item_id`, `status` 바디 전달)
- **상태 종류**: `available` (공유가능), `borrowed` (대여중), `private` (나만보기)
- **권한 체크**: 방 멤버이면서 아이템 공유 등록자 또는 방장(`owner`)만 변경 가능
- **클라이언트 함수**: `updateSharedItemStatus(roomId, itemId, status)` (`mobile/src/lib/wardrobeApi.ts`)
