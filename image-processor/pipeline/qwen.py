from __future__ import annotations

import io
import json
from typing import Any

from PIL import Image, ImageOps

import config
from . import taxonomy as tx
from .base import EnumeratedItem, ItemEnumerator, ItemTagger, ProductImageGenerator, RetryablePipelineError


class SingleItemEnumerator(ItemEnumerator):
    def enumerate(self, image_bytes: bytes, mime: str) -> list[EnumeratedItem]:
        return [EnumeratedItem("the single fashion item", "업로드 아이템", "")]


class NormalizeGenerator(ProductImageGenerator):
    key = "qwen-tag"

    def generate(self, image_bytes: bytes, mime: str, item: EnumeratedItem) -> bytes:
        if mime in {"image/heic", "image/heif"}:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((config.ITEM_NORMALIZE_MAX_PX,) * 2, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.convert("RGB").save(output, "PNG")
            return output.getvalue()


def _prompt() -> str:
    return f"""사진 속 패션 아이템 하나를 분석해 JSON 객체만 출력하세요.
category_large: {tx.CATEGORY_LARGE}
category_small은 다음 짝만 허용: {json.dumps(tx.CATEGORY_SMALL, ensure_ascii=False)}
season: {tx.SEASONS}; style(최대 2개): {tx.STYLES}; color: {tx.COLORS}
season은 반드시 위 목록에서 1개 이상을 선택해 JSON 배열로 출력하세요.
판단이 애매해도 소재·두께·노출 정도를 기준으로 가장 가능성 높은 계절을 고르세요.
season에 빈 배열, 빈 문자열, null 또는 목록 밖 값을 출력하지 마세요.
pattern: {tx.PATTERNS}; fit: {tx.FITS}; material: {tx.MATERIALS}
sleeve: {tx.SLEEVES}; length: {tx.LENGTHS}; layer_role: {tx.LAYER_ROLES}
필드: item_name, category_large, category_small, season, style, color, pattern,
fit, material, sleeve, length, usage, layer_role, layer_order(1~3 또는 null)."""


def _json(raw: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(raw[index:])
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                pass
    return None


def normalize_tags(raw: dict[str, Any]) -> dict[str, Any]:
    enum = lambda key, values: raw.get(key) if raw.get(key) in values else ""
    enum_list = lambda key, values: ([v for v in raw.get(key, []) if v in values]
                                    if isinstance(raw.get(key, []), list) else [])
    season = raw.get("season") or []
    usage = raw.get("usage") or []
    if isinstance(season, str):
        season = [season]
    if isinstance(usage, str):
        usage = [usage]
    tags = {
        "item_name": raw.get("item_name", "") if isinstance(raw.get("item_name", ""), str) else "",
        "category_large": enum("category_large", tx.CATEGORY_LARGE),
        "category_small": enum("category_small", tx.ALL_SMALL),
        "season": [v for v in season if v in tx.SEASONS],
        "style": enum_list("style", tx.STYLES)[:2],
        "color": enum("color", tx.COLORS), "pattern": enum("pattern", tx.PATTERNS),
        "fit": enum("fit", tx.FITS), "material": enum("material", tx.MATERIALS),
        "sleeve": enum("sleeve", tx.SLEEVES), "length": enum("length", tx.LENGTHS),
        "usage": [
            v.strip()
            for v in usage
            if isinstance(v, str) and v.strip()
        ],
        "layer_role": enum("layer_role", tx.LAYER_ROLES),
        "layer_order": raw.get("layer_order") if raw.get("layer_order") in {1, 2, 3} else None,
    }
    tx.fix_category_pair(tags)
    if not tags["category_large"]:
        raise ValueError("유효한 대분류가 없습니다.")
    return tags


class QwenVLTagger(ItemTagger):
    def __init__(self) -> None:
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        self.processor = AutoProcessor.from_pretrained(
            config.QWEN_MODEL, min_pixels=config.QWEN_MIN_PIXELS, max_pixels=config.QWEN_MAX_PIXELS,
        )
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            config.QWEN_MODEL, dtype=torch.bfloat16, device_map="auto", attn_implementation="sdpa",
        ).eval()

    def tag(self, product_png: bytes) -> dict:
        import torch

        image = Image.open(io.BytesIO(product_png))
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": _prompt()},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        try:
            with torch.inference_mode():
                output = self.model.generate(**inputs, do_sample=False,
                                             max_new_tokens=config.QWEN_MAX_NEW_TOKENS)
        except torch.cuda.OutOfMemoryError as exc:
            raise RetryablePipelineError("Qwen GPU OOM") from exc
        text = self.processor.batch_decode(
            output[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True,
        )[0]
        parsed = _json(text)
        if parsed is None:
            raise ValueError("Qwen JSON 파싱 실패")
        return normalize_tags(parsed)
