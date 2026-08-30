"""골든셋 이미지·텍스트 임베딩 백엔드."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from .artifacts import read_json, read_jsonl, write_json
from .config import GoldenSettings


class ImageEmbeddingBackend(Protocol):
    name: str
    dim: int

    def encode_paths(self, paths: list[Path]) -> np.ndarray: ...


class TextEmbeddingBackend(Protocol):
    name: str
    dim: int

    def encode_texts(self, texts: list[str]) -> np.ndarray: ...


class FashionSigLIPBackend:
    """기존 indexer의 FashionSigLIP 설정과 동일한 오프라인 배치 백엔드."""

    def __init__(self, model_id: str, device: str = "auto") -> None:
        import open_clip
        import torch

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._torch = torch
        self._device = device
        self._device_type = torch.device(device).type
        self._model, _, self._preprocess = open_clip.create_model_and_transforms(
            model_id
        )
        self._model = self._model.to(device).eval()
        self.name = model_id
        # 실제 차원은 첫 배치에서 확정한다.
        self.dim = 0

    def encode_paths(self, paths: list[Path]) -> np.ndarray:
        if not paths:
            return np.empty((0, self.dim), dtype=np.float32)
        images = []
        for path in paths:
            with Image.open(path) as image:
                images.append(self._preprocess(image.convert("RGB")))
        batch = self._torch.stack(images).to(self._device)
        with (
            self._torch.no_grad(),
            self._torch.autocast(
                self._device_type,
                enabled=self._device_type == "cuda",
            ),
        ):
            values = self._model.encode_image(batch)
        values = values / values.norm(dim=-1, keepdim=True)
        result = values.float().cpu().numpy()
        self.dim = int(result.shape[1])
        return result


class BgeM3Backend:
    def __init__(self, model_id: str, device: str = "auto") -> None:
        from sentence_transformers import SentenceTransformer

        kwargs = {} if device == "auto" else {"device": device}
        self._model = SentenceTransformer(model_id, **kwargs)
        # indexer/product_indexer의 BgeM3Embedder와 같은 상한을 쓴다.
        self._model.max_seq_length = 512
        self.name = model_id
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dim), dtype=np.float32)
        return np.asarray(
            self._model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )


class DeterministicImageBackend:
    """네트워크·GPU 없는 테스트와 파이프라인 dry-run용 백엔드."""

    name = "deterministic-test-image-v1"

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim

    def encode_paths(self, paths: list[Path]) -> np.ndarray:
        rows = []
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).digest()
            seed = int.from_bytes(digest[:8], "big", signed=False)
            rng = np.random.default_rng(seed)
            vector = rng.normal(size=self.dim).astype(np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.vstack(rows) if rows else np.empty((0, self.dim), dtype=np.float32)


class DeterministicTextBackend:
    name = "deterministic-test-text-v1"

    def __init__(self, dim: int = 48) -> None:
        self.dim = dim

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        rows = []
        for value in texts:
            digest = hashlib.sha256(value.encode("utf-8")).digest()
            seed = int.from_bytes(digest[:8], "big", signed=False)
            rng = np.random.default_rng(seed)
            vector = rng.normal(size=self.dim).astype(np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.vstack(rows) if rows else np.empty((0, self.dim), dtype=np.float32)


def build_image_backend(
    settings: GoldenSettings, backend_name: str
) -> ImageEmbeddingBackend:
    if backend_name == "deterministic":
        return DeterministicImageBackend()
    if backend_name == "fashion":
        return FashionSigLIPBackend(settings.fashion_model_id, settings.device)
    raise ValueError(f"지원하지 않는 이미지 임베딩 백엔드: {backend_name}")


def build_text_backend(
    settings: GoldenSettings, backend_name: str
) -> TextEmbeddingBackend:
    if backend_name == "deterministic":
        return DeterministicTextBackend()
    if backend_name == "bge":
        return BgeM3Backend(settings.text_model_id, settings.device)
    raise ValueError(f"지원하지 않는 텍스트 임베딩 백엔드: {backend_name}")


def embed_manifest_images(
    *,
    run_dir: Path,
    settings: GoldenSettings,
    backend_name: str = "fashion",
) -> tuple[list[str], np.ndarray, str]:
    """코디 원본을 임베딩한다. 이미 임베딩된 sha는 재계산하지 않는다.

    컨테이너가 주기적으로 S3를 다시 스캔하는 구조라, 매번 전량 재임베딩하면
    GPU 시간이 그대로 낭비된다. sha256이 같고 모델 버전이 같으면 기존 벡터를
    그대로 재사용한다.
    """
    images = [
        row
        for row in read_jsonl(run_dir / "images.jsonl")
        if row.get("duplicate_kind") != "exact"
    ]
    if not images:
        raise ValueError("임베딩할 이미지가 manifest에 없습니다.")

    npz_path = run_dir / "image_embeddings.npz"
    meta_path = run_dir / "image_embeddings.meta.json"
    expected_model = _expected_model(settings, backend_name)

    cached: dict[str, np.ndarray] = {}
    if npz_path.exists() and meta_path.exists():
        try:
            cached_model = str(read_json(meta_path).get("model", ""))
            # 모델이 바뀌었으면 캐시를 통째로 버린다 (벡터 공간이 다르다).
            if cached_model == expected_model:
                _, prior_shas, prior_vectors = _load_with_shas(npz_path)
                cached = {
                    sha: prior_vectors[index]
                    for index, sha in enumerate(prior_shas)
                }
        except (KeyError, ValueError, OSError):
            # 예전 포맷이거나 깨진 캐시면 조용히 전량 재계산한다.
            cached = {}

    pending = [row for row in images if str(row["image_sha256"]) not in cached]

    fresh: dict[str, np.ndarray] = {}
    model_name = expected_model
    if pending:
        backend = build_image_backend(settings, backend_name)
        model_name = backend.name
        batch_size = max(1, settings.embedding_batch_size)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            vectors = backend.encode_paths(
                [Path(str(row["local_path"])) for row in batch]
            )
            for row, vector in zip(batch, vectors, strict=True):
                fresh[str(row["image_sha256"])] = vector

    lookup = {**cached, **fresh}
    ids = [str(row["golden_id"]) for row in images]
    shas = [str(row["image_sha256"]) for row in images]
    vectors = np.vstack([lookup[sha] for sha in shas]).astype(np.float32)

    np.savez_compressed(
        npz_path,
        ids=np.asarray(ids),
        shas=np.asarray(shas),
        vectors=vectors,
    )
    write_json(
        meta_path,
        {
            "model": model_name,
            "dim": int(vectors.shape[1]),
            "count": len(ids),
            "reused": len(ids) - len(fresh),
            "embedded": len(fresh),
        },
    )
    return ids, vectors, model_name


def _expected_model(settings: GoldenSettings, backend_name: str) -> str:
    """백엔드를 로드하지 않고 모델 이름만 알아낸다 (캐시 유효성 판정용).

    FashionSigLIPBackend.name은 model_id를 그대로 쓰므로 값이 일치한다.
    """
    if backend_name == "deterministic":
        return DeterministicImageBackend.name
    if backend_name == "fashion":
        return settings.fashion_model_id
    raise ValueError(f"지원하지 않는 이미지 임베딩 백엔드: {backend_name}")


def _load_with_shas(path: Path) -> tuple[list[str], list[str], np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        ids = [str(value) for value in data["ids"].tolist()]
        shas = [str(value) for value in data["shas"].tolist()]
        vectors = np.asarray(data["vectors"], dtype=np.float32)
    return ids, shas, vectors


def load_embeddings(path: Path) -> tuple[list[str], np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        ids = [str(value) for value in data["ids"].tolist()]
        vectors = np.asarray(data["vectors"], dtype=np.float32)
    return ids, vectors
