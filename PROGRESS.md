# 진행 상태

## 2026-08-13

- Docker Desktop 엔진 29.6.2 실행 확인.
- Infisical CLI 설치 경로 확인.
- 공유 옷장·추천·모바일 파일을 포함한 미해결 병합 충돌 10개 확인.
- 공유 기능, 추천 연결, 런타임 환경을 병렬 조사 중.
- Confluence `공유 옷장(Shared Wardrobe) 설계 · 구현 명세서` v5 확인.
- 구현 기준: 관계 기반 공유, 최대 6명, confirmed 아이템만 공유, DB ID 화이트리스트 방식의 공유 옷 추천 연결.
- 로컬 실행은 `config.settings.swagger_noauth`와 `localhost:8000/api/docs/`를 사용하도록 `run-api.ps1` 갱신.
- `main` 병합 충돌 10개 해소. 공유 UI와 서버 추천 카드 경로를 함께 보존.
- 공유 정책 보강: confirmed 아이템만 공유, 방 삭제 owner-only, migration graph merge.
- 추천 연결: 개인·공유 available 아이템 ID를 DB에서 계산해 Qdrant whitelist 검색에 전달.
- 모바일: 실제 클립보드와 카카오 공유 SDK 연결, 추천 목업 제거 후 서버 채팅 추천 사용.
- 검증: Django 공유·추천 120개 테스트 통과, TypeScript 검사 통과.
- 실행: Infisical dev 주입 완료, Docker API 스택 기동, Swagger/공유/추천 API 모두 HTTP 200.
- 로컬 주소: 앱 `http://localhost:8081`, Swagger `http://localhost:8000/api/docs/`.
- 공유방 사용자 정의 카테고리 DB 추가: `shared_wardrobe_category`, `shared_wardrobe_item_category`.
- `wardrobe.0008_shared_wardrobe_categories`를 개발 DB에 적용하고 두 테이블 생성 확인.
- 공유 옷장 카테고리 `GET/POST/DELETE /api/v1/shared-wardrobes/{room_id}/categories/` 구현.
- 모바일 카테고리 관리 저장 버튼을 공유방별 API와 연결하고 추가·삭제 후 DB 목록 재조회.
- 검증: 모바일 TypeScript 통과, 공유 옷장 API 테스트 10개 통과, Swagger HTTP 200.
- 로컬 백엔드 옷 등록에 Gemini 직접 태깅 활성화(`LOCAL_GEMINI_TAGGING=1`, 로컬 저장소 한정). → **2026-08-14 원복 완료** (코드·compose 항목·테스트까지 전부 삭제).
- Gemini 인증키를 URL 쿼리에서 `x-goog-api-key` 헤더로 이동하고 모델을 `gemini-3.5-flash`로 갱신.
- 백엔드 샘플 업로드 실측 성공: 트렌치코트가 아우터/코트/베이지로 분석되어 약 6.7초 내 DONE 저장.

## 2026-08-14

