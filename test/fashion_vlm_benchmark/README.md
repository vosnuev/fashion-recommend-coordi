# Fashion VLM Wardrobe 태그 벤치마크

HyperCLOVA와 Qwen에 같은 이미지와 같은 프롬프트를 입력해 Wardrobe 핵심 태그를 비교한다. 이 단계에서는 모델 설치나 운영 이미지 프로세서 연결을 하지 않는다.

## 비교 범위

기존 이미지 프로세서와 `WardrobeItem`이 사용하는 14개 태깅 필드를 비교한다.

- 이름·분류: `item_name`, `category_large`, `category_small`
- 배열 태그: `season`, `style`, `usage`
- 단일 태그: `color`, `pattern`, `fit`, `material`, `sleeve`, `length`
- 레이어: `layer_role`, `layer_order`

프롬프트의 허용값은 실행할 때마다 `api/apps/wardrobe/taxonomy.py`에서 읽는다. 예시 문서에 값을 복사하지 않으므로 실제 DB 계약과 프롬프트가 어긋나는 것을 줄인다.

## 1. 이미지와 정답지 준비

`dataset.example.json`을 `dataset.json`으로 복사하고 다음 파일명으로 실제 이미지를 배치한다.

```text
images/
├── 01_tshirt.jpg
├── 02_shirt.jpg
├── 03_knit.jpg
├── 04_jacket.jpeg
├── 05_pants.jpeg
├── 06_skirt.jpeg
├── 07_dress.jpeg
├── 08_shoes.jpg
└── 09_bag.jpg
```

`dataset.json`의 `product_name`과 `expected`는 이미지를 직접 확인한 사람이 작성한다. 값은 기존 Wardrobe taxonomy와 정확히 같아야 한다. 이미지로 확정하기 어려운 소재와 해당하지 않는 필드는 `null`로 둔다. `input_mode`는 상품명과 이미지를 함께 쓰는 `catalog`, 직접 촬영 이미지를 쓰는 `photo`로 구분한다.

실제 이미지는 라이선스와 개인정보를 확인해야 하므로 기본적으로 Git에서 제외한다. 정답지는 검수가 끝난 뒤 팀이 버전 관리 여부를 결정한다.

## 2. 로컬 데이터 검증과 프롬프트 생성

```bash
cd test/fashion_vlm_benchmark
python benchmark.py init
python benchmark.py validate
python benchmark.py prepare
```

`init`은 기존 `dataset.json`을 덮어쓰지 않는다. 의도적으로 다시 만들 때만
`python benchmark.py init --force`를 사용한다.

검증이 성공하면 `prompts.jsonl`이 생성된다. 각 모델 runner는 이 파일의 `prompt`를 수정 없이 사용해야 한다.

## 3. PuTTY에서 GPU 서버 점검

저장소를 GPU 서버에 clone/pull한 뒤 모델 설치 전에 실행한다.

```bash
cd test/fashion_vlm_benchmark
mkdir -p results
bash check_gpu_env.sh | tee results/gpu_environment.txt
```

결과에서 다음을 확인한다.

- GPU 이름·개수·VRAM
- 현재 GPU 메모리와 compute process 점유
- 디스크와 시스템 메모리
- Conda 환경
- Python·PyTorch·CUDA 인식 상태

이 결과를 검토한 뒤 기존 환경 재사용 또는 모델별 새 환경 생성을 결정한다.

## 4. 모델 runner 출력 계약

HyperCLOVA와 Qwen runner는 이미지당 JSONL 한 줄을 기록해야 한다.

```json
{
  "sample_id": "03_knit",
  "model": "naver-hyperclovax/HyperCLOVAX-SEED-Vision-Instruct-3B",
  "raw_output": "{\"item_name\":\"그레이 오버핏 브이넥 니트\",\"category_large\":\"상의\",\"category_small\":\"니트/스웨터\",\"season\":[\"가을\",\"겨울\",\"간절기\"],\"style\":[\"캐주얼\",\"미니멀\"],\"color\":\"그레이\",\"pattern\":\"무지\",\"fit\":\"오버핏\",\"material\":\"니트\",\"sleeve\":\"긴팔\",\"length\":\"기본\",\"usage\":[\"데일리\",\"외출\"],\"layer_role\":\"기본 상의\",\"layer_order\":1}",
  "latency_seconds": 2.1,
  "peak_vram_mb": 7420,
  "error": null
}
```

`raw_output`은 후처리 전 모델 응답을 그대로 보존한다. runner가 이미 파싱했다면 `parsed_output` 객체를 추가할 수 있지만 원본 응답을 제거하면 안 된다.

공정한 비교를 위해 다음 조건을 같게 유지한다.

- 같은 이미지와 같은 입력 모드
- 생성된 공통 프롬프트 원문
- 같은 최대 출력 토큰
- 결정적 생성 설정 또는 가능한 가장 낮은 temperature
- warm-up 횟수와 측정 반복 횟수
- 입력 이미지 리사이즈 정책

모델 최초 로딩 시간은 이미지별 latency와 분리해 기록한다. VRAM은 추론 직전 peak 통계를 초기화하고 이미지별 `torch.cuda.max_memory_allocated()`를 기록한다.

## 5. 비교표 생성

예를 들어 각 runner가 다음 파일을 만들었다고 가정한다.

- `results/hyperclova.jsonl`
- `results/qwen.jsonl`

평가 명령은 다음과 같다.

```bash
python benchmark.py evaluate \
  --results results/hyperclova.jsonl results/qwen.jsonl
```

생성 파일:

- `results/evaluation/details.csv`: 이미지별 정답 여부와 오류
- `results/evaluation/summary.json`: 후속 자동 처리용 집계
- `results/evaluation/summary.md`: 사람이 읽는 모델 비교표

평가기는 다음을 계산한다.

- 대·소분류, 색상, 패턴, 핏, 소재, 소매, 기장, 레이어 정확도
- 계절·스타일·용도의 집합 기준 precision·recall·F1
- 14개 필드 누락 여부와 전체 필드 완전 일치율
- 아이템명 생성 여부(문장 품질은 별도 사람 검수)
- JSON 파싱 성공률
- Wardrobe taxonomy 준수율
- 평균 처리시간
- 최대 peak VRAM

## 6. 현재 제한

- 현재 9장은 쇼핑몰형 상품 이미지라 `catalog` 모드의 예비 평가용이다.
- 액세서리·언더웨어를 포함한 소분류 커버리지와 직접 촬영 `photo` 데이터는 추가해야 한다.
- `usage`는 기존 프로젝트에 공식 enum이 없으므로 taxonomy 유효성보다 정답 집합 F1로 비교한다.
