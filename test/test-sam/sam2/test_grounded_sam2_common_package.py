"""Grounding DINO + 공식 Meta SAM2 의류 인스턴스 추출.

프로젝트 구조 예시:

project/
├─ common/
│  ├─ __init__.py
│  ├─ pipeline.py
│  ├─ taxonomy.py
│  └─ feature_extractor.py
├─ test/
│  └─ test_grounded_sam2.py
└─ ...

실행: python test_grounded_sam2_common_package.py [이미지경로 ...] [--out <출력폴더>]
  - 이미지 경로를 생략하면 test/input/ 의 모든 jpg를 일괄 처리한다.
  - 결과는 기본적으로 test/output/grounded_sam2/<이미지명>/ 에 저장된다.
"""
from __future__ import annotations

import argparse
import inspect
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image


# test/sam2/ 하위 폴더에서 직접 실행해도 test/common 패키지를 찾도록 보정.
TEST_ROOT = Path(__file__).resolve().parent.parent

if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))


from common.pipeline import SegmentedItem, collect_input_images, run
from common import taxonomy as T


DINO_ID = os.getenv("DINO_ID", "IDEA-Research/grounding-dino-base")
SAM2_MODEL_ID = os.getenv("SAM2_MODEL_ID", "facebook/sam2.1-hiera-small")

BOX_THRESHOLD = float(os.getenv("BOX_THRESHOLD", "0.30"))
TEXT_THRESHOLD = float(os.getenv("TEXT_THRESHOLD", "0.25"))

SAME_CLASS_NMS_IOU = float(os.getenv("SAME_CLASS_NMS_IOU", "0.60"))
SAME_LARGE_DUPLICATE_IOU = float(
    os.getenv("SAME_LARGE_DUPLICATE_IOU", "0.88")
)
MIN_BOX_AREA_RATIO = float(os.getenv("MIN_BOX_AREA_RATIO", "0.002"))
INCLUDE_UNDERWEAR = os.getenv("INCLUDE_UNDERWEAR", "0") == "1"


@dataclass(frozen=True)
class PromptSpec:
    prompt: str
    category_large: str
    category_small: str


@dataclass
class Detection:
    box: np.ndarray
    score: float
    raw_label: str
    spec: PromptSpec


