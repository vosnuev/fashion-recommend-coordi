# 👗 COZY — 옷장에서 시작하는 AI 패션 추천

<p align="center">
  쇼핑몰이 팔고 싶은 옷이 아니라, <b>내 옷장에서 오늘 입을 수 있는 옷</b>을 추천합니다.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Django" src="https://img.shields.io/badge/Django%205.2-DRF-092E20?style=for-the-badge&logo=django&logoColor=white">
  <img alt="React Native" src="https://img.shields.io/badge/React%20Native-Expo%2057-61DAFB?style=for-the-badge&logo=expo&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white">
  <img alt="Qdrant" src="https://img.shields.io/badge/Qdrant-vector%20search-DC244C?style=for-the-badge">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white">
</p>

## ⌨️ 프로젝트 개요

- **프로젝트명** : COZY (SK네트웍스 Family AI 캠프 28기 · 최종 프로젝트 1팀)
- **기간** : 2026.07.08 ~ 2026.08.27
- **구성원** : 6명 (모바일 · API/인프라 · 추천/채팅 · ML · 이미지 워커 · 기획)
- **내 역할** : 신체치수 추정 ML·API, 공유 옷장 풀스택, 골든셋·모델 벤치마크
- **원본 저장소** : [SKNETWORKS-FAMILY-AICAMP/SKN28-FINAL-1Team](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN28-FINAL-1Team) (이 저장소는 fork)

<br/>

---

<a name="mywork"></a>

# 🙋 내가 한 일 — 전하영 ([@vosnuev](https://github.com/vosnuev))

> 팀 저장소 전 브랜치 기준 **커밋 156개(비머지 130개)**. 아래는 그중 기능 단위로 묶어 정리한 것이고,
> 각 항목은 저장소 안의 코드·문서·평가 결과로 근거를 확인할 수 있다.

| 영역 | 내가 맡은 범위 | 스택 |
|---|---|---|
| **신체치수 추정** | 데이터 구축 · 모델 학습 · VLM 후보 벤치마크 · 추정 API 2종 · 사진 품질 검증 | scikit-learn · OpenRouter VLM · Django |
| **공유 옷장** | 도메인 설계 · 방/멤버/아이템 API · 동시성 제어 · 추천 검색 연동 · 모바일 UI | Django/DRF · PostgreSQL · Qdrant · React Native |
| **추구미(pursuit)** | 옵션·조회·저장 API와 모바일 화면 연결 | Django/DRF · React Native |
| **골든셋** | 체형·색상 판단 규칙 데이터셋 구축, 추천 가중치 레이어 설계 | 데이터 · 규칙 설계 |
| **캘린더 · 옷장 UX** | 단일 슬롯 중복 방지, 옷장·채팅 연동 UX, 실사용 버그 수정 | Django/DRF · React Native |
| **팀 인프라** | Infisical 시크릿 온보딩 가이드, compose 환경변수 주입 방식 문서화 | Infisical · Docker Compose |

<br/>

## 1️⃣ 신체치수 추정 — 줄자 없이 14개 치수를 만든다

체형에 맞는 추천을 하려면 치수가 필요한데, **사용자는 줄자로 재서 입력하지 않는다.** 그래서 입력을 `성별·키·몸무게` 세 개로 줄이고, 나머지는 추정하기로 했다.

### 무사진 경로 — 모델 하나가 아니라 셋을 합친다

| 출력 | 개수 | 모델 | 학습 모집단 |
|---|---:|---|---|
| `chest` `waist` `hip` `shoulder` | 4 | `hist_gradient_boosting_181` | SizeKorea 8차 **직접측정** (학습 172행) |
| `thigh` `calf` `arm` | 3 | `hist_gradient_boosting_circumference` | 같은 직접측정 계열 (학습 178행) |
| `thigh_length` `calf_length` `torso_length` `leg_length` `neck_length` | 5 | `hist_gradient_boosting_exact_lengths_v2` | SizeKorea 8차 **3D 측정** (정제 4,485행) |
| `thigh_calf_ratio` `torso_leg_ratio` | 2 | 모델 없음 — 서버 후처리 나눗셈 | — |

**세 모델의 학습 모집단이 다르다는 사실을 문서에 못박아 뒀다.** 직접측정 계열(181명)과 3D 계열(4,485명)은 `subject_id` 교집합이 0명이다. 그래서 길이 오차(MAE 0.92~1.66 cm)와 둘레 오차(MAE 1.17~3.41 cm)를 **하나의 범위로 묶어 인용하면 안 된다** — "MAE 1~1.6cm"라고 말하는 순간 허리(3.406)에서 2배 이상 틀린 주장이 된다.

### 사진 경로를 왜 뒀는가 — 가장 안 맞는 부위가 가장 중요한 부위였다

무사진 모델의 오차는 **허리 3.406 · 가슴 3.043**으로 가장 컸다. 그런데 이 두 부위가 바로 상·하의 사이즈를 결정한다. 평균 성능은 나쁘지 않은데 **정작 쓸모 있는 부위에서 틀리는** 구조였고, 그래서 정면·측면 전신 사진으로 보정하는 경로를 추가했다.

**VLM 후보 4종을 같은 프롬프트·같은 입력으로 비교했다** (validation 39명).

| 모델 | 평균 MAE (cm) | 평균 시간(초) | 호출당 비용(USD) |
|---|---:|---:|---:|
| **Kimi K2.5** (채택) | **2.757** | 7.653 | 0.004492 |
| Grok 4.3 | 3.441 | 1.747 | 0.005977 |
| Qwen 3.7 Flash | 3.597 | 5.106 | 0.000152 |
| Gemini 3.5 Flash-Lite | 3.962 | 5.985 | 미기록 |

정확도 1위인 Kimi를 채택하되, **Qwen은 0.84 cm 나쁜 대신 약 30배 저렴하다**는 사실을 함께 남겼다. 서비스 규모가 커지면 전환하는 것이 합리적이고, 그 판단을 나중 사람이 다시 실험하지 않고도 할 수 있어야 한다고 봤다.

