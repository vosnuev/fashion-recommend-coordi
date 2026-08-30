"""사람 검수 앵커가 실제 골든셋에서 순위를 얼마나 바꾸는지 나란히 보여준다.

    python manage.py check_human_anchor
    python manage.py check_human_anchor --gender male --top 15
    python manage.py check_human_anchor --all-datasets      # 채팅 필터 없이

단위 테스트는 가짜 Qdrant로 "규칙이 맞게 동작하는가"만 본다. 실제로 순위가 움직이는지는
적재된 코디 전체에 붙여봐야 알 수 있다 — 점수가 붙은 코디가 소수라면 규칙이 맞아도
결과는 거의 그대로다.

같은 후보 집합을 가중치 0(이전 동작)과 설정값으로 각각 점수화해 상위 N을 대조한다.
0 쪽이 기준선이므로, 이 명령의 출력이 곧 "이번 변경이 무엇을 바꿨는가"다.

변화가 작은 것이 곧 실패는 아니다. 앵커는 보조 신호이고 사용자 취향이 1순위라는 것이
골든셋 설계의 전제다. 오히려 순위가 크게 요동치면 가중치가 과한 쪽을 의심한다.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

OK = "  [OK]  "
WARN = "  [주의] "


class Command(BaseCommand):
    help = "사람 검수 앵커가 골든 코디 순위를 얼마나 바꾸는지 대조한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--gender",
            default="",
            help='성별 필터 ("male" | "female"). 비우면 걸지 않는다',
        )
        parser.add_argument("--top", type=int, default=10, help="대조할 상위 건수")
        parser.add_argument(
            "--weight",
            type=float,
            help="비교할 가중치. 생략하면 RETRIEVER_HUMAN_SCORE_WEIGHT",
        )
        parser.add_argument(
            "--all-datasets",
            action="store_true",
            help="CHAT_GOLDENSET_* 필터를 걸지 않고 전체를 본다",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.recommend.services.qdrant import (
            GOLDEN_OUTFIT_COLLECTION,
            get_client,
        )
        from apps.recommend.services.retriever import (
            RetrievalRequest,
            retrieve_outfits,
        )

        weight = options["weight"]
        if weight is None:
            weight = float(settings.RETRIEVER_HUMAN_SCORE_WEIGHT)
        top = max(1, options["top"])

        client = get_client()
        self._report_payload(client, GOLDEN_OUTFIT_COLLECTION)

        request = RetrievalRequest(
            gender=options["gender"],
            limit=top,
            dataset_version=(
                "" if options["all_datasets"] else settings.CHAT_GOLDENSET_DATASET_VERSION
            ),
            dataset_statuses=(
                ()
                if options["all_datasets"]
                else tuple(settings.CHAT_GOLDENSET_DATASET_STATUSES)
            ),
        )

        baseline = self._rank(retrieve_outfits, request, client, 0.0)
        updated = self._rank(retrieve_outfits, request, client, weight)

        if not updated:
            self.stdout.write(
                WARN + "후보가 0건입니다. check_chat_recommend로 필터를 먼저 확인하세요."
            )
            return

        self.stdout.write("")
        self.stdout.write(f"가중치 0 (이전 동작) vs {weight} — 상위 {top}건")
        self.stdout.write("")
        self._print_table(baseline, updated)
        self._print_summary(baseline, updated, weight=weight)

    def _rank(self, retrieve, request, client, weight: float) -> list[Any]:
        """가중치만 바꿔 같은 질의를 두 번 돌린다."""
        previous = settings.RETRIEVER_HUMAN_SCORE_WEIGHT
        settings.RETRIEVER_HUMAN_SCORE_WEIGHT = weight
        try:
            return list(retrieve(request, client=client))
        finally:
            settings.RETRIEVER_HUMAN_SCORE_WEIGHT = previous

    def _report_payload(self, client, collection: str) -> None:
        from qdrant_client import models as qm

        total = client.count(collection_name=collection).count
        verified = client.count(
            collection_name=collection,
            count_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="human_verified", match=qm.MatchValue(value=True)
                    )
                ]
            ),
        ).count
        scored = client.count(
            collection_name=collection,
            count_filter=qm.Filter(
                must=[qm.FieldCondition(key="human_score", range=qm.Range(gte=0))]
            ),
        ).count
        self.stdout.write(f"적재 코디 {total}건")
        line = f"검수 통과 {verified}건 / 앵커 점수 {scored}건"
        if scored:
            self.stdout.write(OK + line)
        else:
            # 점수가 하나도 없으면 이 명령의 대조는 항상 같은 결과를 낸다.
            self.stdout.write(
                WARN + line + " — apply-review를 먼저 돌려야 비교가 의미 있습니다."
            )

    def _print_table(self, baseline: list[Any], updated: list[Any]) -> None:
        positions = {c.golden_id: index for index, c in enumerate(baseline, start=1)}
        header = f"{'순위':>4} {'코디':<24} {'점수':>8} {'앵커':>7} {'구간':<5} {'이전':>6}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for rank, candidate in enumerate(updated, start=1):
            payload = candidate.payload or {}
            anchor = payload.get("human_score")
            before = positions.get(candidate.golden_id)
            if before is None:
                move = "신규"
            elif before == rank:
                move = "-"
            else:
                move = f"{before - rank:+d}"
            self.stdout.write(
                f"{rank:>4} {candidate.golden_id[:24]:<24} "
                f"{candidate.score:>8.2f} "
                f"{(f'{anchor:.0f}' if anchor is not None else '·'):>7} "
                f"{str(payload.get('score_band') or '·'):<5} {move:>6}"
            )

    def _print_summary(
        self, baseline: list[Any], updated: list[Any], *, weight: float
    ) -> None:
        before_ids = [c.golden_id for c in baseline]
        after_ids = [c.golden_id for c in updated]
        entered = [gid for gid in after_ids if gid not in before_ids]
        moved = sum(
            1
            for index, gid in enumerate(after_ids)
            if index < len(before_ids) and before_ids[index] != gid
        )
        anchored = sum(
            1 for c in updated if (c.payload or {}).get("human_score") is not None
        )
        self.stdout.write("")
        self.stdout.write(f"순위가 바뀐 자리: {moved}/{len(after_ids)}")
        self.stdout.write(f"새로 진입: {len(entered)}건 {entered or ''}")
        self.stdout.write(f"상위권 중 앵커 보유: {anchored}건")
        if moved == 0 and weight > 0:
            # 변화 없음이 정상일 수 있다. 무엇을 확인해야 하는지까지 적는다.
            self.stdout.write("")
            self.stdout.write(
                WARN
                + "순위가 그대로입니다. 상위권에 앵커가 없거나(위 '앵커 보유' 확인), "
                "규칙 점수 차이가 앵커 폭보다 크다는 뜻입니다."
            )
