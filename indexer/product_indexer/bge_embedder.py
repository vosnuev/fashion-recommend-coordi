"""BGE-M3 dense 텍스트 임베딩 래퍼.

transformers 버전 제약 (중요):
transformers 4.52.0이 `check_torch_load_is_safe()`를 도입해, `.bin`(pickle)
체크포인트를 `torch.load`로 읽을 때 torch>=2.6을 강제한다 (CVE-2025-32434).
베이스 이미지의 torch는 2.3.1이다.

BAAI/bge-m3 저장소에는 safetensors 가중치가 **없다** — 루트에 `pytorch_model.bin`
하나뿐이라(2026-07 기준) `use_safetensors=True`로 우회할 수 없다. 그래서
util/requirements.txt에서 `transformers<4.52`로 고정해 이 검사 자체를 피한다.

핀을 올리려면 먼저 다음 중 하나가 필요하다:
1. 베이스 이미지를 torch>=2.6으로 올린다 (numpy<2 핀과 open_clip·
   marqo-fashionSigLIP 동작을 함께 재검증해야 한다), 또는
2. safetensors 가중치가 있는 텍스트 임베딩 모델로 교체한다
   (`PRODUCT_TEXT_EMBED_MODEL`), 또는
3. bge-m3를 safetensors로 변환해 사내 저장소에 올리고 그쪽을 바라보게 한다.
"""

from __future__ import annotations

import logging

import numpy as np
import torch

logger = logging.getLogger(__name__)


class BgeM3Embedder:
    def __init__(
        self,
        model_id: str,
        *,
        revision: str | None = None,
        device: str = "auto",
        max_length: int = 512,
    ):
        from sentence_transformers import SentenceTransformer

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(
            "BGE-M3 모델 로드: %s (revision=%s, device=%s)",
            model_id,
            revision or "default",
            device,
        )
        # use_safetensors를 강제하지 않는다 — bge-m3에는 safetensors가 없어
        # 강제하면 "does not appear to have a file named model.safetensors"로
        # 죽는다. transformers<4.52 핀이 .bin 로드를 가능하게 해준다.
        self.model = SentenceTransformer(
            model_id,
            revision=revision,
            device=device,
        )
        self.model.max_seq_length = max_length
        dimension = self.model.get_sentence_embedding_dimension()
        if dimension is None:
            probe = self.model.encode(["dimension probe"])
            dimension = int(probe.shape[-1])
        self.dim = int(dimension)

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        vectors = self.model.encode(
            texts,
            batch_size=max(1, len(texts)),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)
