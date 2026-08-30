"""신규 네이버·11번가 상품 → 이미지+텍스트 임베딩 → Qdrant worker.

collector가 product_embedding_job에 등록한 신규 상품만 처리한다. 기존 DB 상품은
별도 백필 명령을 만들기 전까지 이 worker의 대상이 되지 않는다.

쇼핑몰 분리 정책:
- 작업 큐는 source 컬럼을 가진 공용 테이블(product_embedding_job) 하나를 쓴다.
- S3 보존 경로와 Qdrant 컬렉션만 쇼핑몰별로 나눈다(product_config 참고).
- `--source`로 한 쇼핑몰만 처리할 수 있어 naver drain과 eleven drain을 동시에
  띄워도 서로의 작업을 선점하지 않는다.
- 한 배치 안에서 이미지 다운로드·S3 입출력은 스레드 풀로 병렬 처리하고,
  GPU 임베딩은 모델을 한 번만 로드해 배치로 묶어 돌린다.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import boto3
import requests
from botocore.exceptions import ClientError
from requests.adapters import HTTPAdapter

from util.embedder import FashionSigLIPEmbedder

from . import product_config as config
from .bge_embedder import BgeM3Embedder
from .product_assets import (
    InvalidProductImage,
    PreparedImage,
    StoredProductImageUnavailable,
    download_and_store_image,
    load_stored_image,
)
from .product_catalog_api import ProductCatalogApiClient
from .product_qdrant import (
    build_point,
    ensure_collection,
    make_client,
    upsert_points,
)
from .product_text import build_product_payload, serialize_product_text

logger = logging.getLogger("product_indexer")


@dataclass
class PreparedProduct:
    job: dict[str, Any]
    product: dict[str, Any]
    image: PreparedImage
    text: str


def _retry_delay(job: dict[str, Any]) -> int:
    exponent = max(0, int(job["attempt_count"]) - 1)
    return min(
        24 * 60 * 60,
        config.RETRY_BASE_SECONDS * (2**exponent),
    )


def _is_transient_http_error(error: requests.HTTPError) -> bool:
    response = error.response
    if response is None:
        return True
    status_code = response.status_code
    return status_code in {408, 425, 429} or status_code >= 500


def _is_missing_s3_object(error: ClientError) -> bool:
    code = str(error.response.get("Error", {}).get("Code", ""))
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NotFound"} or status == 404


class ProductIndexer:
    # 처리 대상 쇼핑몰. None이면 전체. 인스턴스가 __init__을 거치지 않는 경우
    # (테스트의 __new__ 등)에도 안전하도록 클래스 기본값으로 둔다.
    source: str | None = None

    def __init__(
        self,
        *,
        catalog_client: ProductCatalogApiClient | None = None,
        reset_stale: bool = True,
        source: str | None = None,
    ) -> None:
        config.validate_runtime_config()
        # None이면 모든 쇼핑몰을 처리한다. 지정하면 해당 source 작업만 선점하므로
        # naver drain과 eleven drain을 동시에 띄워도 서로 간섭하지 않는다.
        self.source = config.require_source(source) if source else None
        self.catalog = catalog_client or ProductCatalogApiClient()
        if reset_stale:
            state = self.catalog.status(
                config.EMBEDDING_VERSION,
                reset_stale=True,
                stale_job_minutes=config.STALE_JOB_MINUTES,
                source=self.source,
            )
            stale_count = int(state.get("reset_stale_count", 0))
            if stale_count:
                logger.warning(
                    "stale 임베딩 작업 %d건을 pending으로 복구",
                    stale_count,
                )

        self.image_embedder = FashionSigLIPEmbedder(
            model_id=config.IMAGE_MODEL_ID,
            device=config.DEVICE,
        )
        self.text_embedder = BgeM3Embedder(
            config.TEXT_MODEL_ID,
            revision=config.TEXT_MODEL_REVISION,
            device=config.DEVICE,
            max_length=config.TEXT_MAX_LENGTH,
        )
        self.qdrant = make_client(config.QDRANT_URL, config.QDRANT_API_KEY)
        # 쇼핑몰별 컬렉션을 각각 준비한다. 벡터 차원은 같지만 컬렉션은 독립이다.
        for source in config.SOURCES:
            ensure_collection(
                self.qdrant,
                config.qdrant_collection(source),
                image_dim=self.image_embedder.dim,
                text_dim=self.text_embedder.dim,
            )
        self.s3 = boto3.client("s3")
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "SKN28-product-indexer/1.0"
        # 이미지 다운로드를 여러 스레드에서 동시에 하므로 커넥션 풀을 워커 수에
        # 맞춘다 (기본 10이면 풀 부족 경고가 뜬다).
        adapter = HTTPAdapter(
            pool_connections=config.IMAGE_WORKERS,
            pool_maxsize=config.IMAGE_WORKERS,
        )
        self.http.mount("http://", adapter)
        self.http.mount("https://", adapter)

    def close(self) -> None:
        self.http.close()
        self.catalog.close()

    def _fail(
        self,
        job: dict[str, Any],
        error: Exception | str,
        *,
        transient: bool,
    ) -> None:
        message = str(error)
        next_status = self.catalog.mark_failure(
            job,
            message,
            max_retries=config.MAX_RETRIES,
            retry_delay_seconds=_retry_delay(job),
            transient=transient,
        )
        if next_status == "pending":
            logger.warning(
                "임베딩 재시도 예약: %s:%s attempt=%s error=%s",
                job["source"],
                job["external_product_id"],
                job["attempt_count"],
                message,
            )
        elif next_status == "failed":
            logger.error(
                "임베딩 최종 실패: %s:%s attempts=%s error=%s",
                job["source"],
                job["external_product_id"],
                job["attempt_count"],
                message,
            )

    def _resolve_image(
        self,
        job: dict[str, Any],
        product: dict[str, Any],
    ) -> tuple[PreparedImage, bool]:
        """S3 체크포인트를 재사용하거나 원본에서 새로 받아 보존한다.

        catalog API는 호출하지 않는다 — 이 단계만 스레드 풀에서 병렬로 돌리고
        체크포인트 기록은 호출자가 순차적으로 수행한다(세션 경합 회피).
        반환값의 두 번째 원소는 "새로 다운로드해서 저장했는지" 여부다.
        """
        image_s3_key = product.get("image_s3_key")
        image_checksum = product.get("image_checksum")
        if image_s3_key and image_checksum:
            try:
                image = load_stored_image(
                    s3_client=self.s3,
                    bucket=config.IMAGE_S3_BUCKET,
                    s3_key=image_s3_key,
                    expected_checksum=image_checksum,
                    max_bytes=config.MAX_IMAGE_BYTES,
                )
                logger.info(
                    "S3 상품 이미지 재사용: %s:%s",
                    job["source"],
                    job["external_product_id"],
                )
                return image, False
            except StoredProductImageUnavailable as exc:
                logger.warning(
                    "저장 이미지 검증 실패로 원본 URL 복구: %s:%s error=%s",
                    job["source"],
                    job["external_product_id"],
                    exc,
                )
            except ClientError as exc:
                if not _is_missing_s3_object(exc):
                    raise
                logger.warning(
                    "S3 이미지가 없어 원본 URL 복구: %s:%s key=%s",
                    job["source"],
                    job["external_product_id"],
                    image_s3_key,
                )

        image = download_and_store_image(
            session=self.http,
            s3_client=self.s3,
            source=job["source"],
            external_product_id=job["external_product_id"],
            image_url=product.get("image_url") or "",
            bucket=config.IMAGE_S3_BUCKET,
            key_prefix=config.image_s3_prefix(job["source"]),
            timeout=config.IMAGE_DOWNLOAD_TIMEOUT,
            max_bytes=config.MAX_IMAGE_BYTES,
        )
        return image, True

    def _load_or_store_image(
        self,
        job: dict[str, Any],
        product: dict[str, Any],
    ) -> PreparedImage | None:
        """이미지를 확보하고, 새로 저장했으면 catalog에 체크포인트를 남긴다."""
        image, downloaded = self._resolve_image(job, product)
        if not downloaded:
            return image
        accepted = self.catalog.mark_image_stored(
            job,
            image_s3_key=image.s3_key,
            image_checksum=image.checksum,
        )
        if not accepted:
            image.image.close()
            logger.info(
                "새 generation 작업으로 변경되어 이미지 체크포인트를 건너뜀: %s:%s",
                job["source"],
                job["external_product_id"],
            )
            return None
        product["image_s3_key"] = image.s3_key
        product["image_checksum"] = image.checksum
        return image

    def _validate(self, job: dict[str, Any]) -> dict[str, Any] | None:
        """임베딩 전에 즉시 실패로 확정할 작업을 걸러낸다."""
        if job["target_version"] != config.EMBEDDING_VERSION:
            self._fail(
                job,
                (
                    "worker와 작업의 embedding_version이 다릅니다: "
                    f"worker={config.EMBEDDING_VERSION}, "
                    f"job={job['target_version']}"
                ),
                transient=False,
            )
            return None

        if job["source"] not in config.SOURCES:
            self._fail(
                job,
                f"지원하지 않는 상품 source: {job['source']}",
                transient=False,
            )
            return None

        product = job.get("product")
        if not isinstance(product, dict):
            self._fail(
                job,
                "catalog API 응답에 상품 데이터가 없습니다.",
                transient=False,
            )
            return None
        return product

    def _fail_image_error(self, job: dict[str, Any], exc: BaseException) -> None:
        """이미지 확보 단계 예외를 재시도 가능 여부로 분류해 기록한다."""
        if isinstance(exc, InvalidProductImage):
            self._fail(job, exc, transient=False)
        elif isinstance(exc, requests.HTTPError):
            self._fail(job, exc, transient=_is_transient_http_error(exc))
        else:
            # requests.RequestException / OSError / boto3 ClientError 등은
            # 일시적 장애로 보고 재시도 예약한다.
            self._fail(job, exc, transient=True)

    def _prepare(self, job: dict[str, Any]) -> PreparedProduct | None:
        """단건 준비 (순차 경로). 배치 경로는 process_once가 병렬로 수행한다."""
        product = self._validate(job)
        if product is None:
            return None
        try:
            image = self._load_or_store_image(job, product)
        except Exception as exc:  # noqa: BLE001 - 분류는 _fail_image_error가 한다
            self._fail_image_error(job, exc)
            return None
        if image is None:
            return None
        return PreparedProduct(
            job=job,
            product=product,
            image=image,
            text=serialize_product_text(product),
        )

    def _prepare_batch(
        self,
        jobs: list[dict[str, Any]],
    ) -> list[PreparedProduct]:
        """배치 전체의 이미지를 병렬로 확보한 뒤 체크포인트를 기록한다.

        네트워크·S3 대기가 대부분이라 스레드 풀로 겹쳐 실행한다. 한 배치에
        naver와 eleven 작업이 섞여 있어도 한쪽이 다른 쪽을 막지 않는다.
        catalog API 호출(mark_image_stored)은 세션 경합을 피하려고 풀 밖에서
        순차 처리한다.
        """
        candidates = [
            (job, product)
            for job in jobs
            if (product := self._validate(job)) is not None
        ]
        if not candidates:
            return []

        workers = min(config.IMAGE_WORKERS, len(candidates))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="product-image",
        ) as pool:
            futures = [
                pool.submit(self._resolve_image, job, product)
                for job, product in candidates
            ]
            results = []
            for (job, product), future in zip(candidates, futures):
                try:
                    results.append((job, product, future.result(), None))
                except Exception as exc:  # noqa: BLE001
                    results.append((job, product, None, exc))

        prepared: list[PreparedProduct] = []
        for job, product, resolved, error in results:
            if error is not None:
                self._fail_image_error(job, error)
                continue
            image, downloaded = resolved
            if downloaded:
                accepted = self.catalog.mark_image_stored(
                    job,
                    image_s3_key=image.s3_key,
                    image_checksum=image.checksum,
                )
                if not accepted:
                    image.image.close()
                    logger.info(
                        "새 generation 작업으로 변경되어 "
                        "이미지 체크포인트를 건너뜀: %s:%s",
                        job["source"],
                        job["external_product_id"],
                    )
                    continue
                product["image_s3_key"] = image.s3_key
                product["image_checksum"] = image.checksum
            prepared.append(
                PreparedProduct(
                    job=job,
                    product=product,
                    image=image,
                    text=serialize_product_text(product),
                )
            )
        return prepared

    def _upsert_by_source(self, points_by_source: dict[str, list]) -> None:
        """쇼핑몰별 Qdrant 컬렉션에 병렬로 적재한다."""
        if not points_by_source:
            return
        if len(points_by_source) == 1:
            source, points = next(iter(points_by_source.items()))
            upsert_points(self.qdrant, config.qdrant_collection(source), points)
            return

        with ThreadPoolExecutor(
            max_workers=len(points_by_source),
            thread_name_prefix="product-upsert",
        ) as pool:
            futures = [
                pool.submit(
                    upsert_points,
                    self.qdrant,
                    config.qdrant_collection(source),
                    points,
                )
                for source, points in points_by_source.items()
            ]
            for future in futures:
                future.result()

    def process_once(self, batch_size: int) -> int:
        jobs = self.catalog.claim_jobs(
            batch_size,
            config.EMBEDDING_VERSION,
            source=self.source,
        )
        if not jobs:
            return 0

        prepared = self._prepare_batch(jobs)
        if not prepared:
            return len(jobs)

        try:
            # 모델은 프로세스당 1회만 로드하고 배치로 묶어 GPU를 한 번에 태운다.
            image_vectors = self.image_embedder.encode_images(
                [item.image.image for item in prepared]
            )
            text_vectors = self.text_embedder.encode_texts(
                [item.text for item in prepared]
            )
            points_by_source: dict[str, list] = defaultdict(list)
            for index, item in enumerate(prepared):
                source = item.job["source"]
                payload = build_product_payload(
                    item.product,
                    embedding_version=config.EMBEDDING_VERSION,
                    image_s3_bucket=config.IMAGE_S3_BUCKET,
                    image_s3_key=item.image.s3_key,
                )
                payload["text"] = item.text
                payload["image_checksum"] = item.image.checksum
                payload["qdrant_collection"] = config.qdrant_collection(source)
                points_by_source[source].append(
                    build_point(
                        source=source,
                        external_product_id=item.job["external_product_id"],
                        image_vector=image_vectors[index].tolist(),
                        text_vector=text_vectors[index].tolist(),
                        payload=payload,
                    )
                )
            self._upsert_by_source(dict(points_by_source))
        except Exception as exc:
            logger.exception("임베딩 또는 Qdrant 배치 적재 실패")
            for item in prepared:
                self._fail(item.job, exc, transient=True)
            return len(jobs)
        finally:
            for item in prepared:
                item.image.image.close()

        for item in prepared:
            accepted = self.catalog.mark_success(
                item.job,
                embedding_version=config.EMBEDDING_VERSION,
                image_s3_key=item.image.s3_key,
                image_checksum=item.image.checksum,
            )
            if accepted:
                logger.info(
                    "상품 임베딩 완료: %s:%s",
                    item.job["source"],
                    item.job["external_product_id"],
                )
            else:
                logger.info(
                    "새 generation으로 이전 작업 완료를 건너뜀: %s:%s",
                    item.job["source"],
                    item.job["external_product_id"],
                )
        return len(jobs)

    def drain(
        self,
        batch_size: int,
        *,
        max_wait_seconds: int,
        max_runtime_minutes: int,
    ) -> int:
        """현재 준비된 작업과 짧은 재시도까지 모두 처리한 뒤 종료한다."""
        started_at = time.monotonic()
        deadline = started_at + (max_runtime_minutes * 60)
        total_claimed = 0

        while time.monotonic() < deadline:
            claimed = self.process_once(batch_size)
            total_claimed += claimed
            if claimed:
                continue

            state = self.catalog.status(
                config.EMBEDDING_VERSION,
                source=self.source,
            )
            next_delay = state.get("next_available_in_seconds")
            if (
                next_delay is None
                or max_wait_seconds == 0
                or next_delay > max_wait_seconds
            ):
                break

            sleep_seconds = max(1.0, next_delay)
            remaining_runtime = deadline - time.monotonic()
            if sleep_seconds > remaining_runtime:
                break
            logger.info(
                "임베딩 재시도 작업을 %.1f초 대기",
                sleep_seconds,
            )
            time.sleep(sleep_seconds)

        logger.info(
            "임베딩 drain 종료: source=%s, claimed=%d, elapsed=%.1f초",
            self.source or "all",
            total_claimed,
            time.monotonic() - started_at,
        )
        return total_claimed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="신규 쇼핑 상품 이미지+텍스트 임베딩 worker"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="현재 pending 배치를 한 번만 처리하고 종료",
    )
    mode.add_argument(
        "--drain",
        action="store_true",
        help="준비된 pending 작업과 짧은 재시도를 모두 처리하고 종료",
    )
    parser.add_argument(
        "--source",
        choices=config.SOURCES,
        default=None,
        help="처리할 쇼핑몰. 생략하면 전체 (naver·eleven 동시 처리)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=config.BATCH_SIZE,
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=config.POLL_SECONDS,
    )
    parser.add_argument(
        "--drain-max-wait-seconds",
        type=int,
        default=config.DRAIN_MAX_WAIT_SECONDS,
    )
    parser.add_argument(
        "--max-runtime-minutes",
        type=int,
        default=config.DRAIN_MAX_RUNTIME_MINUTES,
    )
    return parser.parse_args()


def _create_indexer(args: argparse.Namespace) -> ProductIndexer | None:
    if not args.drain:
        return ProductIndexer(source=args.source)

    config.validate_runtime_config()
    catalog = ProductCatalogApiClient()
    try:
        state = catalog.status(
            config.EMBEDDING_VERSION,
            reset_stale=True,
            stale_job_minutes=config.STALE_JOB_MINUTES,
            source=args.source,
        )
        stale_count = int(state.get("reset_stale_count", 0))
        if stale_count:
            logger.warning("stale 임베딩 작업 %d건을 pending으로 복구", stale_count)

        if not state.get("has_pending_jobs"):
            logger.info("처리 가능한 임베딩 작업이 없어 모델 로드 전 종료합니다.")
            catalog.close()
            return None
        next_delay = state.get("next_available_in_seconds")
        if (
            next_delay is None
            or next_delay > args.drain_max_wait_seconds
        ):
            logger.info(
                "설정된 drain 대기 시간 안에 실행할 작업이 없어 "
                "모델 로드 전 종료합니다."
            )
            catalog.close()
            return None
        return ProductIndexer(
            catalog_client=catalog,
            reset_stale=False,
            source=args.source,
        )
    except Exception:
        catalog.close()
        raise


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise ValueError("--batch-size는 1 이상이어야 합니다.")
    if args.batch_size > 256:
        raise ValueError("--batch-size는 256 이하여야 합니다.")
    if args.poll_seconds < 1:
        raise ValueError("--poll-seconds는 1 이상이어야 합니다.")
    if args.drain_max_wait_seconds < 0:
        raise ValueError("--drain-max-wait-seconds는 0 이상이어야 합니다.")
    if args.max_runtime_minutes < 1:
        raise ValueError("--max-runtime-minutes는 1 이상이어야 합니다.")

    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info(
        "product-indexer 시작: source=%s, batch_size=%s",
        args.source or "all",
        args.batch_size,
    )
    indexer = _create_indexer(args)
    if indexer is None:
        return 0
    try:
        if args.drain:
            indexer.drain(
                args.batch_size,
                max_wait_seconds=args.drain_max_wait_seconds,
                max_runtime_minutes=args.max_runtime_minutes,
            )
            return 0
        while True:
            claimed = indexer.process_once(args.batch_size)
            if args.once:
                return 0
            if claimed == 0:
                time.sleep(args.poll_seconds)
    finally:
        indexer.close()


if __name__ == "__main__":
    raise SystemExit(main())
