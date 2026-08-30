# 세그멘테이션 모델 후보 비교 테스트

옷장 등록 기능(사진 → 아이템 분리 → 특징 추출)의 세그멘테이션 모델 후보를
같은 사진들을 대상으로 비교한다.

## 구조

```
test/
├── Dockerfile             # 3개 테스트 통합 실행 이미지 (GPU)
├── requirements.txt       # 3개 테스트 통합 의존성
├── run_all_tests.sh       # 3개 테스트 순차 실행 (컨테이너 ENTRYPOINT 겸용)
├── run_docker.sh          # 호스트용: 빌드 + 볼륨 마운트 + 실행
├── input/                 # 테스트 대상 jpg를 여기에 넣는다
├── output/                # 모든 테스트 결과가 여기에 쌓인다
├── common/
│   ├── taxonomy.py            # Confluence 카테고리-태그 매핑 enum
│   ├── feature_extractor.py   # Marqo-FashionSigLIP 제로샷 특징 추출 (segformer/sam2 공용)
│   └── pipeline.py            # 마스크 정리 → 흰 배경 크롭 → 특징 추출 → 저장 (공용)
├── segformer/
│   └── test_segformer.py                     # 후보 1: SegFormer clothes (경량, semantic)
├── sam2/
│   ├── test_grounded_sam2_common_package.py  # 후보 2(실행 대상): Grounding DINO + 공식 Meta SAM2
│   └── test_grounded_sam2.py                 # (보관) ultralytics SAM2 기반 구버전
└── sam3/
    ├── test_sam3_gemini.py    # 후보 3: SAM 3 (검출+분할) + Gemini 태깅
    └── download_sam3.py       # sam3.pt 다운로드 (HF gated → 토큰 필요)
```

모든 테스트는 **`test/input/`의 jpg 전체를 일괄 처리**하고 결과를
**`test/output/<모델명>/<이미지명>/`**에 저장한다.
(개별 이미지 경로를 인자로 넘기면 그 이미지들만 처리한다.)

## Docker로 실행 (권장)

```bash
cd test
cp <테스트할 사진들>.jpg input/

# sam3 준비물: 가중치 + Gemini 키 (segformer/sam2만 돌리면 불필요)
python sam3/download_sam3.py        # HF_TOKEN 필요 (gated)
export GEMINI_API_KEY=...           # 또는 저장소 루트 .env

./run_docker.sh                     # 빌드 + 3개 테스트 모두 실행
./run_docker.sh segformer sam2      # 특정 테스트만
NO_BUILD=1 ./run_docker.sh          # 재빌드 생략
```

## 로컬(비도커) 실행

```bash
cd test
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install --no-build-isolation -r requirements.txt

./run_all_tests.sh                  # 3개 모두
python segformer/test_segformer.py                     # 개별 실행 (input/ 전체)
python sam2/test_grounded_sam2_common_package.py
python sam3/test_sam3_gemini.py
python segformer/test_segformer.py path/to/one.jpg     # 특정 이미지만
```

- `DEVICE=cuda|cpu` 환경변수로 디바이스 지정 (기본: cuda 가능 시 cuda)
- 첫 실행은 HF/ultralytics 모델 다운로드로 오래 걸림. latency 비교는 2회차부터.
  (배치 실행 시 모델은 1회만 로드되므로 2번째 이미지부터가 warm 수치다.)

## 출력

`output/<모델명>/<이미지명>/`:

- `item_XX_<대분류>.png` — 아이템별 흰 배경 크롭
- `_overlay.jpg` — 검출 시각화
- `items.json` — 아이템별 특징(Confluence 스키마) + 세그 메타 + 단계별 latency

모델명: `segformer_b2_clothes` / `grounded_sam2` / `sam3_gemini`
(보관용 구버전 실행 시 `grounded_sam2_ultralytics`)

특징 스키마 예 (`items.json`의 각 항목):

```json
{
  "item_name": "화이트 오버핏 반팔 티셔츠",
  "category_large": "상의", "category_small": "티셔츠",
  "season": ["여름"], "style": ["캐주얼", "베이직"],
  "color": "화이트", "pattern": "무지", "fit": "오버핏",
  "material": "코튼", "sleeve": "반팔", "length": "기본",
  "usage": ["데일리", "외출"], "layer_role": "기본 상의", "layer_order": 1,
  "_confidence": {"category_small": 0.91, "...": "제로샷 확률"},
  "_seg": {"model": "...", "raw_label": "...", "score": 0.87, "bbox": [..]}
}
```

## 특징 추출 방식

- **segformer / sam2** (`common/` 공용): FashionSigLIP 제로샷 분류.
  시각 판별 필드(category, color, pattern, fit, material, sleeve, length, style)는
  제로샷, `season`·`layer_role/order`는 규칙 유도, `usage`는 기본값.
- **sam3**: Gemini structured output이 태그 스키마 전체(item_name·usage 포함)를 직접 생성.

## 비교 체크리스트

| 항목 | 확인 방법 |
|---|---|
| 아이템 분리 정확도 (누락·오검출) | `_overlay.jpg`, `num_items` |
| 마스크 경계 품질 (흰 배경 합성) | `item_XX_*.png` 육안 비교 |
| 레이어드 착장 분리 (재킷 속 이너) | 레이어드 사진으로 테스트 |
| latency | `items.json`의 `timings_sec` (warm 기준) |
| 특징 추출 정확도 | 동일 크롭이라도 마스크 품질에 따라 달라짐 |

## 알려진 제약

- **SegFormer**: semantic 방식 — 같은 클래스 2벌 분리 불가(connected component로 근사),
  상의/아우터 구분 없음(Upper-clothes 단일 클래스 → SigLIP이 대분류 재판별).
- **Grounded SAM2**: 모델 2개 로드로 무겁고 콜드 스타트 김. threshold 튜닝 필요.
- **SAM3 + Gemini**: sam3.pt가 HF gated라 자동 다운로드 불가(액세스 승인 + 토큰 필요),
  `GEMINI_API_KEY` 필수. Gemini 호출 비용·rate limit 존재.