- Infisical dev 주입으로 Docker API, PostgreSQL, Redis, Qdrant 및 백엔드 worker 기동 확인.
- health live/ready와 Swagger HTTP 200, Django system check 이상 없음.
- Gemini 직접 태깅이 S3 원본도 임시 다운로드해 처리하도록 보강하고 실제 API 업로드를 검증함.
- 실제 업로드 결과: HTTP 201, job DONE, 원본 파일명·완료시각·태그된 WardrobeItem 1개 DB 저장 확인.
- 빈 파일명과 실패 콜백의 빈 오류 메시지를 수정하고 회귀 테스트 추가.
- 공유 옷 등록→DB 저장→다른 멤버 조회→공유 해제 시 개인 원본 보존 흐름 확인.
- 공유 옷 room+item 유니크 제약 및 private 아이템 접근 차단 추가; wardrobe migration 0009 개발 DB 적용.
- 옷장·공유 옷장 Docker 테스트 28개 통과.
- 카카오 모바일 TypeScript 및 Expo native config 생성 통과; 실제 메시지 전송은 실기기·카카오 콘솔 설정 확인 필요.
- 기존 b55f89bf job은 PENDING이며 연결된 S3 원본이 404라 완료 불가. 기존 Redis 옷장 큐 적체는 GPU 워커 검증 범위에서 제외.
- 카카오 공유 정책 확정: 네이티브 모바일은 초대 문구를 먼저 복사한 뒤 카카오톡 공유 SDK를 열고, PC 웹은 카카오/OS 공유창 없이 문구만 복사.
- 모바일 TypeScript, Expo native config introspect, Expo web production export 통과.
- Swagger/OpenAPI 옷장·공유 옷장 관련 경로 20개 노출 확인.
- 옷 상세 GET 및 add-to-closet 응답 스키마를 보강하고, 공유 카테고리 DELETE의 category_id를 Swagger query 입력으로 노출.
- 옷장·공유 옷장·Swagger 회귀 테스트 37개 통과; 실행 서버 schema에서 수정 operation/parameter 반영 확인.
- 현재 브라우저 제어 연결이 없어 Swagger UI 버튼 직접 클릭은 미검증. 동일 API 요청 및 OpenAPI 계약 검증으로 대체했으며 UI 클릭과 실제 카카오 앱 전환은 실기기에서 최종 확인 필요.
- 공유방 전체 API 생명주기 테스트 추가: 생성→목록→상세→수정→초대코드 재발급→익명 미리보기→참여→멤버 조회→탈퇴.
- Swagger 핵심 옷장·공유 옷장 operation 27개의 summary/tag/response 전수 검사 추가.
- 누락됐던 공유방 목록·상세·수정 및 공유 아이템 상태 PATCH Swagger 설명 보강.
- 옷장·공유 옷장·Swagger 테스트 38개 통과, 최신 Docker API 반영 후 ready/docs/schema 모두 HTTP 200.
- 브라우저 세션 목록이 계속 비어 있고 Android SDK(adb/emulator)도 없어 UI 직접 클릭·카카오 앱 전환 검증은 환경 준비 전까지 진행 불가.
- 공유 카드 상세 404 수정: 카드 클릭 시 SharedWardrobeItem ID 대신 원본 WardrobeItem ID 전달.
- 공유방 멤버는 공유된 옷 상세 GET 가능, 외부인은 404이며 타인 옷 상세는 읽기 전용 UI로 제한.
- 검증: TypeScript 통과, 공유 옷장 테스트 11개 통과, 상세 권한 200/404 확인.
- 카카오 공유용 `EXPO_PUBLIC_KAKAO_JAVASCRIPT_KEY`가 Infisical dev에 존재함을 값 노출 없이 확인.
- Expo를 Infisical 주입 상태로 8081에 재기동하고 `run-mobile.ps1` 실행 경로 추가.
- Kakao JavaScript SDK를 2.8.0으로 갱신. 로컬 앱 HTTP 200 확인.
- 웹 옷 사진 업로드 400 대응: MIME 문자열 대신 파일 헤더로 jpeg/png/webp/heic 검증하도록 변경해 HEIC/Pillow 및 브라우저 MIME 차이를 제거.
- API 오류 파서가 DRF 필드별 검증 메시지를 화면에 표시하도록 보강. Django wardrobe 테스트 29개와 모바일 TypeScript 검사 통과.
- 공유방 보유 여부와 무관하게 공유 탭 방 목록에서 `코드로 참여` 시트를 열 수 있도록 상시 진입 버튼 추가.
- 카카오 웹 공유를 클립보드 전용에서 HTTPS Kakao JavaScript SDK `Share.sendDefault` 호출로 변경; HTTP localhost는 안전하게 복사 폴백 유지.
- 추천 운영 파이프라인이 `accessible_item_ids` 결과를 Qdrant `HasIdCondition` 화이트리스트로 전달해 다른 멤버의 AVAILABLE 공유 옷도 후보에 포함. 관련 추천 테스트 117개 및 모바일 TypeScript 통과.
- Expo HTTPS tunnel은 @expo/ngrok 설치 후에도 Metro watch mode가 시작되지 않아 주소 발급 실패. 패키지는 원복했으며 카카오 콘솔 도메인 등록은 HTTPS 주소 미확정으로 미실행.
- main 비교용 내부 worktree는 Expo 감시 충돌을 일으켜 완전히 제거. 별도 외부 경로 worktree 재생성은 후속 작업 필요.
- 커밋 전 배포 안전성 정리: `run-mobile.ps1`의 localhost 강제값을 원복하고 `mobile/eas.json`의 만료 가능한 trycloudflare API 하드코딩을 제거해 환경별 EAS/Infisical 값을 사용하도록 함.
- 임시 HTTPS 전용 `run-shared-https.ps1`, `scripts/local-https-nginx.conf`와 Docker 프록시·터널 컨테이너를 제거함.
- 검증: 공유 옷장·추천 검색 회귀 테스트 134개, 모바일 TypeScript, Expo 공개 설정, `git diff --check` 통과.
- 최신 `origin/main`(`e9149a3`)과 현재 커밋은 자동 병합 충돌 0건. 미커밋 변경 중 양쪽이 함께 수정한 파일은 추천 3개와 `mobile/src/constants/config.ts` 총 4개로 커밋 후 병합 시 수동 확인 필요.
- 운영 GPU 이미지 프로세서를 사용하기로 확정해 로컬 옷 등록용 Gemini 직접 태깅 코드·환경변수·로컬 파일 저장 분기를 제거함. 추천/코디 GPU 워커의 기존 Gemini 서비스는 유지.
- 로컬 인증 우회 설정(`noauth`, `swagger_noauth`, AutoLoginAuthentication)과 관련 실행·문서 안내를 제거하고 Swagger는 JWT 인증 방식만 유지.
- 정리 후 공유 옷장·추천 검색 테스트 136개, TypeScript, Expo config, migration 누락 검사, `git diff --check` 통과.

## 2026-08-20

- `feature/shared-wardrobe`에 최신 `origin/main`(`4788688`)을 병합하고 기존 로컬 변경 7개를 stash로 보존·복원함.
- `api/apps/wardrobe/views.py` 충돌은 공유방 멤버 조회와 최신 reference eligibility 계산을 모두 유지해 해결함.
- 카카오 네이티브 초대 링크를 `+native-intent.tsx`에서 `/invite?code=...`로 변환하는 수정 반영 확인.
- 카카오 딥링크 회귀 테스트 6개와 모바일 TypeScript 검사 통과. Docker Desktop 미실행으로 백엔드 컨테이너 테스트는 실행하지 못함.
- 체형 사진 측정에 사람 1명, 머리·얼굴·양발, 전신 프레이밍, 정면·측면 자세, 화질 VLM 검증을 추가함.
- 부적합 사진은 치수 저장을 막고 `photo_quality_failed`로 응답하며, 앱에서 `사진 인식 실패`와 재촬영·기본정보 추정 선택지를 표시함.
- 기본정보 fallback 결과에는 `사진을 인식하지 못해 키·몸무게·성별만으로 추정한 값`임을 명시함.
- 검증: ML 10개, 모바일 상태·출처 10개, TypeScript, 대상 ESLint, Python compileall, JSON, migration 누락 검사 통과. PostgreSQL/Docker 미실행으로 Django DB 테스트 22개는 실행하지 못함.
