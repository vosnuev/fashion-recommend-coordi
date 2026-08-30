from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.chat.models import ChatIdentity


class Command(BaseCommand):
    help = "만료된 게스트 identity와 하위 세션·메시지·추천 결과를 정리합니다."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="삭제하지 않고 대상 개수만 출력합니다.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="한 번에 삭제할 identity 수 (기본 500)",
        )

    def handle(self, *args, **options) -> None:
        batch_size = options["batch_size"]
        if batch_size < 1 or batch_size > 5000:
            raise CommandError("batch-size는 1 이상 5000 이하여야 합니다.")

        expired = ChatIdentity.objects.filter(
            identity_type=ChatIdentity.IdentityType.GUEST,
            expires_at__lte=timezone.now(),
        ).order_by("expires_at")
        target_count = expired.count()
        if options["dry_run"]:
            self.stdout.write(f"만료 게스트 identity 삭제 대상: {target_count}개")
            return

        deleted_count = 0
        while True:
            ids = list(expired.values_list("id", flat=True)[:batch_size])
            if not ids:
                break
            with transaction.atomic():
                locked_ids = list(
                    ChatIdentity.objects.select_for_update()
                    .filter(
                        id__in=ids,
                        identity_type=ChatIdentity.IdentityType.GUEST,
                        expires_at__lte=timezone.now(),
                    )
                    .values_list("id", flat=True)
                )
                deleted, _ = ChatIdentity.objects.filter(id__in=locked_ids).delete()
                deleted_count += len(locked_ids)
                if deleted < len(locked_ids):
                    raise CommandError(
                        "게스트 채팅 연쇄 삭제 결과가 올바르지 않습니다."
                    )

        self.stdout.write(
            self.style.SUCCESS(f"만료 게스트 identity 삭제: {deleted_count}개")
        )
