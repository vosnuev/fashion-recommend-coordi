"""후보 4: SAM 3 (검출+분할) + Gemini Flash (태깅)

- SAM 3: 텍스트(컨셉) 프롬프트만으로 검출+분할을 한 번에 수행 (Grounding DINO 역할 흡수).
  가중치(sam3.pt)는 HF gated 배포라 자동 다운로드 불가 → 별도 준비 후 경로 지정.
- Gemini: 흰 배경 크롭을 입력으로 Confluence 태그 스키마를 structured output(JSON)으로 직접 생성.
  제로샷 분류(SigLIP)로는 불가능했던 item_name·usage 등 맥락 필드까지 한 번에 해결.
- 임베딩(FashionSigLIP)은 검색용이므로 이 테스트에서는 의도적으로 생략.

실행: python test_sam3_gemini.py [이미지경로 ...] [--out <출력폴더>]
  - 이미지 경로를 생략하면 test/input/ 의 모든 jpg를 일괄 처리한다.
  - 결과는 기본적으로 test/output/sam3_gemini/<이미지명>/ 에 저장된다.
환경변수:
  GEMINI_API_KEY  (필수) Gemini API 키
  GEMINI_MODEL    (기본 gemini-3.5-flash)
  SAM3_WEIGHTS    (기본 test/sam3/sam3.pt)
  DEVICE          (기본: cuda 가능 시 cuda)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

TEST_ROOT = Path(__file__).resolve().parent.parent  # test/

MODEL_NAME = "sam3_gemini"
SAM3_WEIGHTS = os.getenv(
    "SAM3_WEIGHTS", str(Path(__file__).resolve().parent / "sam3.pt")
)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
NMS_IOU = 0.6
MIN_AREA_RATIO = 0.005  # 전체 이미지 대비 최소 마스크 면적(오검출 컷)
CROP_PAD = 0.05         # bbox 크롭 여백 비율

# SAM 3 컨셉 프롬프트. 세부 분류는 Gemini가 담당하므로
# 여기서는 "아이템을 빠짐없이 분리"하는 데 필요한 수준으로만 나눈다.
SAM3_PROMPTS: list[str] = [
    "t-shirt", "shirt", "sweater", "hoodie", "sleeveless top",
    "jacket", "coat", "padded jacket", "cardigan", "vest",
    "dress", "jumpsuit",
    "pants", "shorts", "skirt", "leggings",
    "shoes", "bag", "hat", "scarf", "belt", "sunglasses",
]

# ── Confluence 태그 체계 (taxonomy.py의 한글 라벨만 발췌) ──
# Gemini는 한글 라벨을 직접 이해하므로 영어 프롬프트 매핑이 필요 없다.
CATEGORY_LARGE = ["상의", "하의", "아우터", "원피스/세트",
                  "신발", "가방", "액세서리", "언더웨어/이너웨어"]
CATEGORY_SMALL = {
    "상의": ["티셔츠", "셔츠/블라우스", "니트/스웨터", "후드/맨투맨", "민소매"],
    "하의": ["데님 팬츠", "슬랙스", "코튼 팬츠", "트레이닝 팬츠",
             "숏팬츠", "스커트", "레깅스"],
    "아우터": ["자켓", "코트", "패딩", "점퍼/블루종", "가디건", "후드집업", "베스트"],
    "원피스/세트": ["원피스", "점프수트/오버롤", "셋업", "파자마/홈웨어 세트"],
    "신발": ["스니커즈", "구두/로퍼", "부츠", "샌들/슬리퍼", "플랫/단화"],
    "가방": ["백팩", "크로스백", "숄더백", "토트백", "에코백", "클러치/파우치", "지갑"],
    "액세서리": ["모자", "벨트", "주얼리", "머플러/스카프", "양말",
                 "안경/선글라스", "헤어 액세서리"],
    "언더웨어/이너웨어": ["브라", "팬티/드로즈", "런닝/캐미솔", "속바지",
                          "보정속옷", "내복/발열 이너"],
}
STYLES = ["캐주얼", "포멀", "미니멀", "스트릿", "스포티", "러블리", "페미닌",
          "시크", "빈티지", "아웃도어", "댄디", "아메카지", "트렌디", "리조트", "베이직"]
COLORS = ["화이트", "블랙", "그레이", "네이비", "블루", "스카이블루", "레드", "핑크",
          "오렌지", "옐로우", "그린", "카키", "브라운", "베이지", "아이보리", "퍼플", "멀티"]
PATTERNS = ["무지", "체크", "스트라이프", "도트", "플로럴", "그래픽/로고", "카모", "애니멀"]
FITS = ["오버핏", "레귤러핏", "슬림핏", "와이드핏"]
MATERIALS = ["코튼", "데님", "니트", "울", "린넨", "레더", "나일론", "폴리에스터",
             "시폰", "코듀로이", "트위드", "퍼/무스탕", "패딩충전재"]
SLEEVES = ["반팔", "긴팔", "민소매"]
LENGTHS = ["크롭", "기본", "롱"]
SEASONS = ["봄", "여름", "가을", "겨울", "간절기"]
LAYER_ROLES = ["기본 상의", "레이어드 상의", "아우터"]

ALL_SMALL = [s for smalls in CATEGORY_SMALL.values() for s in smalls]

# Gemini structured output 스키마 (OpenAPI subset).
# enum으로 라벨을 강제해 taxonomy 밖의 값이 나오는 것을 막는다.
GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "item_name": {"type": "STRING",
                      "description": "색·핏·소매·소재가 드러나는 자연스러운 한국어 상품명"},
        "category_large": {"type": "STRING", "enum": CATEGORY_LARGE},
        "category_small": {"type": "STRING", "enum": ALL_SMALL},
        "season": {"type": "ARRAY", "items": {"type": "STRING", "enum": SEASONS}},
        "style": {"type": "ARRAY", "items": {"type": "STRING", "enum": STYLES},
                  "description": "최대 2개"},
        "color": {"type": "STRING", "enum": COLORS},
        "pattern": {"type": "STRING", "enum": PATTERNS},
        "fit": {"type": "STRING", "enum": FITS, "nullable": True},
        "material": {"type": "STRING", "enum": MATERIALS, "nullable": True},
        "sleeve": {"type": "STRING", "enum": SLEEVES, "nullable": True},
        "length": {"type": "STRING", "enum": LENGTHS, "nullable": True},
        "usage": {"type": "ARRAY", "items": {"type": "STRING"}},
        "layer_role": {"type": "STRING", "enum": LAYER_ROLES, "nullable": True},
        "layer_order": {"type": "INTEGER", "nullable": True},
    },
    "required": ["item_name", "category_large", "category_small", "season",
                 "style", "color", "pattern", "usage"],
}

TAGGING_PROMPT = """\
흰 배경에 놓인 패션 아이템 사진이다. 스키마에 맞춰 태깅하라.

