# test-llm2: Gemini 3.1 Flash Image 편집 + Confluence 태그 프로퍼티

test-llm(5개 모델 비교)에서 **gemini-3.1-flash-image 경로만** 가져와,
편집 결과 이미지에 Confluence 「의류 상품 데이터 카테고리-태그 매핑 문서」
(pageId 14286849, v4) 기준의 태그 프로퍼티를 붙여 items.json으로 출력한다.

## 흐름

```
input/*.jpg|png
  │
  ① 아이템 열거 — Gemini 비전 structured output (이미지당 1회, 캐시)
  │   · output/_enumeration/<이미지명>.json
  │
  ② 편집 — gemini-3.1-flash-image
  │   · 전체 사진 + 분리·가림복구·정면화 프롬프트 → 흰 배경 상품 이미지
  │
  ③ 태깅 — 편집 결과 이미지를 Gemini structured output으로 분석
  │   · 문서 §5-1 필드: item_name, category_large/small, season[], style[],
  │     color, pattern, fit, material, sleeve, length, usage[],
  │     layer_role, layer_order
  │   · enum 강제(taxonomy.py) + 대분류-소분류 짝 사후 보정
  │
  ④ 검증 — 문서 §5-2 대분류별 필수 필드 누락을 _missing_required로 기록
  │
  ⑤ 저장 — output/gemini-3.1-flash-image/<이미지명>/item_XX_<대분류>.png
          + items.json
```

## items.json 아이템 예시

```json
{
  "item_name": "화이트 오버핏 반팔 티셔츠",
  "category_large": "상의", "category_small": "티셔츠",
  "season": ["여름"], "style": ["캐주얼", "베이직"],
  "color": "화이트", "pattern": "무지", "fit": "오버핏",
  "material": "코튼", "sleeve": "반팔", "length": "기본",
  "usage": ["데일리", "외출"], "layer_role": "기본 상의", "layer_order": 1,
  "_missing_required": [],
  "_enum": {"id": 0, "label_ko": "...", "descriptor_en": "...",
            "occluded_by": ["hair"], "view_angle": "front"},
  "_image_file": "item_00_상의.png",
  "_timings_sec": {"edit": 1.2, "tagging": 0.8},
  "_error": null
}
```

## 실행

```bash
cd test/test-llm2
cp <테스트 사진들> input/
export GEMINI_API_KEY=...        # 또는 저장소 루트 .env (키 1개면 충분)

./run_docker.sh                  # input/ 전체
NO_BUILD=1 ./run_docker.sh       # 재빌드 생략
python run_all.py path/to/1.jpg  # 로컬 실행 (pip install -r requirements.txt)
```

## 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `GEMINI_API_KEY` | (필수) | 열거·편집·태깅 공용 |
| `GEMINI_ENUM_MODEL` | `gemini-3.5-flash` | ① 열거 |
| `GEMINI_FLASH_IMAGE_MODEL` | `gemini-3.1-flash-image` | ② 편집 |
| `GEMINI_TAG_MODEL` | `gemini-3.5-flash` | ③ 태깅 |

## 참고

- 태그 enum·필수 필드 정의는 `common/taxonomy.py` 한 곳에 있다.
  Confluence 문서가 갱신되면 이 파일을 함께 갱신할 것.
- color는 문서상 "문자열 또는 배열"이지만 대표 색상 1개(배색은 "멀티")로
  통일했다 — 기존 test-sam 태깅·Qdrant 파이프라인과 형태를 맞추기 위함.
- 편집 결과(생성 이미지)를 태깅하므로, 원본 사진 태그와 비교하면
  편집 과정의 identity drift(색·패턴 변형)를 검출할 수 있다.
- 비용: 이미지당 열거 1회 + 아이템당 편집 1회($0.067/1K) + 태깅 1회(센트 미만).