### 사진 vs 무사진 A/B — 집계 기준에 따라 결론이 뒤집힌다

테스트 143명, 부위별 MAE(cm)로 두 경로를 직접 붙였다.

| 집계 기준 | 무사진 | 사진 | 승자 |
|---|---:|---:|---|
| 3개 (가슴 · 허리 · 엉덩이) | 3.616 | **3.126** | 사진 |
| 7개 전체 | **2.474** | 2.822 | 무사진 |

사진은 **가슴(−1.222) · 허리(−0.930)에서만 확실히 낫고**, 나머지 5개 부위는 무사진이 낫다(특히 어깨는 사진이 2.17 cm 더 나쁘다). 즉 **"사진 경로가 더 정확하다"는 문장은 어느 기준으로 집계했는지를 밝히지 않으면 거짓이 된다.** 그래서 성능을 인용할 때 3개 기준인지 7개 기준인지를 반드시 함께 적도록 문서 규칙으로 만들었다.

### 상용 VLM은 `temperature=0`으로도 재현되지 않는다

같은 사진·같은 입력으로 다른 날 호출한 결과를 대조했더니, 7개 부위 중 일치한 것이 **0/7, 2/7**이었다. 상용 API는 내부 배치·라우팅 때문에 완전 재현이 되지 않는다. 그래서 **단건 결과로 모델 성능을 판단하지 않고, 벤치마크는 응답을 CSV로 남겨 대조한다**는 규칙을 세웠다.

### 그 외 구현

- 추정 API 2종 — `POST /users/me/body/estimate/`(무사진) · `POST /users/me/body/photos/`(사진, 202 + 폴링)
- 사진 품질 검증(다인 검출 · 프레이밍 · 화질)과 실패 시 fallback 라벨링
- 실패 사유를 남기는 `error_code` / `error_message`, 사용자당 진행 중 1건 제약, 10분 초과 건 자동 만료
- **사진을 디스크·DB에 저장하지 않는다.** 요청에서 바이트로 읽어 추론에만 쓰고 버린다.
- 기본 모델로 14개를 먼저 채운 뒤 VLM 결과로 덮어쓰는 2단 구조 — VLM이 일부 부위를 빠뜨려도 응답에 빈칸이 생기지 않는다.

> **기여 경계** — 신체치수 **입력** API와 사진 업로드 비동기 구조는 팀원이 먼저 만들어 뒀고, 그 시점에는 추정 결과 자리에 고정값이 저장되고 있었다. 내가 한 것은 **그 껍데기 위의 추정 로직 전체**(`body_inference.py`에 321줄 추가·71줄 삭제, 최종 338줄)와 ML 파이프라인(`ml/body_measurement/`)이다. "신체치수 추정 기능을 만들었다"는 맞고, "처음부터 끝까지 혼자 만들었다"는 아니다.

<br/>

## 2️⃣ 공유 옷장 — 설계부터 추천 연동, 모바일 화면까지

옷장은 원래 1인 소유 모델이었다. 여기에 **최대 6명이 한 방에서 옷을 공유하고, 다른 멤버의 옷도 추천 후보에 들어가는** 구조를 얹었다. 도메인 설계 · 백엔드 API · 추천 검색 연동 · 모바일 UI를 나눠 맡지 않고 한 사람이 관통했다.

**백엔드**

- 공유방 · 멤버 · 아이템 API, **24시간 초대 코드**, 방장 위임, 탈퇴/삭제 정책
- 공유 예약을 기기 local state에서 **DB 예약 구조로 이전** — 아이템 업로드 job이 확정될 때까지 예약이 유지된다
- 한 옷을 **여러 방에 동시에** 공유 가능하도록 개선
- 방 참여 시 `select_for_update` 행 잠금으로 동시 입장 경합 방어 (정원 초과·중복 참여 차단)
- UUID 형식 오류 · 방 이름 길이 · 권한 관련 4xx 처리 보강, 단위 테스트 추가

**추천 연동**

- 내 옷 + 참여 중인 방의 옷을 합쳐 **접근 가능한 아이템 ID를 DB에서 계산**하고, 이를 Qdrant `HasIdCondition` 화이트리스트로 넘긴다
- ID 목록이 무한정 커지지 않도록 `RETRIEVER_WARDROBE_ID_CAP` 설정 추가
- 공유 옷장 아이템이 채팅 추천 후보로 들어가도록 리트리버까지 연결, retriever 테스트 추가

**모바일 (React Native)**

- 공유 옷장 온보딩 · 초대 미리보기 · 참여 flow를 백엔드 API와 연결
- 아이템 등록/상세 화면의 다중 방 선택 UI (4×2 그리드), PC 웹 2행 · 모바일 한 줄 반응형 분기
- 멤버 파스텔 색상 자동 배정, 소유자 배지, 공유 카드 인라인 해제
- 채팅 입력바에 옷장 아이템 선택 시트 연결, **옷장 카드를 채팅 드롭존으로 드래그**하는 UX 구현

> 화면과 API를 나눠 맡지 않았기 때문에, 요청·응답 계약이 어긋나는 지점을 화면에서 바로 확인하고 고칠 수 있었다.

<br/>

## 3️⃣ 골든셋 — "좋은 코디 모음"이 아니라 "점수의 근거"로

골든셋 체형·색상 판단 규칙 데이터셋을 구축하고, 이를 추천 파이프라인의 **가중치 레이어**로 연결했다.

핵심 판단은 **골든셋을 정답으로 쓰지 않은 것**이다. 골든셋을 그대로 정답처럼 쓰면 수집한 사람의 취향이 사용자에게 강요된다. 그래서 우선순위를 이렇게 뒀다.

| 순위 | 신호 | 처리 |
|---|---|---|
| 1 | 사용자가 **싫다고 한** 조건 | 강한 감점 — 골든셋보다 우선 |
| 2 | 사용자 선호 · 추구미 | 가점 |
| 3 | 골든셋 체형·색상 규칙 | 랭킹 **앞단의 보정 가중치** |

<br/>

## 4️⃣ 그 외 기여