규칙:
- category_small은 반드시 category_large에 속한 소분류만 고른다.
- 니트 베스트(민소매 니트)는 상의/니트/스웨터, 패딩·퍼 베스트는 아우터/베스트.
- fit/sleeve/length는 의류(상의·하의·아우터·원피스/세트)에만, 아니면 null.
  sleeve는 하의에는 null.
- season 유도: 패딩·퍼·울→겨울, 코트→가을·겨울, 반팔·민소매·린넨→여름,
  니트·후드→가을·겨울·간절기, 판단이 어려우면 봄·가을·간절기.
- layer_role/order: 아우터→(아우터,3), 민소매 니트 등 레이어드용 상의→(레이어드 상의,2),
  일반 상의·원피스→(기본 상의,1), 그 외→null.
- usage: 사진만으로 특정 어려우면 ["데일리", "외출"].
- 세그멘테이션 모델의 검출 라벨 힌트: "{hint}" (참고만 하고 이미지 우선으로 판단)
"""


@dataclass
class SegmentedItem:
    mask: np.ndarray                      # (H, W) bool
    label: str                            # SAM 3 검출 라벨(프롬프트 구문)
    score: float = 1.0
    bbox: tuple[int, int, int, int] = field(default=None)


# ── ① SAM 3: 텍스트 프롬프트 → 검출 + 마스크 ─────────────
_PREDICTOR_CACHE: dict[str, object] = {}


def _load_predictor(device: str):
    """SAM3 프레딕터를 디바이스별로 1회만 로드 (일괄 처리 시 재로드 방지)."""
    if device not in _PREDICTOR_CACHE:
        from ultralytics.models.sam import SAM3SemanticPredictor

        _PREDICTOR_CACHE[device] = SAM3SemanticPredictor(
            overrides=dict(model=SAM3_WEIGHTS, task="segment", mode="predict",
                           conf=0.3, device=device, save=False, verbose=False)
        )
    return _PREDICTOR_CACHE[device]


def segment(image_path: str, device: str) -> tuple[list[SegmentedItem], dict]:
    from torchvision.ops import nms

    timings: dict[str, float] = {}
    image = Image.open(image_path).convert("RGB")

    t0 = time.perf_counter()
    predictor = _load_predictor(device)
    predictor.set_image(image_path)
    timings["sam3_model_load"] = round(time.perf_counter() - t0, 3)  # warm이면 ~0

    t0 = time.perf_counter()
    res = predictor(text=SAM3_PROMPTS)[0]
    timings["sam3_inference"] = round(time.perf_counter() - t0, 3)

    if res.masks is None or len(res.masks) == 0:
        return [], timings

    boxes = res.boxes.xyxy.cpu()
    scores = res.boxes.conf.cpu()
    masks = res.masks.data.cpu()
    names = getattr(res, "names", None) or []
    class_ids = res.boxes.cls.detach().cpu().tolist()

    if isinstance(names, dict):
        labels = [
            str(names.get(int(class_id), int(class_id)))
            for class_id in class_ids
        ]
    elif isinstance(names, (list, tuple)):
        labels = [
            str(names[int(class_id)])
            if 0 <= int(class_id) < len(names)
            else str(int(class_id))
            for class_id in class_ids
        ]
    else:
        labels = [str(int(class_id)) for class_id in class_ids]

    # 같은 옷이 "shirt"/"t-shirt" 등 여러 프롬프트로 중복 검출될 수 있어
    # 라벨 무관 NMS로 정리 (기존 grounded_sam2 테스트와 동일 정책)
    keep = nms(boxes, scores, NMS_IOU).tolist()

    items: list[SegmentedItem] = []
    for i in keep:
        mask = masks[i].numpy().astype(bool)
        if mask.shape != (image.height, image.width):
            mask = cv2.resize(mask.astype(np.uint8), image.size,
                              interpolation=cv2.INTER_NEAREST).astype(bool)
        items.append(SegmentedItem(mask=mask, label=labels[i],
                                   score=float(scores[i])))
    return items, timings


# ── 마스크 후처리 (기존 common/pipeline.py와 동일 정책) ────
def clean_mask(mask: np.ndarray) -> np.ndarray:
    """구멍 메움 + 경계 다듬기. 경계가 거칠면 흰 배경 합성 품질이 떨어진다."""
    m = mask.astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
    return m.astype(bool)


def white_bg_crop(image: np.ndarray, mask: np.ndarray) -> tuple[Image.Image, tuple]:
    """마스크 영역만 남기고 흰 배경(255) 합성 후 bbox+여백으로 크롭."""
    ys, xs = np.where(mask)
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    h, w = image.shape[:2]
    pad_x, pad_y = int((x2 - x1) * CROP_PAD), int((y2 - y1) * CROP_PAD)
    x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)

    white = np.full_like(image, 255)
    composed = np.where(mask[..., None], image, white)
    crop = composed[y1:y2, x1:x2]
    return Image.fromarray(crop), (int(x1), int(y1), int(x2), int(y2))


def save_overlay(image: np.ndarray, items: list[SegmentedItem], path: Path) -> None:
    overlay = image.copy()
    rng = np.random.default_rng(0)
    for it in items:
        color = rng.integers(60, 255, 3).tolist()
        overlay[it.mask] = (overlay[it.mask] * 0.5 + np.array(color) * 0.5).astype(np.uint8)
        x1, y1, x2, y2 = it.bbox
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
        cv2.putText(overlay, f"{it.label} {it.score:.2f}", (x1, max(15, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    cv2.imwrite(str(path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))


# ── ② Gemini: 크롭 → 태그 JSON ────────────────────────────
class GeminiTagger:
    def __init__(self) -> None:
        from google import genai  # 지연 import: 세그 단독 실행 시 불필요

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 필요합니다.")
        self.client = genai.Client(api_key=api_key)

    def tag(self, crop: Image.Image, hint: str) -> dict:
        from google.genai import types

        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        resp = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"),
                TAGGING_PROMPT.format(hint=hint),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GEMINI_SCHEMA,
                temperature=0.1,
            ),
        )
        feats = json.loads(resp.text)

        # enum이 대분류-소분류 짝까지 강제하지는 못하므로 마지막에 정합성만 보정
        if feats.get("category_small") not in CATEGORY_SMALL.get(feats.get("category_large"), []):
            for large, smalls in CATEGORY_SMALL.items():
                if feats.get("category_small") in smalls:
                    feats["category_large"] = large
                    break
        return feats


# ── 실행 파이프라인 ───────────────────────────────────────
def collect_input_images(input_dir: Path) -> list[str]:
    """test/input/ 폴더의 jpg 이미지 목록을 정렬해 반환. 없으면 즉시 종료.

    (common 패키지와 동일 정책이지만, 이 테스트는 common 미의존을 유지한다.)
    """
    images = sorted(
        str(p) for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}
    ) if input_dir.is_dir() else []
    if not images:
        raise SystemExit(
            f"입력 이미지가 없습니다: {input_dir} (jpg 파일을 넣어주세요)"
        )
    return images


def run(image_path: str, out_root: str, tagger: GeminiTagger | None = None) -> Path:
    device = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
    items, timings = segment(image_path, device)

    image = np.array(Image.open(image_path).convert("RGB"))
    total_px = image.shape[0] * image.shape[1]
    out_dir = Path(out_root) / MODEL_NAME / Path(image_path).stem
    out_dir.mkdir(parents=True, exist_ok=True)

    kept: list[SegmentedItem] = []
    for it in items:
        it.mask = clean_mask(it.mask)
        if it.mask.sum() / total_px >= MIN_AREA_RATIO:
            kept.append(it)

    tagger = tagger or GeminiTagger()
    results = []
    t0 = time.perf_counter()
    for i, it in enumerate(kept):
        crop, it.bbox = white_bg_crop(image, it.mask)
        try:
            feats = tagger.tag(crop, it.label)
        except Exception as e:  # 태깅 실패 아이템도 크롭·메타는 남긴다
            feats = {"item_name": None, "category_large": "미분류", "_error": str(e)}
        fname = f"item_{i:02d}_{feats.get('category_large', '미분류')}.png".replace("/", "_")
        crop.save(out_dir / fname)
        results.append({
            **feats,
            "_seg": {
                "model": MODEL_NAME, "raw_label": it.label,
                "score": round(it.score, 4), "bbox": it.bbox,
                "mask_area_ratio": round(float(it.mask.sum()) / total_px, 4),
            },
            "_image_file": fname,
        })
    timings["gemini_tagging"] = round(time.perf_counter() - t0, 3)

    save_overlay(image, kept, out_dir / "_overlay.jpg")
    report = {
        "source_image": str(image_path),
        "model": MODEL_NAME,
        "sam3_weights": SAM3_WEIGHTS,
        "gemini_model": GEMINI_MODEL,
        "num_items": len(results),
        "timings_sec": timings,
        "items": results,
    }
    (out_dir / "items.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[{MODEL_NAME}] {len(results)} items -> {out_dir}")
    print(json.dumps(timings, indent=2))
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="*",
                    help="이미지 경로 목록. 생략 시 test/input/의 모든 jpg")
    ap.add_argument("--out", default=str(TEST_ROOT / "output"))
    args = ap.parse_args()

    images = args.images or collect_input_images(TEST_ROOT / "input")

    if not Path(SAM3_WEIGHTS).is_file():
        raise SystemExit(
            f"SAM3 가중치가 없습니다: {SAM3_WEIGHTS}\n"
            "python sam3/download_sam3.py 로 받거나 SAM3_WEIGHTS로 경로를 지정하세요."
        )

    # Gemini 클라이언트·API 키 확인은 배치 시작 전에 1회만 수행
    tagger = GeminiTagger()

    failures = 0
    for i, image_path in enumerate(images, 1):
        print(f"\n[{MODEL_NAME}] ({i}/{len(images)}) {image_path}")
        try:
            run(image_path, args.out, tagger)
        except Exception as e:  # 이미지 1장 실패가 전체 배치를 막지 않도록
            failures += 1
            print(f"[{MODEL_NAME}] 실패: {image_path} -> {e}", file=sys.stderr)

    print(f"\n[{MODEL_NAME}] 완료: 성공 {len(images) - failures} / 실패 {failures}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
