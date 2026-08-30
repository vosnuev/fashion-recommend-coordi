"""세그멘테이션 결과 공통 후처리·실행 파이프라인.

세 테스트 파일(test_segformer / test_yolov8_deepfashion2 / test_grounded_sam2)이
공유한다. 각 테스트는 `segment() -> list[SegmentedItem]`만 구현하면 된다.

흐름: 마스크 정리 → 흰 배경 합성 → bbox 크롭 → FashionSigLIP 특징 추출
     → output/<모델>/<이미지명>/ 에 item PNG + items.json + overlay 저장
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

MIN_AREA_RATIO = 0.005  # 전체 이미지 대비 최소 마스크 면적(오검출 컷)
CROP_PAD = 0.05         # bbox 크롭 여백 비율

IMAGE_EXTS = {".jpg", ".jpeg"}


def collect_input_images(input_dir: str | Path) -> list[str]:
    """test/input/ 폴더의 jpg 이미지 목록을 정렬해 반환. 없으면 즉시 종료."""
    input_dir = Path(input_dir)
    images = sorted(
        str(p) for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ) if input_dir.is_dir() else []
    if not images:
        raise SystemExit(
            f"입력 이미지가 없습니다: {input_dir} (jpg 파일을 넣어주세요)"
        )
    return images


@dataclass
class SegmentedItem:
    mask: np.ndarray                      # (H, W) bool
    label: str                            # 세그 모델의 원시 클래스명
    score: float = 1.0
    category_large_hint: str | None = None
    bbox: tuple[int, int, int, int] = field(default=None)  # 후처리에서 채움


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


_EXTRACTOR = None


def _get_extractor():
    """FashionSigLIP 특징 추출기를 프로세스당 1회만 로드 (일괄 처리 시 재로드 방지)."""
    global _EXTRACTOR
    if _EXTRACTOR is None:
        from .feature_extractor import FashionFeatureExtractor  # 지연 import (모델 로드 비용)

        _EXTRACTOR = FashionFeatureExtractor()
    return _EXTRACTOR


def run(image_path: str, out_root: str, model_name: str,
        items: list[SegmentedItem], timings: dict[str, float]) -> Path:
    """세그멘테이션 결과 → 크롭 저장 + 특징 추출 + json 리포트."""

    image = np.array(Image.open(image_path).convert("RGB"))
    total_px = image.shape[0] * image.shape[1]
    out_dir = Path(out_root) / model_name / Path(image_path).stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # 면적 필터 + 마스크 정리
    kept: list[SegmentedItem] = []
    for it in items:
        it.mask = clean_mask(it.mask)
        if it.mask.sum() / total_px >= MIN_AREA_RATIO:
            kept.append(it)

    t0 = time.perf_counter()
    extractor = _get_extractor()
    timings["feature_model_load"] = round(time.perf_counter() - t0, 3)  # warm이면 ~0

    results = []
    t0 = time.perf_counter()
    for i, it in enumerate(kept):
        crop, it.bbox = white_bg_crop(image, it.mask)
        feats = extractor.extract(crop, it.category_large_hint)
        fname = f"item_{i:02d}_{feats['category_large']}.png".replace("/", "_")
        crop.save(out_dir / fname)
        results.append({
            **feats,
            "_seg": {
                "model": model_name, "raw_label": it.label,
                "score": round(it.score, 4), "bbox": it.bbox,
                "mask_area_ratio": round(float(it.mask.sum()) / total_px, 4),
            },
            "_image_file": fname,
        })
    timings["feature_extract"] = round(time.perf_counter() - t0, 3)

    save_overlay(image, kept, out_dir / "_overlay.jpg")
    report = {
        "source_image": str(image_path),
        "model": model_name,
        "num_items": len(results),
        "timings_sec": timings,
        "items": results,
    }
    (out_dir / "items.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[{model_name}] {len(results)} items -> {out_dir}")
    print(json.dumps(timings, indent=2))
    return out_dir
