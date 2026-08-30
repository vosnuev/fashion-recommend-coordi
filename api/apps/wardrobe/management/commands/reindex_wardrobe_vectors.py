"""기존 옷장 아이템의 벡터 재생성 작업을 GPU 큐에 적재한다."""

from __future__ import annotations

import uuid
from typing import Any

import redis
from django.core.management.base import BaseCommand, CommandError

from apps.wardrobe.models import WardrobeItem
from apps.wardrobe.services import vector_reindex_jobs


class Command(BaseCommand):
    help = (
        "기존 크롭 이미지와 DB 태그로 옷장 벡터를 재생성하도록 GPU 큐에 적재한다. "
        "기본은 미리보기이며 --enqueue를 지정해야 큐를 수정한다."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--user-id", type=int, help="특정 사용자 ID만 선택")
        parser.add_argument(
            "--item-id",
            action="append",
            dest="item_ids",
            help="특정 옷장 아이템 UUID만 선택 (여러 번 지정 가능)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="한 번에 선택할 최대 아이템 수 (기본값: 1000, 최대: 5000)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="embedding_version이 있는 아이템도 강제로 재생성",
        )
        parser.add_argument(
            "--enqueue",
            action="store_true",
            help="미리보기 대신 실제 Redis 재인덱싱 큐에 적재",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"]
        if not 1 <= limit <= 5000:
            raise CommandError("--limit은 1 이상 5000 이하여야 합니다.")

        queryset = WardrobeItem.objects.exclude(s3_key="").order_by("id")
        if not options["force"]:
            queryset = queryset.filter(embedding_version="")
        if options.get("user_id") is not None:
            queryset = queryset.filter(user_id=options["user_id"])
        if options.get("item_ids"):
            queryset = queryset.filter(
                id__in=self._validated_item_ids(options["item_ids"])
            )

        items = list(queryset[:limit])
        for item in items:
            self.stdout.write(
                f"item={item.pk} user={item.user_id} "
                f"current_version={item.embedding_version or '-'} s3_key={item.s3_key}"
            )

        enqueued = 0
        if options["enqueue"] and items:
            try:
                enqueued = vector_reindex_jobs.enqueue_many(items)
            except (
                vector_reindex_jobs.ReindexQueueConfigurationError,
                redis.RedisError,
            ) as exc:
                raise CommandError(f"재인덱싱 큐 적재 실패: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"재인덱싱 대상: selected={len(items)} enqueued={enqueued} "
                f"mode={'enqueue' if options['enqueue'] else 'dry-run'} "
                f"force={options['force']}"
            )
        )

    @staticmethod
    def _validated_item_ids(raw_item_ids: list[str]) -> list[uuid.UUID]:
        try:
            return [uuid.UUID(raw_item_id) for raw_item_id in raw_item_ids]
        except (TypeError, ValueError) as exc:
            raise CommandError("--item-id는 UUID 형식이어야 합니다.") from exc