def normalize_label(value: str) -> str:
    return " ".join(
        value.lower()
        .replace(".", " ")
        .replace(",", " ")
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def build_prompt_specs() -> list[PromptSpec]:
    """common.taxonomy.CATEGORY_SMALL로 탐지 프롬프트 자동 생성."""
    specs: list[PromptSpec] = []

    for category_large, small_map in T.CATEGORY_SMALL.items():
        if category_large == "언더웨어/이너웨어" and not INCLUDE_UNDERWEAR:
            continue

        for category_small, english_prompt in small_map.items():
            prompt = normalize_label(english_prompt)
            if not prompt:
                continue

            specs.append(
                PromptSpec(
                    prompt=prompt,
                    category_large=category_large,
                    category_small=category_small,
                )
            )

    if not specs:
        raise RuntimeError("common.taxonomy.CATEGORY_SMALL에 탐지 후보가 없습니다.")

    return specs


PROMPT_SPECS = build_prompt_specs()
TEXT_LABELS = [[spec.prompt for spec in PROMPT_SPECS]]


def build_prompt_lookup() -> dict[str, PromptSpec]:
    lookup: dict[str, PromptSpec] = {}

    for spec in PROMPT_SPECS:
        lookup[normalize_label(spec.prompt)] = spec

        split_text = (
            spec.prompt
            .replace("/", " or ")
            .replace(",", " or ")
            .replace(" and ", " or ")
        )
        for part in split_text.split(" or "):
            normalized = normalize_label(part)
            if normalized:
                lookup.setdefault(normalized, spec)

    return lookup


PROMPT_LOOKUP = build_prompt_lookup()


def match_prompt_spec(raw_label: str) -> PromptSpec | None:
    normalized = normalize_label(raw_label)

    exact = PROMPT_LOOKUP.get(normalized)
    if exact is not None:
        return exact

    candidates: list[tuple[int, PromptSpec]] = []

    for key, spec in PROMPT_LOOKUP.items():
        if key in normalized or normalized in key:
            candidates.append((abs(len(key) - len(normalized)), spec))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def box_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    ax1, ay1, ax2, ay2 = [float(value) for value in box_a]
    bx1, by1, bx2, by2 = [float(value) for value in box_b]

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection

    return intersection / union if union > 0 else 0.0


def remove_duplicate_detections(
    detections: list[Detection],
) -> list[Detection]:
    """다른 대분류의 겹침은 유지하고 명확한 중복만 제거."""
    kept: list[Detection] = []

    for detection in sorted(
        detections,
        key=lambda item: item.score,
        reverse=True,
    ):
        duplicate = False

        for previous in kept:
            iou = box_iou(detection.box, previous.box)

            if (
                detection.spec.category_small
                == previous.spec.category_small
                and iou >= SAME_CLASS_NMS_IOU
            ):
                duplicate = True
                break

            if (
                detection.spec.category_large
                == previous.spec.category_large
                and iou >= SAME_LARGE_DUPLICATE_IOU
            ):
                duplicate = True
                break

        if not duplicate:
            kept.append(detection)

    return kept



def mask_containment(container_mask: np.ndarray, target_mask: np.ndarray) -> float:
    """target_mask가 container_mask 안에 포함된 비율을 계산."""
    target_area = int(target_mask.sum())
    if target_area == 0:
        return 0.0

    intersection = int(np.logical_and(container_mask, target_mask).sum())
    return intersection / target_area


def merge_duplicate_shoes(
    items: list[SegmentedItem],
) -> list[SegmentedItem]:
    """좌우 신발과 신발 한 쌍 중복 검출을 하나의 아이템으로 합친다.

    처리 기준:
    1. 신발이 0~1개면 그대로 반환
    2. 가장 큰 신발 마스크가 다른 신발 마스크를 75% 이상 포함하면
       가장 큰 마스크 하나만 유지
    3. 그렇지 않으면 모든 신발 마스크를 OR 연산으로 합쳐 한 쌍으로 저장
    """
    shoe_items = [
        item
        for item in items
        if item.category_large_hint == "신발"
    ]
    other_items = [
        item
        for item in items
        if item.category_large_hint != "신발"
    ]

    if len(shoe_items) <= 1:
        return items

    largest = max(
        shoe_items,
        key=lambda item: int(item.mask.sum()),
    )

    contained_items = [
        item
        for item in shoe_items
        if item is not largest
        and mask_containment(largest.mask, item.mask) >= 0.75
    ]

    # 큰 마스크가 좌우 신발 마스크 중 하나 이상을 충분히 포함하면
    # 이미 신발 한 쌍을 잡은 결과로 보고 큰 마스크 하나만 유지한다.
    if contained_items:
        largest.label = "shoes pair"
        return other_items + [largest]

    # 통합 마스크가 없다면 좌우 신발 마스크를 하나로 합친다.
    merged_mask = np.zeros_like(
        shoe_items[0].mask,
        dtype=bool,
    )
    for item in shoe_items:
        merged_mask = np.logical_or(merged_mask, item.mask)

    best_item = max(
        shoe_items,
        key=lambda item: item.score,
    )

    kwargs = {
        "mask": merged_mask,
        "label": "shoes pair",
        "score": best_item.score,
        "category_large_hint": "신발",
    }

    parameters = inspect.signature(SegmentedItem).parameters
    if "category_small_hint" in parameters:
        kwargs["category_small_hint"] = getattr(
            best_item,
            "category_small_hint",
            None,
        )

    merged_item = SegmentedItem(**kwargs)
    return other_items + [merged_item]

def create_segmented_item(
    mask: np.ndarray,
    detection: Detection,
) -> SegmentedItem:
    kwargs = {
        "mask": mask,
        "label": detection.raw_label,
        "score": detection.score,
        "category_large_hint": detection.spec.category_large,
    }

    # common.pipeline.SegmentedItem에 category_small_hint를 추가한 경우 자동 전달.
    parameters = inspect.signature(SegmentedItem).parameters
    if "category_small_hint" in parameters:
        kwargs["category_small_hint"] = detection.spec.category_small

    return SegmentedItem(**kwargs)


class GroundedSAM2Segmenter:
    def __init__(
        self,
        device: str,
        dino_id: str = DINO_ID,
        sam2_model_id: str = SAM2_MODEL_ID,
    ):
        from transformers import (
            AutoModelForZeroShotObjectDetection,
            AutoProcessor,
        )

        try:
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except ImportError as exc:
            raise RuntimeError(
                "공식 SAM2가 설치되지 않았습니다.\n"
                "git clone https://github.com/facebookresearch/sam2.git\n"
                "cd sam2\n"
                "pip install -e ."
            ) from exc

        self.device = torch.device(device)
        self.timings: dict[str, float | int] = {}

        started = time.perf_counter()

        self.processor = AutoProcessor.from_pretrained(dino_id)
        self.dino = (
            AutoModelForZeroShotObjectDetection
            .from_pretrained(dino_id)
            .to(self.device)
            .eval()
        )
        self.sam2 = SAM2ImagePredictor.from_pretrained(
            sam2_model_id,
            device=str(self.device),
        )

        self.timings["model_load"] = round(
            time.perf_counter() - started,
            3,
        )

    @torch.inference_mode()
    def detect(self, image: Image.Image) -> list[Detection]:
        started = time.perf_counter()

        inputs = self.processor(
            images=image,
            text=TEXT_LABELS,
            return_tensors="pt",
        ).to(self.device)

        outputs = self.dino(**inputs)

        result = self.processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=BOX_THRESHOLD,
            text_threshold=TEXT_THRESHOLD,
            target_sizes=[image.size[::-1]],
        )[0]

        width, height = image.size
        image_area = float(width * height)

        detections: list[Detection] = []

        for box_tensor, score_tensor, raw_label in zip(
            result["boxes"],
            result["scores"],
            result["labels"],
        ):
            raw_label_text = str(raw_label)
            spec = match_prompt_spec(raw_label_text)

            if spec is None:
                continue

            box = box_tensor.detach().float().cpu().numpy()
            box[0] = np.clip(box[0], 0, width - 1)
            box[1] = np.clip(box[1], 0, height - 1)
            box[2] = np.clip(box[2], 1, width)
            box[3] = np.clip(box[3], 1, height)

            box_area = max(0.0, box[2] - box[0]) * max(
                0.0,
                box[3] - box[1],
            )

            if box_area / image_area < MIN_BOX_AREA_RATIO:
                continue

            detections.append(
                Detection(
                    box=box.astype(np.float32),
                    score=float(score_tensor.detach().cpu()),
                    raw_label=raw_label_text,
                    spec=spec,
                )
            )

        detections = remove_duplicate_detections(detections)

        self.timings["dino_inference"] = round(
            time.perf_counter() - started,
            3,
        )
        self.timings["detected_before_sam2"] = len(detections)

        return detections

    def segment(
        self,
        image_path: str,
    ) -> tuple[list[SegmentedItem], dict[str, float | int]]:
        image = Image.open(image_path).convert("RGB")
        image_array = np.asarray(image)

        detections = self.detect(image)

        if not detections:
            self.timings["sam2_inference"] = 0.0
            self.timings["num_items"] = 0
            return [], dict(self.timings)

        started = time.perf_counter()
        self.sam2.set_image(image_array)

        items: list[SegmentedItem] = []

        for detection in detections:
            autocast_enabled = self.device.type == "cuda"

            with torch.inference_mode(), torch.autocast(
                device_type="cuda",
                dtype=torch.bfloat16,
                enabled=autocast_enabled,
            ):
                masks, _, _ = self.sam2.predict(
                    box=detection.box.astype(np.float32),
                    multimask_output=False,
                )

            mask = np.asarray(masks)

            if mask.ndim == 3:
                mask = mask[0]
            elif mask.ndim == 4:
                mask = mask[0, 0]

            mask = mask.astype(bool)

            if mask.shape != (image.height, image.width):
                import cv2

                mask = cv2.resize(
                    mask.astype(np.uint8),
                    image.size,
                    interpolation=cv2.INTER_NEAREST,
                ).astype(bool)

            if not mask.any():
                continue

            items.append(create_segmented_item(mask, detection))

        self.timings["sam2_inference"] = round(
            time.perf_counter() - started,
            3,
        )

        # 좌우 신발 및 신발 한 쌍 중복 검출을 하나의 아이템으로 정리.
        items = merge_duplicate_shoes(items)

        self.timings["num_items"] = len(items)

        return items, dict(self.timings)