- **추구미(pursuit)** — 옵션·조회·저장 API 구현과 모바일 화면 연결. 사용자가 긴 설명을 쓰지 않아도 추천 조건이 쌓이게 하는 장치다.
- **착장 캘린더** — 하의·신발·원피스/세트·모자 등 단일 슬롯 카테고리 중복 방지. 수동 등록과 AI 룩북 자동 등록 **두 경로 모두**에 검증을 적용하고 `DuplicateCategorySlotError` / 400 응답과 단위 테스트를 추가했다.
- **팀 인프라** — Infisical 시크릿 온보딩 가이드([`docs/infisical-guide.md`](docs/infisical-guide.md)) 작성. compose가 값을 찾는 두 경로(`env_file` vs `${VAR}` 보간)를 문서화해, 팀에서 반복되던 기동 실패를 정리했다.
- **실사용 버그** — iPhone portrait 사진 EXIF 회전 허용, 가방 개수 표기 오류, 공유 옷장 타일 텍스트 잘림 등.

<br/>

<div align="right"><a href="#tableContents">목차로 이동</a></div>

---

<a name="tableContents"></a>

## 🔎 목차

1. <a href="#mywork">🙋 내가 한 일</a>
1. <a href="#why">🎯 왜 만들었나</a>
1. <a href="#features">⭐️ 핵심 기능</a>
1. <a href="#architecture">⚙️ 시스템 아키텍처</a>
1. <a href="#pipeline">🧠 AI 파이프라인</a>
1. <a href="#evaluation">📊 모델 선정과 평가</a>
1. <a href="#data">🗂 데이터</a>
1. <a href="#stack">🛠️ 기술 스택</a>
1. <a href="#structure">📁 저장소 구조</a>
1. <a href="#quickstart">🚀 빠른 시작</a>
1. <a href="#api">🔌 주요 API</a>
1. <a href="#team">👥 팀원 소개</a>

<br/>

<!------- 왜 만들었나 -------->

<a name="why"></a>

## 🎯 왜 만들었나

옷은 이미 충분히 많은데 아침마다 입을 게 없다. 기존 패션 서비스는 **팔 물건**을 기준으로 추천하기 때문에, 추천을 받아도 결국 새로 사야 실행할 수 있다.

이 서비스는 출발점을 뒤집었다. **사용자가 이미 가진 옷**을 디지털로 등록하고, 그 옷장을 후보 풀로 삼아 오늘의 날씨·TPO·체형·추구미에 맞는 코디를 추천한다. 추천이 곧바로 실행 가능하고, 새 옷 추천도 "지금 옷장과 어떻게 조합되는지"를 근거로 제시하므로 충동구매 대신 활용도 높은 구매로 이어진다.

| | 기존 패션 추천 | COZY |
|---|---|---|
| 추천 대상 | 판매 재고 | **보유 옷장 + 판매 상품** |
| 실행 가능성 | 구매해야 실행 | **오늘 바로 입을 수 있음** |
| 근거 | 인기순 · 협업필터링 | **날씨 · 체형 · TPO · 조합 사례 기반 RAG 설명** |
| 개인화 축 | 구매 이력 | **옷장 · 신체치수 · 추구미 · 착장 기록** |

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 핵심 기능 -------->

<a name="features"></a>

## ⭐️ 핵심 기능

### 1. 옷장 — 사진 한 장으로 등록

전신 사진이나 옷 사진을 올리면 AI가 사진 속 아이템을 **각각 분리해 상품컷처럼 만들고**, 카테고리·색상·소재·핏·계절·스타일을 태깅해 옷장에 넣는다. 여러 장 일괄 등록, 개인 해시태그, 정렬 취향 저장, 벡터 재색인까지 지원한다.

### 2. 오늘의 룩 — 요청하지 않아도 준비되는 코디

홈에 들어오면 그날 위치의 날씨를 반영한 코디가 이미 만들어져 있다. 사용자 입력이 없는 기능이라 **조회가 곧 생성 트리거**이며, 생성 중·완성·프로필 부족 세 가지 상태를 홈 첫 프레임부터 구분해 보여준다. 최근 5일 착장은 반복 추천에서 제외하고, '다른 룩' 후보도 함께 만든다.

### 3. AI 스타일리스트 채팅

자연어로 물어보면 옷장·상품·지식 컬렉션을 함께 검색해 코디 카드로 답한다. 사진을 첨부해 무드를 잡을 수도 있고, 결과는 Redis Stream **SSE로 실시간 스트리밍**된다. 로그인 전 게스트로 먼저 써 보고 가입 시 대화를 그대로 승계할 수 있다.

**스타일리스트 모드**를 켜면 성향이 다른 스타일리스트 3인(**미니멀 · 실용형 · 실험형**)이 동시에 제안하고, 중복은 병합해 한 화면에 나란히 보여준다. 마음에 안 드는 제안은 그 페르소나만 골라 다시 받을 수 있다. 셋 중 LLM으로 가설을 만드는 건 experimental 하나뿐이고 나머지 둘은 결정적 스코어링 함수라, 같은 입력이면 같은 답이 나온다.

### 4. 코디 평가

입은 코디 사진을 올리면 **8개 축**(색조화 · 실루엣 · TPO · 계절 · 소재/패턴 · 스타일 일관성 · 완성도 · 착용자 적합성)으로 0~100점과 축별 코멘트를 낸다. 기온·상황·사진이 없어도 평가가 성립하도록, 근거가 없는 축은 끄고 **가중치를 재정규화**한다.

### 5. 신체치수 추정 & 사이즈 · 공유 옷장 🙋

키·몸무게·성별만으로 14개 치수·비율을 추정하고, 정면·측면 전신 사진을 주면 VLM이 길이감을 보정한다. 최대 6명이 방을 만들어 `confirmed` 아이템만 공유하고, 다른 멤버의 옷도 추천 후보에 포함시킨다. → <a href="#mywork">내가 구현한 부분</a>

### 6. 룩북 · 캘린더 · 가상 착장

