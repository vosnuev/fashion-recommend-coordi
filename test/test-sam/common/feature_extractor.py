"""Marqo-FashionSigLIP 제로샷 특징 추출기.

세그멘테이션으로 잘라낸 아이템 크롭(흰 배경) 1장을 받아,
Confluence 태그 체계(taxonomy.py) 스키마의 특징 dict를 반환한다.

- 이미지 임베딩은 크롭당 1회만 계산하고 필드별 텍스트 후보와 비교한다.
- 텍스트 후보 임베딩은 필드 단위로 캐시한다 (반복 호출 시 재계산 방지).
"""
from __future__ import annotations

import os

import torch
from PIL import Image

from . import taxonomy as T

MODEL_ID = "hf-hub:Marqo/marqo-fashionSigLIP"


class FashionFeatureExtractor:
    def __init__(self, device: str | None = None):
        import open_clip  # 지연 import: 세그 전용 실행 시 불필요한 로드 방지

        self.device = device or os.getenv(
            "DEVICE", "cuda" if torch.cuda.is_available() else "cpu"
        )
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(MODEL_ID)
        self.tokenizer = open_clip.get_tokenizer(MODEL_ID)
        self.model.eval().to(self.device)
        self._text_cache: dict[str, torch.Tensor] = {}

    # ── 저수준 ──────────────────────────────────────────
    @torch.no_grad()
    def _text_feats(self, cache_key: str, prompts: list[str]) -> torch.Tensor:
        if cache_key not in self._text_cache:
            tokens = self.tokenizer(prompts).to(self.device)
            feats = self.model.encode_text(tokens)
            self._text_cache[cache_key] = feats / feats.norm(dim=-1, keepdim=True)
        return self._text_cache[cache_key]

    @torch.no_grad()
    def image_feats(self, image: Image.Image) -> torch.Tensor:
        t = self.preprocess(image.convert("RGB")).unsqueeze(0).to(self.device)
        f = self.model.encode_image(t)
        return f / f.norm(dim=-1, keepdim=True)

    def classify(
        self,
        img_f: torch.Tensor,
        field: str,
        candidates: dict[str, str],
        top_k: int = 1,
        min_prob: float = 0.0,
    ) -> list[tuple[str, float]]:
        """이미지 피처 vs 후보 프롬프트 → (한글 라벨, 확률) 상위 top_k."""
        labels = list(candidates)
        prompts = [f"a photo of {candidates[lb]}" for lb in labels]
        tf = self._text_feats(field, prompts)
        probs = (100.0 * img_f @ tf.T).softmax(dim=-1)[0]
        order = probs.argsort(descending=True)[:top_k]
        return [
            (labels[i], float(probs[i])) for i in order if float(probs[i]) >= min_prob
        ]

    # ── 스키마 추출 ─────────────────────────────────────
    def extract(
        self, crop: Image.Image, category_large_hint: str | None = None
    ) -> dict:
        """크롭 1장 → Confluence 스키마 특징 dict.

        category_large_hint: 세그멘테이션 모델의 클래스에서 유도한 대분류.
        힌트가 있으면 대분류 분류를 건너뛰어 오류 전파를 줄인다.
        """
        img_f = self.image_feats(crop)

        if category_large_hint in T.CATEGORY_LARGE:
            cat_l, cat_l_p = category_large_hint, 1.0
        else:
            (cat_l, cat_l_p), = self.classify(img_f, "category_large", T.CATEGORY_LARGE)

        (cat_s, cat_s_p), = self.classify(
            img_f, f"category_small:{cat_l}", T.CATEGORY_SMALL[cat_l]
        )

        colors = self.classify(img_f, "color", T.COLORS, top_k=2, min_prob=0.20)
        (pattern, _), = self.classify(img_f, "pattern", T.PATTERNS)
        styles = self.classify(img_f, "style", T.STYLES, top_k=2, min_prob=0.15)
        (material, _), = self.classify(img_f, "material", T.MATERIALS)

        fit = sleeve = length = None
        if cat_l in T.CLOTHING_LARGE:
            (fit, _), = self.classify(img_f, "fit", T.FITS)
            (length, _), = self.classify(img_f, "length", T.LENGTHS)
        if cat_l in T.SLEEVE_TARGET:
            (sleeve, _), = self.classify(img_f, "sleeve", T.SLEEVES)

        layer_role, layer_order = T.infer_layer(cat_l, cat_s, sleeve)
        season = T.infer_season(cat_l, cat_s, sleeve, material)

        color_labels = [c for c, _ in colors] or ["멀티"]
        item_name = " ".join(
            x for x in [color_labels[0], fit, sleeve, cat_s.split("/")[0]] if x
        )

        return {
            "item_name": item_name,
            "category_large": cat_l,
            "category_small": cat_s,
            "season": season,
            "style": [s for s, _ in styles] or ["베이직"],
            "color": color_labels[0] if len(color_labels) == 1 else color_labels,
            "pattern": pattern,
            "fit": fit,
            "material": material,
            "sleeve": sleeve,
            "length": length,
            "usage": list(T.DEFAULT_USAGE),
            "layer_role": layer_role,
            "layer_order": layer_order,
            "_confidence": {
                "category_large": round(cat_l_p, 4),
                "category_small": round(cat_s_p, 4),
                "color": [(c, round(p, 4)) for c, p in colors],
                "style": [(s, round(p, 4)) for s, p in styles],
            },
        }
