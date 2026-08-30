"""catalog 내부 상품 임베딩 API 클라이언트."""

from __future__ import annotations

from typing import Any

import requests

from . import product_config as config


class ProductCatalogApiError(RuntimeError):
    """catalog API 응답이 유효하지 않거나 호출에 실패한 경우."""


class ProductCatalogApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: int | None = None,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or config.CATALOG_API_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds or config.CATALOG_API_TIMEOUT_SECONDS
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.session.headers.update(
            {
                "Authorization": (
                    f"Bearer {token or config.CATALOG_API_TOKEN}"
                ),
                "Content-Type": "application/json",
                "User-Agent": "SKN28-product-indexer/1.0",
            }
        )

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{self.base_url}/{path.lstrip('/')}",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProductCatalogApiError(
                f"catalog API 호출 실패: {path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ProductCatalogApiError(
                f"catalog API 응답 형식이 올바르지 않습니다: {path}"
            )
        return data

    @staticmethod
    def _with_source(
        payload: dict[str, Any],
        source: str | None,
    ) -> dict[str, Any]:
        """source가 있을 때만 payload에 싣는다 (생략 = 전체 쇼핑몰)."""
        if source:
            payload["source"] = source
        return payload

    def status(
        self,
        target_version: str,
        *,
        reset_stale: bool = False,
        stale_job_minutes: int = 30,
        source: str | None = None,
    ) -> dict[str, Any]:
        data = self._post(
            "status/",
            self._with_source(
                {
                    "target_version": target_version,
                    "reset_stale": reset_stale,
                    "stale_job_minutes": stale_job_minutes,
                },
                source,
            ),
        )
        has_pending = data.get("has_pending_jobs")
        next_delay = data.get("next_available_in_seconds")
        stale_count = data.get("reset_stale_count")
        if (
            not isinstance(has_pending, bool)
            or (
                next_delay is not None
                and (
                    not isinstance(next_delay, (int, float))
                    or isinstance(next_delay, bool)
                    or next_delay < 0
                )
            )
            or not isinstance(stale_count, int)
            or isinstance(stale_count, bool)
            or stale_count < 0
        ):
            raise ProductCatalogApiError(
                "catalog API status 응답 형식이 올바르지 않습니다."
            )
        return data

    def claim_jobs(
        self,
        limit: int,
        target_version: str,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self._post(
            "claim/",
            self._with_source(
                {
                    "limit": limit,
                    "target_version": target_version,
                },
                source,
            ),
        )
        jobs = data.get("jobs")
        if not isinstance(jobs, list) or not all(
            isinstance(job, dict) for job in jobs
        ):
            raise ProductCatalogApiError(
                "catalog API claim 응답에 jobs 배열이 없습니다."
            )
        required = {
            "id",
            "source",
            "external_product_id",
            "target_version",
            "generation",
            "attempt_count",
            "product",
        }
        if any(
            not required.issubset(job)
            or not isinstance(job.get("product"), dict)
            for job in jobs
        ):
            raise ProductCatalogApiError(
                "catalog API claim 작업 형식이 올바르지 않습니다."
            )
        return jobs

    @staticmethod
    def _job_identity(job: dict[str, Any]) -> dict[str, int]:
        return {
            "generation": int(job["generation"]),
            "attempt_count": int(job["attempt_count"]),
        }

    def mark_image_stored(
        self,
        job: dict[str, Any],
        *,
        image_s3_key: str,
        image_checksum: str,
    ) -> bool:
        data = self._post(
            f"{job['id']}/image/",
            {
                **self._job_identity(job),
                "image_s3_key": image_s3_key,
                "image_checksum": image_checksum,
            },
        )
        return data.get("accepted") is True

    def mark_success(
        self,
        job: dict[str, Any],
        *,
        embedding_version: str,
        image_s3_key: str,
        image_checksum: str,
    ) -> bool:
        data = self._post(
            f"{job['id']}/complete/",
            {
                **self._job_identity(job),
                "embedding_version": embedding_version,
                "image_s3_key": image_s3_key,
                "image_checksum": image_checksum,
            },
        )
        return data.get("accepted") is True

    def mark_failure(
        self,
        job: dict[str, Any],
        error: str,
        *,
        max_retries: int,
        retry_delay_seconds: int,
        transient: bool,
    ) -> str | None:
        data = self._post(
            f"{job['id']}/fail/",
            {
                **self._job_identity(job),
                "error": error[:4000],
                "max_retries": max_retries,
                "retry_delay_seconds": retry_delay_seconds,
                "transient": transient,
            },
        )
        if data.get("accepted") is not True:
            return None
        status = data.get("status")
        if status not in {"pending", "failed"}:
            raise ProductCatalogApiError(
                "catalog API failure 응답의 status가 올바르지 않습니다."
            )
        return status