def segment(
    image_path: str,
    device: str,
) -> tuple[list[SegmentedItem], dict[str, float | int]]:
    segmenter = GroundedSAM2Segmenter(device=device)
    return segmenter.segment(image_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "images",
        nargs="*",
        help="이미지 경로 목록. 생략 시 test/input/의 모든 jpg",
    )
    parser.add_argument("--out", default=str(TEST_ROOT / "output"))
    parser.add_argument(
        "--device",
        choices=["cuda", "cpu"],
        default=os.getenv(
            "DEVICE",
            "cuda" if torch.cuda.is_available() else "cpu",
        ),
    )
    args = parser.parse_args()

    images = args.images or collect_input_images(TEST_ROOT / "input")

    # 모델(DINO + SAM2)은 1회만 로드하고 이미지들만 순회한다.
    segmenter = GroundedSAM2Segmenter(device=args.device)

    failures = 0
    for i, image_path in enumerate(images, 1):
        print(f"\n[grounded_sam2] ({i}/{len(images)}) {image_path}")
        try:
            if not Path(image_path).exists():
                raise FileNotFoundError(f"이미지를 찾을 수 없습니다: {image_path}")
            items, timings = segmenter.segment(str(image_path))
            run(str(image_path), args.out, "grounded_sam2", items, timings)
        except Exception as e:  # 이미지 1장 실패가 전체 배치를 막지 않도록
            failures += 1
            print(f"[grounded_sam2] 실패: {image_path} -> {e}", file=sys.stderr)

    print(f"\n[grounded_sam2] 완료: 성공 {len(images) - failures} / 실패 {failures}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()