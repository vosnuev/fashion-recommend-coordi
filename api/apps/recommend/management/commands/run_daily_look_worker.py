"""오늘의 룩 생성 워커.

    python manage.py run_daily_look_worker
    python manage.py run_daily_look_worker --once     # 1건만 처리하고 종료
    python manage.py run_daily_look_worker --sweep    # 밀린 QUEUED를 큐에 다시 밀어넣고 종료

run_outfit_worker와 같은 구조다 (큐 → claim → 처리 → ack). 다른 점은 두 가지.

- 큐가 분리돼 있다. 오늘의 룩이 밀려도 코디 평가는 영향받지 않는다 —
  평가는 사용자가 화면에서 기다리지만 이쪽은 백그라운드다.
- `--sweep`이 있다. 홈 진입 시점에 Redis가 죽어 있으면 행은 QUEUED로 남지만 큐에는
  없다. 그 상태를 방치하면 사용자는 종일 '생성 중'을 본다.

**워커는 1대만 띄운다.** recover_stale()이 processing에 남은 작업을 전부 되돌리기
때문에, 2대 이상이면 다른 워커가 처리 중인 작업까지 회수한다.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.recommend.models import DailyLook
from apps.recommend.services import daily_look as service
from apps.recommend.services import queue as queue_service

logger = logging.getLogger(__name__)

SPEC = queue_service.DAILY_LOOK

#: 이 시간이 지나도록 PROCESSING인 행은 워커가 죽은 것으로 본다.
STALE_MINUTES = 15
SWEEP_INTERVAL_SEC = 300


class Command(BaseCommand):
    help = "오늘의 룩 큐를 소비해 리트리버 + Gemini로 추천을 생성한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--once", action="store_true", help="1건만 처리하고 종료")
        parser.add_argument(
            "--sweep",
            action="store_true",
            help="큐에서 누락된 QUEUED 행을 다시 적재하고 종료",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # 어느 코드로 도는지 먼저 찍는다. 이 워커는 api와 다른 이미지로 빌드된
        # 적이 있어, api만 다시 만들고 워커는 옛 코드로 몇 시간을 돌았다.
        # 증상은 "추천 로직이 틀렸다"로 보였다 — 스냅샷은 새 코드가 쓰고 추천은
        # 옛 코드가 만들었으니. api 쪽 지문(check_daily_look)과 비교하면 된다.
        from apps.recommend.services import build_stamp

        logger.info("daily-look-worker 기동: %s", build_stamp.describe())
        self.stdout.write(f"daily-look-worker 기동: {build_stamp.describe()}")

        if options["sweep"]:
            self.stdout.write(f"재적재: {self._requeue_orphans()}건")
            return

        self._running = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._stop)

        recovered = queue_service.recover_stale(spec=SPEC)
        if recovered:
            self.stdout.write(f"재시작 복구: {recovered}건")

        last_sweep = 0.0
        while self._running:
            now = time.monotonic()
            if now - last_sweep > SWEEP_INTERVAL_SEC:
                self._release_stale()
                self._requeue_orphans()
                last_sweep = now

            raw = queue_service.fetch(spec=SPEC)
            if raw is None:
                if options["once"]:
                    return
                continue

            self._handle(raw)
            if options["once"]:
                return

    def _stop(self, *_: Any) -> None:
        self._running = False

    def _handle(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
            look_id = str(payload["look_id"])
        except (ValueError, KeyError):
            # 형식이 깨진 페이로드는 재시도해도 같다. 바로 dead로 보낸다.
            logger.error("오늘의 룩 큐 페이로드 파손: %s", raw[:500])
            queue_service.retry_or_dead(raw, "unknown", "malformed payload", spec=SPEC)
            return

        # 착용 이미지만 다시 만드는 작업. 추천은 이미 성공했으므로 claim()을
        # 거치지 않는다 (그 함수는 SUCCEEDED면 None을 돌려준다).
        if payload.get("job") == service.JOB_RENDER:
            try:
                service.run_render_only(look_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("오늘의 룩 %s 착용 이미지 재생성 실패", look_id)
                queue_service.retry_or_dead(raw, look_id, str(exc), spec=SPEC)
                return
            queue_service.ack(raw, look_id, spec=SPEC)
            return

        # '다른 룩' 후보들의 착용 이미지. 추천이 이미 끝난 뒤에 도는 부가 작업이라
        # 여기서도 claim()을 거치지 않는다.
        if payload.get("job") == service.JOB_RENDER_ALTERNATIVES:
            try:
                service.run_alternative_renders(look_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("오늘의 룩 %s 후보 착용 이미지 생성 실패", look_id)
                queue_service.retry_or_dead(raw, look_id, str(exc), spec=SPEC)
                return
            queue_service.ack(raw, look_id, spec=SPEC)
            return

        look = service.claim(look_id)
        if look is None:
            # 이미 성공했거나 행이 지워졌다. 재시도 대상이 아니다.
            queue_service.ack(raw, look_id, spec=SPEC)
            return

        try:
            service.run(look)
        except Exception as exc:  # noqa: BLE001 — 한 건의 실패로 워커가 죽으면 안 된다
            logger.exception("오늘의 룩 %s 생성 실패", look_id)
            dead = queue_service.retry_or_dead(raw, look_id, str(exc), spec=SPEC)
            if dead:
                service.mark_failed(look, str(exc))
            else:
                # 재시도 예약됐으니 다시 집을 수 있게 QUEUED로 되돌린다.
                look.status = DailyLook.Status.QUEUED
                look.enqueued_at = timezone.now()
                look.save(update_fields=["status", "enqueued_at", "updated_at"])
            return

        queue_service.ack(raw, look_id, spec=SPEC)
        self.stdout.write(f"완료 {look_id} ({look.status})")

    def _release_stale(self) -> int:
        """오래 PROCESSING인 행을 QUEUED로 되돌린다 (워커가 죽었던 경우)."""
        cutoff = timezone.now() - timedelta(minutes=STALE_MINUTES)
        count = DailyLook.objects.filter(
            status=DailyLook.Status.PROCESSING, updated_at__lt=cutoff
        ).update(
            status=DailyLook.Status.QUEUED,
            enqueued_at=None,
            updated_at=timezone.now(),
        )
        if count:
            logger.warning("정체된 오늘의 룩 %d건을 QUEUED로 되돌림", count)
        return count

    def _requeue_orphans(self) -> int:
        """QUEUED인데 큐에 없는 행을 다시 적재한다.

        홈 진입 시점에 Redis가 죽어 있었으면 행만 남고 작업은 없다. 그대로 두면
        사용자는 종일 '생성 중'을 보게 된다.
        """
        orphans = DailyLook.objects.filter(
            status=DailyLook.Status.QUEUED, look_date=service.today()
        ).values_list("pk", flat=True)
        count = 0
        for look_id in orphans:
            try:
                queue_service.push({"look_id": str(look_id)}, spec=SPEC)
                now = timezone.now()
                DailyLook.objects.filter(pk=look_id).update(
                    enqueued_at=now,
                    updated_at=now,
                )
                count += 1
            except Exception:  # noqa: BLE001
                logger.exception("오늘의 룩 %s 재적재 실패", look_id)
        if count:
            logger.info("큐에서 누락된 오늘의 룩 %d건 재적재", count)
        return count
