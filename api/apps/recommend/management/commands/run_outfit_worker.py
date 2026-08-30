"""코디 평가 워커.

    python manage.py run_outfit_worker
    python manage.py run_outfit_worker --once        # 1건만 처리하고 종료 (디버깅)

api 이미지를 그대로 쓰는 Django 커맨드다. 옷장 파이프라인(image-processor)은 GPU
별도 서버라 DB 접근을 막고 콜백을 썼지만, 이 워커는 api와 같은 코드·같은 네트워크에서
돌기 때문에 ORM으로 직접 쓴다 — 콜백·내부 토큰이라는 실패 지점이 통째로 사라진다.

흐름: 큐에서 꺼내기 → claim(PROCESSING) → S3 다운로드·축소·Gemini → 기록 → ack
실패하면 재시도, 한도를 넘기면 dead queue로 보내고 행을 FAILED로 마킹한다.

**워커는 1대만 띄운다.** recover_stale()이 processing에 남은 작업을 전부 되돌리기
때문에, 2대 이상이면 다른 워커가 처리 중인 작업까지 회수한다 (queue.py 주석 참고).
"""

from __future__ import annotations

import json
import logging
import signal
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.recommend.services import analysis as analysis_service
from apps.recommend.services import queue, storage

logger = logging.getLogger(__name__)

# 방치된 행 정리 주기. 별도 크론을 두지 않고 워커 루프에 얹는다.
SWEEP_INTERVAL_SEC = 60


class Command(BaseCommand):
    help = "코디 평가 큐를 소비해 Gemini 평가를 수행한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="작업 1건을 처리(또는 대기 타임아웃)한 뒤 종료한다.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not storage.is_configured():
            # 사진을 못 읽으면 모든 작업이 실패한다. 조용히 돌지 않고 크게 알린다.
            logger.error(
                "OUTFIT_S3_BUCKET/WARDROBE_S3_BUCKET 미설정 — 사진을 읽을 수 없어 모든 평가가 실패한다"
            )

        self._running = True
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        queue.recover_stale()
        logger.info("코디 평가 워커 시작 (queue=%s)", queue.PENDING_KEY)

        last_sweep = 0.0
        while self._running:
            now = time.monotonic()
            if now - last_sweep > SWEEP_INTERVAL_SEC:
                last_sweep = now
                try:
                    analysis_service.sweep_stale()
                except Exception:  # noqa: BLE001 — 정리 실패가 워커를 죽이지 않는다
                    logger.exception("방치 작업 정리 실패")

            raw = queue.fetch()
            if raw is None:
                if options["once"]:
                    break
                continue

            self._handle(raw)
            if options["once"]:
                break

        logger.info("코디 평가 워커 종료")

    def _request_stop(self, *_args: Any) -> None:
        """SIGTERM/SIGINT — 현재 작업을 끝내고 루프를 빠져나온다.

        중간에 죽어도 processing에 남아 재시작 시 복구되지만, 중복 Gemini 호출은 돈이다.
        """
        logger.info("종료 신호 수신 — 현재 작업을 마치고 종료한다")
        self._running = False

    def _handle(self, raw: str) -> None:
        analysis_id = "?"
        try:
            analysis_id = str(json.loads(raw)["analysis_id"])
        except (ValueError, KeyError, TypeError):
            # 파싱조차 안 되는 페이로드는 재시도해도 같다 — 바로 버린다
            logger.exception("큐 페이로드 해석 실패, 폐기: %s", raw[:200])
            queue.ack(raw, analysis_id)
            return

        try:
            analysis = analysis_service.claim(analysis_id)
            if analysis is None:
                # 행이 없거나 이미 완료 — 중복 배달이므로 ack만 한다
                queue.ack(raw, analysis_id)
                return

            analysis_service.run_analysis(analysis)
            queue.ack(raw, analysis_id)
        except Exception as exc:  # noqa: BLE001 — 작업 단위로 격리하고 재시도한다
            logger.exception("평가 %s 실패", analysis_id)
            error = f"{type(exc).__name__}: {exc}"
            if queue.retry_or_dead(raw, analysis_id, error):
                self._mark_failed(analysis_id, error)

    def _mark_failed(self, analysis_id: str, error: str) -> None:
        from apps.recommend.models import OutfitAnalysis

        analysis = OutfitAnalysis.objects.filter(pk=analysis_id).first()
        if analysis is None:
            return
        analysis_service.mark_failed(analysis, error)
        logger.error(
            "평가 %s 최종 실패 (attempts=%s, %s)",
            analysis_id,
            analysis.attempts,
            timezone.now().isoformat(),
        )
