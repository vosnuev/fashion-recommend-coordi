"""Marqo Fashion SigLIP 임베딩 래퍼 (product_indexer·old 공용).

- 이미지·텍스트를 같은 공간에 임베딩하는 패션 도메인 특화 모델.
- open_clip으로 HuggingFace 허브에서 로드한다.
- RTX 3090 등 CUDA 환경에서는 fp16 autocast로 추론한다.

두 컨테이너가 공유하므로 설정 모듈을 import 하지 않고 model_id/device를
호출자가 명시적으로 넘긴다.

주의: 텍스트 인코더는 영어 중심으로 학습되어 한국어 임베딩 품질에는
한계가 있다. 한국어 텍스트 검색 품질이 중요해지면 다국어 모델
(예: multilingual-CLIP) 도입을 검토한다 (README 참고).
"""

from __future__ import annotations

import logging

import numpy as np
import torch

logger = logging.getLogger(__name__)


class FashionSigLIPEmbedder:
    def __init__(self, model_id: str, device: str = "auto"):
        import open_clip  # 무거운 임포트라 사용 시점에 로드

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.device_type = torch.device(device).type
        logger.info("임베딩 모델 로드: %s (device=%s)", model_id, device)

        self.model, _, self.preprocess = open_clip.create_model_and_transforms(model_id)
        self.tokenizer = open_clip.get_tokenizer(model_id)
        self.model = self.model.to(device).eval()

        # 벡터 차원은 더미 입력으로 확정한다 (모델 구현별 속성명 차이 회피)
        with torch.no_grad():
            probe = self.model.encode_text(self.tokenizer(["probe"]).to(device))
        self.dim = int(probe.shape[-1])
        logger.info("임베딩 차원: %d", self.dim)

    @torch.no_grad()
    def encode_images(self, images: list) -> np.ndarray:
        """PIL 이미지 목록 → L2 정규화된 (N, dim) float32 배열."""
        batch = torch.stack([self.preprocess(img) for img in images]).to(self.device)
        with torch.autocast(
            self.device_type,
            enabled=self.device_type == "cuda",
        ):
            features = self.model.encode_image(batch)
        return self._normalize(features)

    @torch.no_grad()
    def encode_texts(self, texts: list[str]) -> np.ndarray:
        """텍스트 목록 → L2 정규화된 (N, dim) float32 배열.

        토크나이저가 컨텍스트 길이(64 토큰) 초과분을 자동으로 잘라낸다.
        """
        tokens = self.tokenizer(texts).to(self.device)
        with torch.autocast(
            self.device_type,
            enabled=self.device_type == "cuda",
        ):
            features = self.model.encode_text(tokens)
        return self._normalize(features)

    @staticmethod
    def _normalize(features: torch.Tensor) -> np.ndarray:
        features = features / features.norm(dim=-1, keepdim=True)
        return features.float().cpu().numpy()
