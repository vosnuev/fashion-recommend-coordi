"""방치된 코디 평가를 실패로 정리한다.

    python manage.py sweep_stale_analyses
    python manage.py sweep_stale_analyses --minutes 10
    python manage.py sweep_stale_analyses --dry-run

워커가 통째로 죽으면 QUEUED/PROCESSING 행이 남아 프론트가 영원히 폴링한다.
평시에는 `run_outfit_worker`가 루프 안에서 주기적으로 같은 정리를 수행하므로
별도 크론이 필요 없다. 이 커맨드는 워커가 내려간 동안 쌓인 것을 손으로 치울 때 쓴다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.recommend.models import OutfitAnalysis
from apps.recommend.services import analysis as analysis_service


class Command(BaseCommand):
    help = "일정 시간 내에 끝나지 않은 코디 평가를 FAILED로 정리한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--minutes",
            type=int,
            default=None,
            help=(
                "이 시간(분)을 넘긴 QUEUED/PROCESSING 행을 정리한다. "
                "기본값은 OUTFIT_STALE_AFTER_MINUTES."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="대상만 출력하고 변경하지 않는다.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        minutes = options["minutes"] or settings.OUTFIT_STALE_AFTER_MINUTES

        if options["dry_run"]:
            deadline = timezone.now() - timedelta(minutes=minutes)
            targets = (
                OutfitAnalysis.objects.filter(
                    status__in=OutfitAnalysis.PENDING_STATUSES
                )
                .annotate(since=Coalesce(F("started_at"), F("created_at")))
                .filter(Q(since__lt=deadline))
            )
            for analysis in targets:
                self.stdout.write(
                    f"  {analysis.pk} {analysis.status} "
                    f"since={analysis.started_at or analysis.created_at}"
                )
            self.stdout.write(f"대상 {targets.count()}건 (변경 없음)")
            return

        count = analysis_service.sweep_stale(minutes)
        if count:
            self.stdout.write(self.style.WARNING(f"{count}건을 FAILED로 정리했습니다."))
        else:
            self.stdout.write("정리할 작업이 없습니다.")
