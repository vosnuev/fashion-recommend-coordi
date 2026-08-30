"""옷장 DB와 Qdrant 벡터 상태를 점검하고 DB 플래그를 선택적으로 복구한다."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any, Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.wardrobe.models import WardrobeItem
from apps.wardrobe.services.vector_reconciliation import (
    WardrobeVectorAuditResult,
    WardrobeVectorReconciler,
    WardrobeVectorStoreUnavailable,
)


def _batches(items: Iterable[WardrobeItem], batch_size: int):
    batch: list[WardrobeItem] = []
    for item in items:
        batch.append(item)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


class Command(BaseCommand):
    help = (
        "옷장 DB embedding_version과 실제 Qdrant 포인트를 점검한다. "
        "기본은 읽기 전용이며 --repair-flags를 지정해야 DB 플래그를 수정한다."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--user-id", type=int, help="특정 사용자 ID만 점검")
        parser.add_argument(
            "--item-id",
            action="append",
            dest="item_ids",
            help="특정 옷장 아이템 UUID만 점검 (여러 번 지정 가능)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Qdrant retrieve 배치 크기 (기본값: 100)",
        )
        parser.add_argument(
            "--repair-flags",
            action="store_true",
            help="실제 벡터 상태에 맞춰 DB embedding_version만 수정",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size는 1 이상이어야 합니다.")

        queryset = WardrobeItem.objects.order_by("id").only(
            "id",
            "user_id",
            "s3_key",
            "embedding_version",
            "confirmed",
            "category_large",
        )
        if options.get("user_id") is not None:
            queryset = queryset.filter(user_id=options["user_id"])
        if options.get("item_ids"):
            item_ids = self._validated_item_ids(options["item_ids"])
            queryset = queryset.filter(id__in=item_ids)

        reconciler = WardrobeVectorReconciler()
        results: list[WardrobeVectorAuditResult] = []
        repair_items: list[WardrobeItem] = []
        try:
            for item_batch in _batches(
                queryset.iterator(chunk_size=batch_size),
                batch_size,
            ):
                batch_results = reconciler.audit(item_batch)
                results.extend(batch_results)
                for item, result in zip(item_batch, batch_results):
                    if result.needs_flag_repair:
                        item.embedding_version = result.desired_embedding_version
                        repair_items.append(item)
        except WardrobeVectorStoreUnavailable as exc:
            # 모든 조회가 끝난 뒤에만 DB를 갱신하므로 중간 장애에도 부분 반영되지 않는다.
            raise CommandError(str(exc)) from exc

        for result in results:
            if result.issues or result.needs_flag_repair:
                issues = (
                    ",".join(result.issues)
                    if result.issues
                    else "DB_FLAG_MISMATCH"
                )
                self.stdout.write(
                    f"item={result.item_id} user={result.user_id} "
                    f"db_version={result.db_embedding_version or '-'} "
                    f"indexed_version={result.indexed_embedding_version or '-'} "
                    f"desired_version={result.desired_embedding_version or '-'} "
                    f"issues={issues}"
                )

        repaired = 0
        if options["repair_flags"] and repair_items:
            with transaction.atomic():
                WardrobeItem.objects.bulk_update(
                    repair_items,
                    ["embedding_version"],
                    batch_size=batch_size,
                )
            repaired = len(repair_items)

        issue_counts = Counter(issue for result in results for issue in result.issues)
        ready_count = sum(result.vector_ready for result in results)
        self.stdout.write(
            self.style.SUCCESS(
                f"점검 완료: total={len(results)} ready={ready_count} "
                f"not_ready={len(results) - ready_count} "
                f"flag_mismatches={len(repair_items)} repaired={repaired} "
                f"mode={'repair' if options['repair_flags'] else 'dry-run'}"
            )
        )
        if issue_counts:
            self.stdout.write(
                "이슈 집계: "
                + ", ".join(
                    f"{issue}={count}" for issue, count in sorted(issue_counts.items())
                )
            )

    @staticmethod
    def _validated_item_ids(raw_item_ids: list[str]) -> list[uuid.UUID]:
        try:
            return [uuid.UUID(raw_item_id) for raw_item_id in raw_item_ids]
        except (TypeError, ValueError) as exc:
            raise CommandError("--item-id는 UUID 형식이어야 합니다.") from exc
