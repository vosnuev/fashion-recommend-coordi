"""환경변수 기반 골든셋 파이프라인 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: 리포 루트 (ml/golden_set/config.py → ../../)
REPO_ROOT = Path(__file__).resolve().parents[2]

GOLDEN_DATASET_STATUSES = frozenset({"PILOT", "DRAFT", "ACTIVE", "ARCHIVED"})


def load_project_env() -> None:
    """루트 .env를 기존 프로세스 환경보다 낮은 우선순위로 읽는다.

    컨테이너에서는 compose가 env_file로 값을 직접 주입하므로 파일이 없는 게
    정상이다. override=False라 주입된 값을 파일이 덮어쓰지 않는다.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    explicit = os.getenv("ENV_FILE")
    path = Path(explicit) if explicit else REPO_ROOT / ".env"
    if path.exists():
        load_dotenv(path, override=False)


def _int(name: str, default: str) -> int:
    return int(os.getenv(name, default))


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y"}


def normalize_dataset_status(value: object, *, default: str = "PILOT") -> str:
    """골든셋 상태를 DB의 GoldenDataset.Status 계약에 맞춰 정규화한다."""
    status = str(value or default).strip().upper()
    if status not in GOLDEN_DATASET_STATUSES:
        allowed = ", ".join(sorted(GOLDEN_DATASET_STATUSES))
        raise ValueError(
            f"지원하지 않는 골든셋 상태입니다: {status!r} (허용값: {allowed})"
        )
    return status


