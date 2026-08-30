# 프론티어 이미지 편집 모델 end-to-end 비교 테스트 (test-llm)

옷장 등록에서 SAM3 크롭 방식의 한계(머리카락·팔에 가려진 부분 복구 불가,
측면·대각선 착장의 정면화 불가)를 확인하기 위해, **분리 → 가림 복구 → 정면화**
전 과정을 프론티어 이미지 편집 모델에 위임하는 방식을 비교한다.

핵심 설계: 편집 모델에는 SAM3 크롭이 아니라 **원본 전체 사진**을 준다.
가려진 픽셀은 크롭 시점에 이미 소실되므로, 전체 사진의 문맥이 있어야
복원 근거가 생긴다.

## 테스트 대상 모델

| 키 (`--models` 값) | 모델 | API | 필요 키 |
|---|---|---|---|
| `gpt-image-2` | GPT Image 2 | OpenAI images.edit | `OPENAI_API_KEY` |
| `gemini-3-pro-image` | Gemini 3 Pro Image (Nano Banana Pro) | google-genai | `GEMINI_API_KEY` |
| `gemini-3.1-flash-image` | Gemini 3.1 Flash Image (Nano Banana 2) | google-genai | `GEMINI_API_KEY` |
| `seedream-5-0-pro` | Seedream 5.0 Pro Edit | BytePlus ModelArk | `ARK_API_KEY` |
| `qwen-image-edit-plus` | Qwen Image Edit Plus | DashScope (국제 리전) | `DASHSCOPE_API_KEY` |

키가 없는 모델은 자동으로 건너뛴다 (부분 실행 가능).

## 흐름

```
input/*.jpg|png
  │
  ① 아이템 열거 — Gemini 비전 structured output (이미지당 1회)
  │   · 사진 속 모든 패션 아이템 목록 + 가림 원인 + 촬영 각도
  │   · output/_enumeration/<이미지명>.json 에 캐시
  │   · 모델 5종이 같은 목록을 공유해야 공정 비교가 된다
  │
  ② 아이템별 편집 — 모델 순차 실행
  │   · 입력: 원본 전체 사진 + "이 아이템만 분리·가림 복구·정면
  │     흰 배경 상품 사진으로" 프롬프트 (common/prompts.py, 전 모델 동일)
  │
  ③ 저장 — output/<모델>/<이미지명>/item_XX_<대분류>.png + items.json
  │   · items.json: 아이템 메타 + latency + 사용 프롬프트 + 에러
  │
  ④ 집계 — output/summary.json / summary.md (모델별 성공률·평균 latency)
```

## 실행 (Docker, 권장)

```bash
cd test/test-llm
cp <테스트할 사진들> input/

# 저장소 루트 .env 에 키를 넣거나 셸에서 export
export GEMINI_API_KEY=...      # 열거 단계 필수 + Gemini 이미지 2종
export OPENAI_API_KEY=...      # gpt-image-2
export ARK_API_KEY=...         # seedream
export DASHSCOPE_API_KEY=...   # qwen

./run_docker.sh                             # input/ 전체 × 모든 모델
./run_docker.sh --models gpt-image-2,seedream-5-0-pro
NO_BUILD=1 ./run_docker.sh                  # 재빌드 생략
```

GPU가 필요 없다(전부 API 호출). `input/`, `output/`은 호스트 폴더가
컨테이너의 `/app/input`, `/app/output`에 볼륨 마운트된다.

## 로컬(비도커) 실행

```bash
pip install -r requirements.txt
python run_all.py
python run_all.py --models gemini-3.1-flash-image path/to/one.jpg
```

## 환경변수 (전체)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `GEMINI_ENUM_MODEL` | `gemini-3.5-flash` | 열거 단계 비전 모델 |
| `OPENAI_IMAGE_MODEL` | `gpt-image-2` | |
| `OPENAI_IMAGE_SIZE` | `auto` | |
| `GEMINI_PRO_IMAGE_MODEL` | `gemini-3-pro-image` | |
| `GEMINI_FLASH_IMAGE_MODEL` | `gemini-3.1-flash-image` | |
| `SEEDREAM_MODEL` | `seedream-5-0-pro` | |
| `SEEDREAM_BASE_URL` | `https://ark.ap-southeast.bytepluses.com/api/v3` | |
| `SEEDREAM_SIZE` | `2K` | |
| `QWEN_IMAGE_MODEL` | `qwen-image-edit-plus` | |
| `DASHSCOPE_BASE_URL` | `https://dashscope-intl.aliyuncs.com/api/v1` | 국제 리전 |
| `TEST_MODELS` | (전체) | `--models`와 동일, 쉼표 구분 |

## 평가 방법

- `output/<모델>/<이미지>/item_XX_*.png`를 육안 비교 (분리 정확도, 복구 품질,
  정면화, identity 보존).
- 자동 정합성 검증(권장): 결과 이미지를 기존 Gemini 태깅
  (`test-sam/sam3/test_sam3_gemini.py`의 스키마)에 넣어 원본 사진 태그와
  색·카테고리·패턴이 달라졌는지 확인 → identity drift(환각 복원) 검출.
- 반드시 포함할 테스트 케이스: 긴 머리로 가려진 상의, 팔짱 낀 착장,
  측면·대각선 촬영, 로고/프린트 티셔츠(텍스트 복원 검증), 소형 아이템(신발·가방).

## 비용 참고 (편집 1회 기준, 2026-07)

gpt-image-2 ~$0.28 / gemini-3-pro-image ~$0.14 / gemini-3.1-flash-image ~$0.08
/ seedream-5-0-pro ~$0.075(공식가) / qwen-image-edit-plus ~$0.03.
아이템 수 × 모델 수만큼 호출되므로 input/에 사진을 대량으로 넣지 말 것.
