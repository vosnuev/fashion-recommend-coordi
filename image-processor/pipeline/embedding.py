"""임베딩 단계 — 검색용 벡터 생성 (파이프라인 설계서에 없던 조율 추가분).

- 이미지: Marqo-FashionSigLIP (768d) — Qdrant named vector "image"
- 텍스트: bge-m3 (1024d) — 캡션(item_name+태그 문장화) → named vector "text"
벡터는 DB가 아닌 콜백 페이로드를 거쳐 Qdrant로만 간다.

임베딩 실패는 등록 자체를 막지 않는다(빈 벡터 → API가 재색인 대상으로 마킹).
"""
from __future__ import annotations

import io
import logging

import config

from .base import Embedder

logger = logging.getLogger(__name__)


class NullEmbedder(Embedder):
    """임베딩 비활성화 (WORKER_EMBED_ENABLED=0). 항상 빈 벡터."""

    version = ""

    def embed_image(self, product_png: bytes) -> list[float]:
        return []

    def embed_text(self, caption: str) -> list[float]:
        return []


class SigLIPBgeEmbedder(Embedder):
    """FashionSigLIP(이미지) + bge-m3(텍스트). 모델은 최초 사용 시 lazy 로드."""

    def __init__(self) -> None:
        self.version = config.EMBEDDING_VERSION
        self._image_model = None
        self._preprocess = None
        self._text_model = None

    # ── lazy 로더 ──
    def _load_image_model(self):
        if self._image_model is None:
            import open_clip
            import torch

            model, _, preprocess = open_clip.create_model_and_transforms(
                config.IMAGE_EMBED_MODEL
            )
            device = config.DEVICE or ("cuda" if torch.cuda.is_available() else "cpu")
            self._image_model = model.eval().to(device)
            self._preprocess = preprocess
            self._device = device
        return self._image_model

    def _load_text_model(self):
        if self._text_model is None:
            from sentence_transformers import SentenceTransformer

            self._text_model = SentenceTransformer(config.TEXT_EMBED_MODEL)
        return self._text_model

    # ── Embedder 구현 ──
    def embed_image(self, product_png: bytes) -> list[float]:
        try:
            import torch
            from PIL import Image

            model = self._load_image_model()
            img = Image.open(io.BytesIO(product_png)).convert("RGB")
            x = self._preprocess(img).unsqueeze(0).to(self._device)
            with torch.no_grad():
                v = model.encode_image(x)
                v = v / v.norm(dim=-1, keepdim=True)
            return v[0].cpu().tolist()
        except Exception:  # noqa: BLE001 — 임베딩 실패는 등록을 막지 않는다
            logger.exception("이미지 임베딩 실패")
            return []

    def embed_text(self, caption: str) -> list[float]:
        try:
            model = self._load_text_model()
            return model.encode(caption, normalize_embeddings=True).tolist()
        except Exception:  # noqa: BLE001
            logger.exception("텍스트 임베딩 실패")
            return []


def caption_from_tags(tags: dict) -> str:
    """태그를 한국어 검색 문장으로 직렬화 — 텍스트 임베딩 입력."""
    parts = [tags.get("item_name", "")]
    for key in ("category_large", "category_small", "color", "pattern",
                "fit", "material", "sleeve", "length"):
        if tags.get(key):
            parts.append(str(tags[key]))
    parts.extend(tags.get("style") or [])
    parts.extend(tags.get("season") or [])
    return " ".join(p for p in parts if p)
