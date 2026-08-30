"""공유 옷의 채팅 레퍼런스 선택 가능 여부를 판정한다.

Qdrant는 파생 저장소이며, 옷장 벡터 적재가 성공한 경우에만
``WardrobeItem.embedding_version``이 유지된다. 기본 판정은 이 서버 관리 상태를
사용하고, 공유 옷 목록 응답은 실제 Qdrant 포인트도 배치 검증한다.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import redis

from apps.wardrobe.models import SharedWardrobeItem, WardrobeItem
from apps.wardrobe.services import vector_reindex_jobs
from apps.wardrobe.services.vector_reconciliation import (
    WardrobeVectorReconciler,
    WardrobeVectorStoreUnavailable,
)

logger = logging.getLogger(__name__)

REFERENCE_UNAVAILABLE_NOT_CONFIRMED = "NOT_CONFIRMED"
REFERENCE_UNAVAILABLE_VECTOR_NOT_READY = "VECTOR_NOT_READY"

REFERENCE_UNAVAILABLE_REASON_CHOICES = (
    REFERENCE_UNAVAILABLE_NOT_CONFIRMED,
    REFERENCE_UNAVAILABLE_VECTOR_NOT_READY,
)


@dataclass(frozen=True, slots=True)
class ReferenceEligibility:
    eligible: bool
    unavailable_reason: str | None = None


def evaluate_reference_eligibility(
    shared_item: SharedWardrobeItem,
) -> ReferenceEligibility:
    """원본 옷의 처리 상태를 기준으로 선택 가능 여부를 반환한다.

    공유 상태(available/borrowed/private)는 더 이상 판정에 쓰지 않는다 —
    방에 등록된 옷은 멤버 전원이 항상 참고할 수 있다.
    """

    item = shared_item.wardrobe_item
    if not item.confirmed:
        return ReferenceEligibility(
            eligible=False,
            unavailable_reason=REFERENCE_UNAVAILABLE_NOT_CONFIRMED,
        )

    if not item.s3_key or not item.embedding_version:
        return ReferenceEligibility(
            eligible=False,
            unavailable_reason=REFERENCE_UNAVAILABLE_VECTOR_NOT_READY,
        )

    return ReferenceEligibility(eligible=True)


def resolve_wardrobe_vector_readiness(
    wardrobe_items: Iterable[WardrobeItem],
    *,
    reconciler: WardrobeVectorReconciler | None = None,
    enqueue_missing: bool = False,
) -> dict[str, bool]:
    """확정된 옷장 아이템의 실제 Qdrant 벡터 준비 여부를 일괄 판정한다.

    개인 옷과 공유 옷 모두 같은 벡터 계약을 사용한다. Qdrant 장애는 포인트 누락으로
    단정하지 않으며, 실제 누락 또는 DB 임베딩 플래그 누락만 재인덱싱 큐에 넣는다.
    """

    unique_items = {
        str(item.pk): item
        for item in wardrobe_items
    }
    ready = {item_id: False for item_id in unique_items}
    repair_items = {
        item_id: item
        for item_id, item in unique_items.items()
        if item.confirmed and item.s3_key and not item.embedding_version
    }
    candidates = [
        item
        for item in unique_items.values()
        if item.confirmed and item.s3_key and item.embedding_version
    ]

    audits = []
    if candidates:
        try:
            audits = (reconciler or WardrobeVectorReconciler()).audit(candidates)
        except WardrobeVectorStoreUnavailable:
            logger.warning("옷장 참고 가능 여부를 위한 Qdrant 조회 실패")
            # 저장소 장애를 포인트 누락으로 오인해 전체를 재색인하지 않는다.

    if audits:
        for audit in audits:
            ready[audit.item_id] = audit.vector_ready
            if not audit.vector_ready and audit.item_id in unique_items:
                repair_items[audit.item_id] = unique_items[audit.item_id]

    if enqueue_missing and repair_items:
        try:
            enqueued = vector_reindex_jobs.enqueue_many(repair_items.values())
            if enqueued:
                logger.info("옷장 누락 벡터 자동 복구 큐 적재: %s건", enqueued)
        except (
            vector_reindex_jobs.ReindexQueueConfigurationError,
            redis.RedisError,
        ) as exc:
            # 목록·채팅 요청은 설정 장애 때문에 500이 되지 않고 준비 중으로 닫힌다.
            logger.warning("옷장 누락 벡터 자동 복구 요청 실패: %s", exc)

    return ready


def resolve_reference_eligibilities(
    shared_items: Iterable[SharedWardrobeItem],
    *,
    reconciler: WardrobeVectorReconciler | None = None,
    enqueue_missing: bool = False,
) -> dict[str, ReferenceEligibility]:
    """공유 옷 목록의 실제 벡터 준비 상태를 Qdrant 한 번에 확인한다.

    미확정 아이템은 기존 판정만 사용한다. DB 기준으로 선택 가능한 후보는 Qdrant에서
    검증하며, 저장소 장애 시에는 거짓 양성보다 안전한 선택 불가 상태로 닫는다.
    ``enqueue_missing``이면 누락 아이템을 멱등 재인덱싱 큐에 적재한다.
    """

    items = list(shared_items)
    resolved = {
        str(shared_item.pk): evaluate_reference_eligibility(shared_item)
        for shared_item in items
    }
    vector_candidates = [
        shared_item.wardrobe_item
        for shared_item in items
        if shared_item.wardrobe_item.confirmed
    ]
    readiness_by_item_id = resolve_wardrobe_vector_readiness(
        vector_candidates,
        reconciler=reconciler,
        enqueue_missing=enqueue_missing,
    )
    for shared_item in items:
        shared_id = str(shared_item.pk)
        if (
            resolved[shared_id].unavailable_reason
            == REFERENCE_UNAVAILABLE_NOT_CONFIRMED
        ):
            continue
        if readiness_by_item_id.get(str(shared_item.wardrobe_item_id)) is not True:
            resolved[shared_id] = ReferenceEligibility(
                eligible=False,
                unavailable_reason=REFERENCE_UNAVAILABLE_VECTOR_NOT_READY,
            )
    return resolved
