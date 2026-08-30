"""후보 3: Grounding DINO + SAM2

open-vocabulary 검출(텍스트 프롬프트) + 프롬프터블 세그멘테이션.
품질 상한이 가장 높고 카테고리 확장이 프롬프트 수정만으로 가능. GPU 권장.

- Grounding DINO: HF transformers (IDEA-Research/grounding-dino-base)
- SAM2: ultralytics 배포 체크포인트(sam2.1_l.pt, 첫 실행 시 자동 다운로드)
  * 공식 facebookresearch/sam2 패키지도 가능하나 설치가 단순한 쪽을 기본으로 한다.

실행: python test_grounded_sam2.py [이미지경로 ...] [--out <출력폴더>]
  - 이미지 경로를 생략하면 test/input/ 의 모든 jpg를 일괄 처리한다.
  - 결과는 기본적으로 test/output/grounded_sam2_ultralytics/<이미지명>/ 에 저장된다.
  - 현재 실행 대상은 test_grounded_sam2_common_package.py(공식 Meta SAM2)이며
    이 파일은 ultralytics 기반 비교용으로 보관한다.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

# test/ 하위 폴더에서 실행해도 test/common 패키지를 찾도록 보정
TEST_ROOT = Path(__file__).resolve().parent.parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from common import SegmentedItem, run, collect_input_images

DINO_ID = "IDEA-Research/grounding-dino-base"
SAM2_WEIGHTS = os.getenv("SAM2_WEIGHTS", "sam2.1_l.pt")
BOX_THRESHOLD = 0.30
TEXT_THRESHOLD = 0.25
NMS_IOU = 0.6

# 검출 프롬프트 구문 → 대분류 힌트.
# DINO 텍스트 타워는 영어 중심이므로 프롬프트는 영어로 구성한다.
PROMPT_CLASSES: dict[str, str | None] = {
    "t-shirt": "상의",
    "shirt": "상의",
    "sweater": "상의",
    "hoodie": "상의",
    "sleeveless top": "상의",
    "jacket": "아우터",
    "coat": "아우터",
    "padded jacket": "아우터",
    "cardigan": "아우터",
    "vest": None,          # 니트 베스트(상의) vs 패딩 베스트(아우터) → SigLIP 판별
    "dress": "원피스/세트",
    "jumpsuit": "원피스/세트",
    "pants": "하의",
    "jeans": "하의",
    "shorts": "하의",
    "skirt": "하의",
    "leggings": "하의",
    "shoes": "신발",
    "sneakers": "신발",
    "boots": "신발",
    "sandals": "신발",
    "bag": "가방",
    "backpack": "가방",
    "hat": "액세서리",
    "cap": "액세서리",
    "scarf": "액세서리",
    "belt": "액세서리",
    "sunglasses": "액세서리",
}
TEXT_PROMPT = " . ".join(PROMPT_CLASSES) + " ."


def _match_hint(label: str) -> str | None:
    """DINO가 반환한 라벨(프롬프트 구문 조각)을 힌트 테이블에 매칭."""
    label = label.strip().lower()
    if label in PROMPT_CLASSES:
        return PROMPT_CLASSES[label]
    for phrase, hint in PROMPT_CLASSES.items():  # 부분 일치 fallback
        if phrase in label:
            return hint
    return None


def segment(image_path: str, device: str) -> tuple[list[SegmentedItem], dict]:
    from torchvision.ops import nms
    from transformers import AutoProcessor, GroundingDinoForObjectDetection
    from ultralytics import SAM

    timings: dict[str, float] = {}
    image = Image.open(image_path).convert("RGB")

    # ── ① Grounding DINO: 텍스트 프롬프트 → bbox ──────────
    t0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(DINO_ID)
    dino = GroundingDinoForObjectDetection.from_pretrained(DINO_ID).to(device).eval()
    timings["dino_model_load"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    inputs = processor(images=image, text=TEXT_PROMPT, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = dino(**inputs)
    det = processor.post_process_grounded_object_detection(
        outputs, inputs.input_ids,
        box_threshold=BOX_THRESHOLD, text_threshold=TEXT_THRESHOLD,
        target_sizes=[image.size[::-1]],
    )[0]
    timings["dino_inference"] = round(time.perf_counter() - t0, 3)

    boxes, scores, labels = det["boxes"], det["scores"], det["labels"]
    if len(boxes) == 0:
        return [], timings

    # 같은 옷이 "shirt"/"t-shirt" 등으로 중복 검출되므로 라벨 무관 NMS로 정리
    keep = nms(boxes, scores, NMS_IOU)
    boxes, scores = boxes[keep].cpu(), scores[keep].cpu()
    labels = [labels[i] for i in keep.tolist()]

    # ── ② SAM2: bbox 프롬프트 → 마스크 ────────────────────
    t0 = time.perf_counter()
    sam = SAM(SAM2_WEIGHTS)
    timings["sam2_model_load"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    sam_res = sam(image_path, bboxes=boxes.numpy(), verbose=False)[0]
    timings["sam2_inference"] = round(time.perf_counter() - t0, 3)

    items: list[SegmentedItem] = []
    for mask_t, score, label in zip(sam_res.masks.data, scores, labels):
        mask = mask_t.cpu().numpy().astype(bool)
        if mask.shape != (image.height, image.width):
            import cv2
            mask = cv2.resize(mask.astype(np.uint8), image.size,
                              interpolation=cv2.INTER_NEAREST).astype(bool)
        items.append(SegmentedItem(mask=mask, label=str(label),
                                   score=float(score),
                                   category_large_hint=_match_hint(str(label))))
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
        print(f"\n[grounded_sam2_ultralytics] ({i}/{len(images)}) {image_path}")
        try:
            items, timings = segment(image_path, device)
            # common_package 버전(grounded_sam2)과 출력 폴더가 겹치지 않도록 구분
            run(image_path, args.out, "grounded_sam2_ultralytics", items, timings)
        except Exception as e:  # 이미지 1장 실패가 전체 배치를 막지 않도록
            failures += 1
            print(f"[grounded_sam2_ultralytics] 실패: {image_path} -> {e}", file=sys.stderr)

    print(f"\n[grounded_sam2_ultralytics] 완료: 성공 {len(images) - failures} / 실패 {failures}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