@dataclass(frozen=True)
class GoldenSettings:
    # ── 분석·합성 LLM ──
    gemini_api_key: str
    gemini_api_base_url: str
    gemini_model: str
    gemini_timeout_seconds: int
    max_multimodal_calls: int

    # ── 임베딩 ──
    fashion_model_id: str
    text_model_id: str
    device: str
    embedding_batch_size: int

    # ── S3 원본·산출물 ──
    # 아래는 전부 기본값을 둔다. from_env()가 항상 전부 채우지만, 테스트와
    # 스크립트가 관심 있는 필드만 지정해 만들 수 있어야 한다.
    s3_bucket: str = ""
    s3_source_prefix: str = "goldenset/source"
    s3_output_prefix: str = "goldenset/derived"
    s3_metadata_key: str = ""

    # ── 실행 ──
    run_root: Path = REPO_ROOT / "local/golden-runs"
    dataset_name: str = "team-golden"
    dataset_version: str = "v1"
    dataset_status: str = "PILOT"
    item_pipeline: str = "gemini-edit"
    item_embedding_version: str = ""
    anchor_exposable: bool = False
    auto_index: bool = True
    index_only_missing: bool = True
    scan_interval_seconds: int = 0
    image_processor_path: Path = REPO_ROOT / "image-processor"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dataset_status",
            normalize_dataset_status(self.dataset_status),
        )

    @classmethod
    def from_env(cls) -> GoldenSettings:
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_api_base_url=os.getenv(
                "GEMINI_API_BASE_URL",
                "https://generativelanguage.googleapis.com",
            ).rstrip("/"),
            gemini_model=os.getenv("GOLDEN_GEMINI_MODEL")
            or os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
            gemini_timeout_seconds=_int("GOLDEN_GEMINI_TIMEOUT_SECONDS", "90"),
            max_multimodal_calls=_int("GOLDEN_MAX_MULTIMODAL_CALLS", "15"),
            fashion_model_id=os.getenv("GOLDEN_FASHION_EMBED_MODEL")
            or os.getenv("FASHION_EMBED_MODEL", "hf-hub:Marqo/marqo-fashionSigLIP"),
            text_model_id=os.getenv("GOLDEN_TEXT_EMBED_MODEL")
            or os.getenv("TEXT_EMBED_MODEL", "BAAI/bge-m3"),
            device=os.getenv("GOLDEN_DEVICE", "auto"),
            embedding_batch_size=_int("GOLDEN_EMBED_BATCH_SIZE", "16"),
            s3_bucket=os.getenv("GOLDEN_S3_BUCKET", "").strip(),
            s3_source_prefix=os.getenv(
                "GOLDEN_S3_SOURCE_PREFIX", "goldenset/source"
            ).strip("/"),
            s3_output_prefix=os.getenv(
                "GOLDEN_S3_OUTPUT_PREFIX", "goldenset/derived"
            ).strip("/"),
            # 비우면 메타데이터 없이 진행한다 (스타일·TPO는 UNAVAILABLE 처리).
            s3_metadata_key=os.getenv("GOLDEN_S3_METADATA_KEY", "").strip(),
            run_root=Path(os.getenv("GOLDEN_RUN_ROOT", str(REPO_ROOT / "local/golden-runs"))),
            dataset_name=os.getenv("GOLDEN_DATASET_NAME", "team-golden"),
            dataset_version=os.getenv("GOLDEN_DATASET_VERSION", "v1"),
            dataset_status=normalize_dataset_status(
                os.getenv("GOLDEN_DATASET_STATUS", "PILOT")
            ),
            # image-processor의 pipeline 레지스트리 키. sam3-crop이 등록되면
            # 코드 변경 없이 이 값만 바꿔 교체한다.
            item_pipeline=os.getenv("GOLDEN_ITEM_PIPELINE")
            or os.getenv("WORKER_PIPELINE", "gemini-edit"),
            # 아이템 임베딩에 찍을 버전 라벨. 비우면 파이프라인 임베더의 값을
            # 쓰는데, 그건 image-processor의 WARDROBE_EMBEDDING_VERSION이라
            # 골든 아이템에 옷장 이름표가 찍힌다. 지금은 같은 모델이라 맞는
            # 값이지만 갈라지는 순간 거짓말이 되므로 별도로 둔다.
            item_embedding_version=os.getenv("GOLDEN_EMBEDDING_VERSION", "").strip(),
            # 골든 원본을 사용자 응답에 노출할지. 기본은 비노출(사용권 보수적).
            anchor_exposable=_bool("GOLDEN_ANCHOR_EXPOSABLE", "0"),
            # 임베딩 후 Qdrant 적재까지 자동으로 이어갈지.
            auto_index=_bool("GOLDEN_AUTO_INDEX", "1"),
            # Qdrant에 이미 있는 포인트는 건너뛴다. 이번 실행에서 새로 처리한
            # 코디는 내용이 바뀌었으므로 이 설정과 무관하게 항상 덮어쓴다.
            # 0으로 두면 매번 전량 upsert (결과는 같고 느리다).
            index_only_missing=_bool("GOLDEN_INDEX_ONLY_MISSING", "1"),
            # 0이면 1회 처리 후 종료, 양수면 그 간격(초)으로 계속 스캔한다.
            scan_interval_seconds=_int("GOLDEN_SCAN_INTERVAL_SECONDS", "0"),
            image_processor_path=Path(
                os.getenv("GOLDEN_IMAGE_PROCESSOR_PATH")
                or (REPO_ROOT / "image-processor")
            ),
        )

    @property
    def run_dir(self) -> Path:
        """데이터셋 버전 하나가 쓰는 run 디렉터리."""
        return self.run_root / self.dataset_version

    def source_prefix(self) -> str:
        return f"{self.s3_source_prefix}/" if self.s3_source_prefix else ""

    def derived_prefix(self) -> str:
        """버전별 파생 산출물 prefix (아이템 이미지·per-image manifest)."""
        base = f"{self.s3_output_prefix}/" if self.s3_output_prefix else ""
        return f"{base}{self.dataset_version}"

    def require_bucket(self) -> str:
        if not self.s3_bucket:
            raise ValueError(
                "GOLDEN_S3_BUCKET이 비어 있습니다. 골든셋 원본 버킷을 지정하세요."
            )
        return self.s3_bucket