- **룩북** — 마음에 든 룩을 저장하고 공개 피드로 탐색. 사진 속 '이미 입은 부위'는 옷장 재등록에서 제외한다.
- **캘린더** — 하루 한 건의 착장 기록. 옷 빼기는 삭제가 아니라 연결 해제라 원본 옷장 아이템은 보존된다.
- **가상 착장 · 렌더** — 추천 카드의 착장 이미지를 생성 모델로 만들어 붙인다.
- **찜 · 예산** — 판매 상품 찜 목록과 예산 설정.

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 아키텍처 -------->

<a name="architecture"></a>

## ⚙️ 시스템 아키텍처

<img src="docs/service-architecture.svg" width="100%">

클라이언트 — 백엔드 — AI 워커 — 데이터의 4계층이다. **API 서버는 무거운 AI 작업을 직접 하지 않는다.** 모든 생성·분석 작업은 Redis 신뢰성 큐(`BLMOVE pending→processing`, ack 시에만 제거, 재시도 초과 시 dead 큐)에 넣고 202로 응답하며, 워커가 처리한 뒤 내부 콜백 또는 SSE로 결과를 돌려준다.

### 실행 단위

| 계층 | 서비스 | 역할 |
|---|---|---|
| 클라이언트 | `mobile/` | Expo Router 기반 RN 앱 (iOS · Android · Web) |
| API | `api` | Django/DRF · JWT · 스키마 소유 · 작업 큐잉 |
| CPU 워커 | `chat-worker` | 채팅 오케스트레이션 → 추천 파이프라인 → SSE publish |
| | `outfit-worker` | 코디 평가 (룰 계산 + VLM 루브릭) |
| | `daily-look-worker` | 오늘의 룩 생성 · 대안 후보 |
| | `outfit-render-worker` | 코디 카드 이미지 · 가상 착장 렌더 |
| 수집기 | `weather-collector` | 기상청 APIHub 실황 · 단기 · 중기 예보 |
| | `naver-collector` / `eleven-collector` | 쇼핑 상품 수집 + LLM 태깅 |
| GPU 워커 | `image-processor` | 옷장 사진 → 아이템 열거 · 상품컷 생성 · 태깅 · 임베딩 |
| | `wardrobe-item-tagger` | 단일 아이템 사진 일괄 등록 전용 태깅 (Qwen3-VL) |
| | `wardrobe-reindex-worker` | 기존 크롭·태그 → 임베딩 재생성 |
| | `product-indexer` | 상품 이미지·텍스트 임베딩 → Qdrant 적재 |
| | `text-embedding-api` | 채팅 질의문 → BGE-M3 벡터 HTTP API |
| | `golden-set` | 골든 코디 사진 → 판단 지식 · 보조 점수 앵커 배치 |
| 데이터 | PostgreSQL 16 · Qdrant · Redis · S3 | 원본 · 벡터 · 큐/캐시 · 이미지 |

### 벡터 컬렉션

| 컬렉션 | 내용 | 벡터 |
|---|---|---|
| `wardrobe_items` | 사용자 옷장 아이템 | image 768 + text 1024 |
| `products_naver_v1` / `products_eleven_v1` | 판매 상품 카탈로그 | image 768 + text 1024 |
| `outfit_goldenset` | 검수된 코디(룩) 사례 | image 768 + text 1024 |
| `goldenset_items` | 골든 코디에서 분리한 아이템 | image 768 + text 1024 |
| `knowledge` | 스타일링 지식 청크 | text 1024 |

이미지와 텍스트를 한 모델로 합치지 않은 이유: FashionSigLIP의 텍스트 타워는 영어 중심이라 **한국어 채팅 질의·한국어 태그 문장에 부적합**하다. Qdrant named vector로 한 포인트에 두 벡터를 붙여 저장 구조는 단순하게 유지했다.

### 데이터 모델

**스키마 소유권**: collector가 쓰는 테이블을 포함해 **모든 DDL은 Django migration(`api/apps/*`)이 관리한다.** collector는 raw SQL upsert만 수행하며 실행 전 `migrate`가 선행되어야 한다. 모든 테이블·컬럼에는 한글 `db_table_comment` / `db_comment`가 달려 있어 DB 툴에서 스키마만 봐도 의미가 읽힌다.

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- AI 파이프라인 -------->

<a name="pipeline"></a>

## 🧠 AI 파이프라인

### 옷장 등록 (image-processor)

```
사진 → ① 열거(Vision structured output) → ② 상품컷 생성(이미지 편집 모델)
     → ③ 태깅(taxonomy enum 강제 + 짝 보정) → ④ 임베딩(FashionSigLIP 768 / BGE-M3 1024)
     → S3 크롭·manifest 업로드 → API 콜백 → DB + Qdrant
```

- 컴포넌트는 **전략 패턴**이다. `pipeline/base.py` 인터페이스를 구현하고 `_REGISTRY`에 등록한 뒤 `WORKER_PIPELINE` 환경변수로 교체한다 (`gemini-edit` ↔ `qwen-tag` ↔ 예정 `sam3-crop`).
- 멱등: 같은 `job_id`는 같은 S3 경로를 재사용하고, `manifest.json`이 이미 있으면 이미지 처리를 건너뛰고 콜백만 재시도한다.
- 아이템 단위 부분 실패를 허용한다. 워커는 DB를 직접 건드리지 않는다.

### 추천 리트리버

세 기능(오늘의 룩 · 추구미 추천 · 옷장 추천)이 **하나의 리트리버**를 공유하고, 기능별로는 얇은 어댑터만 둔다.

```
필터 빌더 → 메타데이터 하드 필터 → 벡터 유사도 랭킹 → 슬롯별 후보 → 룰 검증 → LLM 설명 생성
```

설계 원칙은 **"날씨 적합성과 체형 적합성은 벡터로 풀지 않는다"** 이다. 한겨울 린넨 반팔처럼 위반 시 곧바로 틀리는 하드 조건이나 퍼센타일 같은 정량 규칙은 payload 필터와 코드 상수(`api/apps/recommend/rules/*.json`)로 처리하고, 벡터가 담당하는 건 "무드가 비슷한 룩 찾기"뿐이다.

