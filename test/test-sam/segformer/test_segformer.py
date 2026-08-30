"""후보 1: SegFormer clothes (mattmdjaga/segformer_b2_clothes)

semantic segmentation — 가볍고 설치 간단, CPU도 가능.
같은 클래스 옷 2벌은 분리 못 하므로 connected component로 근사 분리한다.

실행: python test_segformer.py [이미지경로 ...] [--out <출력폴더>]
  - 이미지 경로를 생략하면 test/input/ 의 모든 jpg를 일괄 처리한다.
  - 결과는 기본적으로 test/output/segformer_b2_clothes/<이미지명>/ 에 저장된다.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

# test/ 하위 폴더에서 실행해도 test/common 패키지를 찾도록 보정
TEST_ROOT = Path(__file__).resolve().parent.parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from common import SegmentedItem, run, collect_input_images

MODEL_ID = "mattmdjaga/segformer_b2_clothes"

# ATR 18클래스 중 패션 아이템 클래스 → (라벨, 대분류 힌트)
# 신체·배경 클래스(피부·머리·다리 등)는 제외한다.
ATR_CLASSES: dict[int, tuple[str, str | None]] = {
    1: ("Hat", "액세서리"),
    3: ("Sunglasses", "액세서리"),
    4: ("Upper-clothes", "상의"),     # 아우터도 여기에 섞임 → 대분류는 SigLIP 재판별
    5: ("Skirt", "하의"),
    6: ("Pants", "하의"),
    7: ("Dress", "원피스/세트"),
    8: ("Belt", "액세서리"),
    9: ("Left-shoe", "신발"),
    10: ("Right-shoe", "신발"),
    16: ("Bag", "가방"),
    17: ("Scarf", "액세서리"),
}
MERGE_LR_SHOES = True  # 왼발/오른발 → 신발 1아이템으로 병합
# Upper-clothes는 상의/아우터 구분이 안 되므로 힌트를 주지 않고 SigLIP에 맡긴다.
NO_HINT_LABELS = {"Upper-clothes"}


_MODEL_CACHE: dict[str, tuple] = {}


def _load_model(device: str) -> tuple:
    """모델을 디바이스별로 1회만 로드 (일괄 처리 시 재로드 방지)."""
    if device not in _MODEL_CACHE:
        from transformers import AutoModelForSemanticSegmentation, SegformerImageProcessor

        processor = SegformerImageProcessor.from_pretrained(MODEL_ID)
        model = AutoModelForSemanticSegmentation.from_pretrained(MODEL_ID).to(device).eval()
        _MODEL_CACHE[device] = (processor, model)
    return _MODEL_CACHE[device]


def segment(image_path: str, device: str) -> tuple[list[SegmentedItem], dict]:
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    processor, model = _load_model(device)
    timings["seg_model_load"] = round(time.perf_counter() - t0, 3)  # warm이면 ~0

    image = Image.open(image_path).convert("RGB")
    t0 = time.perf_counter()
    inputs = processor(images=image, return_tensors="pt").to(device)
    with torch.no_grad():
        logits = model(**inputs).logits
    # 원본 해상도로 업샘플 후 클래스 맵 생성
    up = torch.nn.functional.interpolate(
        logits, size=image.size[::-1], mode="bilinear", align_corners=False
    )
    seg_map = up.argmax(dim=1)[0].cpu().numpy()
    timings["seg_inference"] = round(time.perf_counter() - t0, 3)

    items: list[SegmentedItem] = []
    shoe_mask = np.zeros(seg_map.shape, dtype=bool)

    for cls_id, (label, hint) in ATR_CLASSES.items():
        mask = seg_map == cls_id
        if not mask.any():
            continue
        if MERGE_LR_SHOES and label in ("Left-shoe", "Right-shoe"):
            shoe_mask |= mask
            continue
        # semantic 마스크를 connected component로 분리 (동일 클래스 복수 아이템 근사)
        n, comp = cv2.connectedComponents(mask.astype(np.uint8))
        for c in range(1, n):
            items.append(SegmentedItem(
                mask=comp == c,
                label=label,
                category_large_hint=None if label in NO_HINT_LABELS else hint,
            ))

    if shoe_mask.any():
        items.append(SegmentedItem(mask=shoe_mask, label="Shoes",
                                   category_large_hint="신발"))
    return items, timings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="*",
                    help="이미지 경로 목록. 생략 시 test/input/의 모든 jpg")
    ap.add_argument("--out", default=str(TEST_ROOT / "output"))
    args = ap.parse_args()

    images = args.images or collect_input_images(TEST_ROOT / "input")
    device = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")

    failures = 0
    for i, image_path in enumerate(images, 1):
        print(f"\n[segformer] ({i}/{len(images)}) {image_path}")
        try:
            items, timings = segment(image_path, device)
            run(image_path, args.out, "segformer_b2_clothes", items, timings)
        except Exception as e:  # 이미지 1장 실패가 전체 배치를 막지 않도록
            failures += 1
            print(f"[segformer] 실패: {image_path} -> {e}", file=sys.stderr)

    print(f"\n[segformer] 완료: 성공 {len(images) - failures} / 실패 {failures}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