공유 옷장 사용 시에는 접근 가능한 아이템 ID를 DB에서 계산해 Qdrant `HasIdCondition` 화이트리스트로 넘긴다. 🙋

### 코디 평가

```
L0 컨텍스트 정규화 + 근거 가용성 판정
L1 결정론적 사실 계산기 (clo 지수, 격식 레벨 ℓ, 색상 거리 …)  ← 점수가 아니라 '사실'
L2 골든셋 RAG (필터 사다리)
L3 VLM 루브릭 (사실을 프롬프트에 주입)
L4 재정규화 · 캘리브레이션
```

VLM은 clo 계산도 한국 조문 규범도 모르기 때문에 L1이 사실을 먼저 계산해 넣는다. 꺼진 축은 0점으로도 만점으로도 두지 않고 가중평균에서 제외한다 — 전자는 정보를 덜 준 사용자를 처벌하고, 후자는 정보를 숨길 인센티브가 되기 때문이다. 활성 가중치 합이 0.5 미만이면 점수 대신 `INSUFFICIENT`를 반환한다.

### 신체치수 🙋

무사진 경로는 HistGradientBoosting 모델 **3개를 순서대로 호출해 합쳐** 14개 항목을 만든다(코어 둘레 4 + 부가 둘레 3 + 길이 5, 비율 2는 서버 후처리 나눗셈). 사진 경로는 기본 모델 값으로 먼저 채운 뒤, 정면·측면 전신 사진에서 VLM이 추정한 값으로 덮어쓴다.

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 모델 평가 -------->

<a name="evaluation"></a>

## 📊 모델 선정과 평가

### 옷장 아이템 분리·태깅 — 후보 비교

자체 학습 없이 **사전학습 모델 조합(제로샷)** 으로 간다는 방침 아래, 세그멘테이션 후보를 같은 조건에서 비교했다. SegFormer는 같은 클래스 옷 두 벌을 분리하지 못했고, YOLOv8+DeepFashion2는 신발·가방 클래스가 없어 탈락했다.

| 파이프라인 | 태깅 정확도 | 검출 재현율 |
|---|---:|---:|
| **SAM 3 + Gemini** | **93.6 %** | 78.0 % |
| SegFormer + Gemini | 84.3 % | 70.7 % |
| Grounded-SAM 2 + Gemini | 79.0 % | **87.8 %** |

> 사람 평가 · 사진 10장 / GT 41 아이템 (2026-07-24). **표본이 작아 순위의 유의성은 제한적**이며, 절대 수치보다 후보 간 상대 비교 용도로 인용한다. 현재 운영 파이프라인은 `gemini-edit`이고, `sam3-crop`은 인터페이스만 열어 둔 예정 구현이다.

### 신체치수 추정 — 서빙 모델 지표 🙋

전부 shuffled 5-fold CV. **세 모델의 학습 모집단이 다르므로 하나의 범위로 묶어 인용하면 안 된다.**

<table>
<tr><td valign="top">

**코어 둘레 4개** · 172행

| 항목 | MAE (cm) | R² |
|---|---:|---:|
| shoulder | 1.541 | 0.593 |
| hip | 2.306 | 0.726 |
| chest | 3.043 | 0.727 |
| waist | 3.406 | 0.761 |

</td><td valign="top">

**부가 둘레 3개** · 178행

| 항목 | MAE (cm) | R² |
|---|---:|---:|
| arm | 1.170 | 0.775 |
| calf | 1.183 | 0.619 |
| thigh | 2.223 | 0.645 |

</td><td valign="top">

**길이 5개 + 비율 2개** · 4,485행

| 항목 | MAE | R² |
|---|---:|---:|
| neck_length | 0.917 cm | 0.256 |
| calf_length | 1.065 cm | 0.776 |
| thigh_length | 1.396 cm | 0.405 |
| leg_length | 1.537 cm | 0.857 |
| torso_length | 1.660 cm | 0.439 |
| thigh_calf_ratio | 0.046 | 0.292 |
| torso_leg_ratio | 0.028 | 0.020 |

</td></tr>
</table>

**해석 시 전제**

1. 길이(0.92~1.66 cm)와 둘레(1.17~3.41 cm)를 한 범위로 묶지 않는다.
2. 오차가 가장 큰 허리(3.406)·가슴(3.043)이 바로 상·하의 사이즈를 결정하는 부위다. **사진(VLM) 경로를 둔 이유가 여기 있다.**
3. `neck_length`는 키·몸무게로 예측되지 않는다(R² 0.256). 실제 값은 사진 경로로만 얻는다.
4. `torso_leg_ratio`는 MAE가 작지만 R² 0.020으로 사실상 집단 평균이다. 사진 측정값·사용자 수정값을 우선한다.

VLM 후보 4종 비교와 사진/무사진 A/B 결과는 <a href="#mywork">내가 한 일 §1</a>과 [`docs/body-measurement-api-design.md`](docs/body-measurement-api-design.md)에 있다.

### 코디 평가 검증 기준

골든셋은 AI Hub 선호도 데이터 + 팀 라벨 300건으로 구성했고, 합격선은 **전체 축 Spearman ρ ≥ 0.65 / 최소 근거 모드 ρ ≥ 0.55 / 모드 간 평균 점수차 |Δ| ≤ 3점**이다.

설계를 강제한 선행 연구:

- 고전 색채 조화(보색·삼각)는 선호 예측력이 없다 (O'Donovan, SIGGRAPH 2011; Schloss & Palmer 2011) → 감점 필터로만 쓰고 가점은 금지.
- 황금분할·7:3 실측 반박 (FCSRJ 2007, 수평분할 1위는 50/50) → 5:5 감점 금지.
- VLM 단독 전신사진 평가는 Spearman 0.117~0.519, position bias 30~74 % (ZOZO, SIGGRAPH Asia 2024) → 스왑 필터·앙상블·캘리브레이션 필수.
- 소셜 '좋아요' 라벨은 얼굴·팔로워 수를 학습한다 (Fashion144k) → **얼굴 마스킹 필수**.
- 유일한 하드 근거 축은 계절(ISO 9920 clo). 시중 '기온별 옷차림표'는 기상청 공식 자료가 아니다.

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 데이터 -------->

<a name="data"></a>

## 🗂 데이터

| 원천 | 용도 |
|---|---|
| AI Hub **K-Fashion** (jpg 32.8만 / 라벨 90.4만) | 룩 조합 사례 벡터 검색, 스타일 공출현 통계 |
| AI Hub **의류 통합 데이터** (jpg 49.6만, 333.6 GB) | 기온↔소재·복종 매핑의 정량 근거, 체형별 착장 사례 |
| **사이즈코리아** 5~8차 인체치수조사 | 신체치수 모델 학습, 체형 분류 상수 🙋 |
| AI Hub **연도별 패션 선호도** | 코디 평가 골든셋 선호 라벨 |
| ETRI **패션 코디 데이터셋** | 스타일리스트 few-shot, 평가 골든셋 |
| Polyvore Outfits (CC-BY 4.0) | 조합 호환성 스코어러 |
| 네이버 쇼핑 · 11번가 Open API | 판매 상품 카탈로그 (LLM 태깅 후 임베딩) |
| 기상청 APIHub | 실황 · 단기 · 중기 예보 |

S3 버킷 실측(2026-07-13): **총 6,405,835 객체 / 1.58 TB**. 이 중 약 70 %는 RAG 비대상인 신체 데이터로, 실제 RAG 파이프라인이 읽는 범위는 훨씬 작다.

**라이선스 격리 원칙** — AI Hub 데이터는 학습용 한정·재배포 금지·국외 반출 제한이므로 리전을 `ap-northeast-2`로 고정하고, 원본 이미지가 서비스에 그대로 노출되는 경로는 두지 않는다. 무신사·오늘의집 등 robots.txt 전면 차단 사이트는 수집 대상에서 제외한다.

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 기술 스택 -------->

<a name="stack"></a>

## 🛠️ 기술 스택

### 모바일

![React Native](https://img.shields.io/badge/React%20Native-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![Expo](https://img.shields.io/badge/Expo-000020?style=for-the-badge&logo=expo&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)

### 백엔드

![Python](https://img.shields.io/badge/Python%203.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django%205.2-092E20?style=for-the-badge&logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-A30000?style=for-the-badge&logo=django&logoColor=white)
![JWT](https://img.shields.io/badge/JWT-000000?style=for-the-badge&logo=jsonwebtokens&logoColor=white)

### 데이터 · AI

![PostgreSQL](https://img.shields.io/badge/PostgreSQL%2016-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Qdrant](https://img.shields.io/badge/Qdrant%20v1.17-DC244C?style=for-the-badge)
![Redis](https://img.shields.io/badge/Redis%207-DC382D?style=for-the-badge&logo=redis&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=for-the-badge&logo=scikitlearn&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)

### 인프라

![Docker](https://img.shields.io/badge/Docker%20Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![AWS](https://img.shields.io/badge/AWS%20EC2%20·%20RDS%20·%20S3-232F3E?style=for-the-badge&logo=amazonwebservices&logoColor=white)
![Infisical](https://img.shields.io/badge/Infisical-EBEBEB?style=for-the-badge&logo=infisical&logoColor=black)

| 영역 | 사용 기술 |
|---|---|
| 모바일 | React Native 0.86 · Expo 57 · Expo Router · TypeScript · React 19 |
| 백엔드 | Python 3.11 · Django 5.2 · DRF · simplejwt · drf-spectacular · gunicorn |
| 데이터 | PostgreSQL 16 · Qdrant v1.17 · Redis 7 · AWS S3 |
| 임베딩 | Marqo-FashionSigLIP (768d, 이미지) · BAAI/bge-m3 (1024d, 한국어 텍스트) |
| 생성 모델 | Gemini (열거 · 이미지 편집 · 태깅 · 평가) · GPT (채팅 오케스트레이션 · 상품 태깅) · Qwen3-VL (일괄 태깅) · Kimi K2.5 (신체치수) |
| ML | scikit-learn (HistGradientBoosting) · PyTorch · SAM 3 / SegFormer / Grounded-SAM 2 (후보 비교) |
| 인프라 | Docker Compose · AWS (EC2 · RDS · S3) · RunPod GPU · Infisical (시크릿) |
| 인증 | JWT · OAuth (네이버 · 카카오 · 구글 · 애플) · 이메일 인증 |

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 저장소 구조 -------->

<a name="structure"></a>

## 📁 저장소 구조

`🙋` 표시는 내가 주로 작업한 영역이다.

```
fashion-recommend-coordi/
├── docker-compose.yml          # 통합 스택 (profiles: db/api/weather/naver/eleven/all)
├── docker-compose.gpu.yml      # GPU 서버 전용 (임베딩·이미지 워커·골든셋)
├── .env.example                # 환경변수 템플릿 — 루트 .env 하나로 전체 관리
├── api/                        # Django REST API 서버
│   ├── config/settings/        #   base / dev / prod / swagger
│   └── apps/
│       ├── users/              #   인증 · 프로필 · 신체치수 🙋 · 추구미 🙋 · 예산
│       ├── home/               #   홈 대시보드 (날씨 + 오늘의 룩 상태)
│       ├── wardrobe/           #   옷장 · 일괄등록 · 해시태그 · 공유 옷장 🙋
│       ├── recommend/          #   추천 · 오늘의 룩 · 코디 평가 · 렌더 · 찜
│       ├── chat/               #   AI 스타일리스트 채팅 (멀티에이전트 · SSE)
│       ├── lookbook/           #   룩북 · 공개 피드
│       ├── style_calendar/     #   착장 캘린더 🙋
│       ├── catalog/ weather/   #   상품 · 날씨 스키마 (collector가 사용)
│       └── goldenset/          #   골든셋 run 임포트
├── collector/                  # 독립 실행 수집기 (naver · eleven · weather)
├── image-processor/            # 옷장 이미지 GPU 워커 (전략 패턴 파이프라인)
├── indexer/                    # 상품 임베딩 GPU 워커
├── ml/                         # 모델 실험 · 학습 · 골든셋 파이프라인
│   ├── body_measurement/       #   신체치수 추정 (학습 · 평가 · 서빙 아티팩트) 🙋
│   └── golden_set/             #   골든 코디 → 판단 지식 배치
├── mobile/                     # React Native (Expo) 앱
├── golden-set/                 # 골든셋 라벨링 작업물 (체형 🙋 · 색상 🙋 · 문서)
├── shared-wardrobe/            # 공유 옷장 설계 명세 · 인수인계 노트 🙋
├── scripts/                    # 배포 · 데이터 처리 스크립트
├── docs/                       # 설계 · 아키텍처
└── test/                       # 후보 모델 비교 하네스
```

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 빠른 시작 -------->

<a name="quickstart"></a>

## 🚀 빠른 시작

### Docker

```bash
infisical login                                    # 최초 1회, US(app.infisical.com) 리전
infisical export --env=dev --output-file=./.env    # Infisical → 루트 .env 생성

docker compose --profile api up -d --build      # db · qdrant · migrate · api · 워커 4종 · 날씨 수집기
docker compose --profile naver up -d --build    # 네이버 상품 수집기
docker compose --profile eleven up -d --build   # 11번가 상품 수집기
docker compose --profile all up -d --build      # 전부
```

- 실행 순서는 자동 보장된다: `db(healthy) → migrate → qdrant-init → api/워커`
- `redis`는 프로필이 없어 어떤 스택을 켜도 항상 함께 뜬다. `db`도 모든 프로필에 포함된다.
- `.env`에 `COMPOSE_PROFILES=api,naver`를 넣으면 `docker compose up -d`만으로 동작한다.
- API 실행 모드는 `DJANGO_SETTINGS_MODULE`로 고른다 — `config.settings.prod`(기본) / `dev` / `swagger`(+ Swagger UI `/api/docs/`).
- 헬스체크: `/health/live/` · `/health/ready/`

<details>
<summary><b>compose가 값을 찾는 두 경로 (자주 막히는 곳)</b></summary>

- **`env_file: .env`** — 컨테이너 안으로 파일째 주입된다. **파일에서만** 읽으므로 셸 환경변수는 무시된다.
- **`${VAR}` 보간** — compose 파일 파싱 시 **셸 환경변수를 먼저** 보고 없으면 `.env`를 본다. `infisical run`으로 감싸면 그대로 주입된다. `REDIS_PASSWORD`, `OPENROUTER_API_KEY`, `DJANGO_SETTINGS_MODULE` 등 9개가 이 방식이다.
- 그래서 `REDIS_PASSWORD`가 `.env`에 없으면 `required variable REDIS_PASSWORD is missing a value`로 **기동 자체가 실패**한다. `infisical run --env=dev -- docker compose ...`로 감싸면 해결된다.
- `.env`는 `infisical export`로만 생성·갱신하고 손으로 편집하지 않는다. **커밋 금지.**

원리는 [`docs/infisical-guide.md`](docs/infisical-guide.md) 참고.
</details>

### GPU 서버 (RunPod / GPU EC2)

db·qdrant·redis·api는 AWS 스택에 있다고 가정하고, GPU가 필요한 워커만 별도 compose로 띄운다.

```bash
./run-gpu.sh                                          # infisical export + 빌드 + 기동
./run-gpu.sh product-indexer                          # 특정 서비스만
docker compose -f docker-compose.gpu.yml logs -f
```

여러 서비스가 HF 모델 캐시 볼륨(`hf_cache`)을 공유해 FashionSigLIP·bge-m3를 한 번만 받는다.

### 모바일 앱

```bash
cd mobile
npm ci            # node_modules/package.json 불일치가 잦아 install이 아니라 ci
npx expo start    # Node 22 LTS 필요
```

### 로컬 개발 · 테스트

```bash
docker compose --profile db up -d          # DB만 컨테이너로

cd api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py init_qdrant               # Qdrant 컬렉션 생성
python manage.py runserver

python manage.py test apps                 # 전체 테스트
python manage.py test apps.recommend       # 앱 단위
```

현재 `api/apps` 아래 테스트 파일 92개 · 테스트 케이스 **1,275개**가 있다.

<details>
<summary><b>워커 단독 실행 · 진단용 커맨드</b></summary>

```bash
python manage.py run_chat_worker
python manage.py run_outfit_worker
python manage.py run_daily_look_worker
python manage.py run_outfit_render_worker
```

```bash
python manage.py check_daily_look --user-id <id>  # 오늘의 룩 생성 경로 점검
python manage.py check_chat_recommend             # 채팅 추천 파이프라인 점검
python manage.py audit_wardrobe_vectors           # 옷장 벡터 정합성 감사
python manage.py reindex_wardrobe_vectors         # 옷장 벡터 재색인 큐잉
python manage.py sweep_stale_analyses             # 고착된 코디 평가 정리
```
</details>

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- API -------->

<a name="api"></a>

## 🔌 주요 API

전체 스펙은 Swagger UI(`/api/docs/`)에서 확인한다. `DJANGO_SETTINGS_MODULE=config.settings.swagger`로 띄워야 노출된다.

<details>
<summary><b>인증 · 사용자 · 신체치수</b> 🙋</summary>

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/v1/auth/signup/` · `/auth/login/` | 이메일 가입 · 로그인 |
| POST | `/api/v1/auth/email/verify/` · `/resend/` | 이메일 인증 (인증 시 토큰 미발급 — 탈취 차단) |
| POST | `/api/v1/auth/{naver\|kakao\|google\|apple}/login/` | 소셜 로그인 → JWT |
| POST | `/api/v1/auth/token/refresh/` | refresh → 새 access |
| GET/PATCH | `/api/v1/users/me/` | 내 정보 |
| GET/PUT | `/api/v1/users/me/body/` `body/basic/` `body/detail/` | 신체치수 🙋 |
| POST | `/api/v1/users/me/body/estimate/` | 사진 없이 치수 추정 🙋 |
| POST/GET | `/api/v1/users/me/body/photos/` `photos/{id}/` | 사진 기반 추정 요청 · 폴링 🙋 |
| GET/PUT | `/api/v1/users/me/pursuit/` `budget/` | 추구미 🙋 · 예산 |
</details>

<details>
<summary><b>옷장 · 공유 옷장</b> 🙋</summary>

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST/GET | `/api/v1/wardrobe/uploads/` `uploads/{job_id}/` | 사진 업로드(비동기) · 진행 조회 |
| POST/GET | `/api/v1/wardrobe/batches/` `batches/{id}/` | 일괄 등록 |
| GET | `/api/v1/wardrobe/items/` | 옷장 목록 |
| GET/PATCH/DELETE | `/api/v1/wardrobe/items/{id}/` | 아이템 상세 |
| GET/POST/PATCH/DELETE | `/api/v1/wardrobe/hashtags/…` | 개인 해시태그 · 정렬 |
| — | `/api/v1/shared-wardrobes/…` | 공유방 생성 · 초대 · 참여 · 아이템 🙋 |
| POST | `/api/v1/internal/wardrobe/callback/` | 워커 콜백 (내부 토큰 인증) |
</details>

<details>
<summary><b>추천 · 오늘의 룩 · 코디 평가</b></summary>

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/home/` | 홈 (날씨 + 오늘의 룩 상태) |
| GET | `/api/v1/looks/today/` | 오늘의 룩 — **조회가 곧 생성 트리거** |
| POST | `/api/v1/looks/today/save/` | 룩북에 담기 |
| POST | `/api/v1/looks/{id}/virtual-try-on/` | 가상 착장 |
| GET | `/api/v1/recommendations/{result_id}/` | 추천 결과 · 카드 |
| POST | `/api/v1/recommendations/{r}/cards/{c}/feedback/` `save/` `render/` | 피드백 · 저장 · 렌더 |
| GET | `/api/v1/recommendations/render-jobs/{id}/events/` | 렌더 진행 SSE |
| POST/GET | `/api/v1/outfits/analyze/` `analyses/{id}/` | 코디 평가 요청 · 결과 |
| GET/POST/DELETE | `/api/v1/wishlist/…` | 찜 |
</details>

<details>
<summary><b>채팅 · 룩북 · 캘린더</b></summary>

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/chat/stylists/` | 스타일리스트 목록 |
| POST | `/api/v1/chat/guest/` `guest/claim/` | 게스트 발급 · 계정 승계 |
| GET/POST | `/api/v1/chat/sessions/` `sessions/{id}/messages/` | 세션 · 메시지 |
| POST | `/api/v1/chat/sessions/{id}/attachments/` | 사진 첨부 · 무드 분석 |
| GET | `/api/v1/chat/runs/{run_id}/events/` | 응답 스트리밍 (SSE) |
| GET/POST | `/api/v1/lookbooks/` `lookbooks/public/` `discover/` | 룩북 · 공개 피드 · 탐색 |
| GET/POST | `/api/v1/calendars/` `by-date/` `{id}/items/` | 착장 캘린더 🙋 |
</details>

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

<!------- 팀원 -------->

<a name="team"></a>

## 👥 팀원 소개

| 이름 | GitHub | 주요 영역 |
|---|---|---|
| 이건우 | [@rp1028](https://github.com/rp1028) | 모바일 앱 개발 — 화면 · 네비게이션 · 디자인 시스템 |
| 신혜지 | [@HyejiShin-20](https://github.com/HyejiShin-20) | 채팅 멀티에이전트 · 추천 파이프라인 |
| 박건우 | [@92shepherd](https://github.com/92shepherd) | API 아키텍처 · 인프라 · 배포 |
| **전하영** | [**@vosnuev**](https://github.com/vosnuev) | **신체치수 추정 ML · 공유 옷장 · 골든셋 · 모델 벤치마크** |
| 김민욱 | [@WHwi99](https://github.com/WHwi99) | 가상 착장(VTON) · VLM 벤치마크 · 이미지 워커 |
| 김지효 | [@jjeoe0317](https://github.com/jjeoe0317) | 룩북 · 캘린더 · 서비스 기획 · 시나리오 정립 |

<div align="right"><a href="#tableContents">목차로 이동</a></div>

<br/>

## 📚 문서

| 문서 | 내용 |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | 개발 규칙 단일 진실 공급원 (코딩 컨벤션 · Git · 배포) |
| [`docs/project-overview.md`](docs/project-overview.md) | 서비스 기획 · 기대효과 |
| [`docs/body-measurement-api-design.md`](docs/body-measurement-api-design.md) | 신체치수 API 설계 · VLM 벤치마크 · A/B 결과 🙋 |
| [`ml/README.md`](ml/README.md) | ML 파이프라인 개요 🙋 |
| [`docs/fashion-rag-embedding-pipeline_1.md`](docs/fashion-rag-embedding-pipeline_1.md) | 임베딩 인프라 (RunPod + S3 + EC2 Qdrant) |
| [`docs/fashion-rag-embedding-retriever_2.md`](docs/fashion-rag-embedding-retriever_2.md) | 임베딩 · 리트리버 공통 설계 |
| [`docs/fashion-rag-s3-datasets_3.md`](docs/fashion-rag-s3-datasets_3.md) | S3 데이터셋 실측 인벤토리 |
| [`docs/fashion-rag-today-look_4.md`](docs/fashion-rag-today-look_4.md) | 오늘의 룩 RAG 계획 |
| [`docs/lookbook-api-design.md`](docs/lookbook-api-design.md) | 룩북 API 설계 |
| [`docs/infisical-guide.md`](docs/infisical-guide.md) | 시크릿 관리 🙋 |
| [`api/README.md`](api/README.md) · [`image-processor/README.md`](image-processor/README.md) · [`indexer/README.md`](indexer/README.md) · [`mobile/README.md`](mobile/README.md) | 컴포넌트별 상세 |

<br/>

<p align="center">
  <sub>SK네트웍스 Family AI 캠프 28기 · 최종 프로젝트 1팀 · COZY</sub>
</p>
