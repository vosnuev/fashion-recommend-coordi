"""이미 만들어진 '오늘의 룩' 한 건이 **왜 그렇게 나왔는지** 되짚는다.

    python manage.py explain_daily_look                 # 가장 최근 행
    python manage.py explain_daily_look --user-id 3     # 그 사용자의 오늘 행
    python manage.py explain_daily_look --look-id <uuid>

check_daily_look은 "지금 이 서버가 추천을 만들 수 있는 상태인가"를 본다. 이쪽은
반대로 **이미 나온 결과 하나를 해부한다.** 남성 사용자에게 여성 코디가 나갔을 때
원인이 셋 중 어디인지 갈라야 했는데, 셋 다 겉모습이 똑같아서 만들었다:

    (1) 배포된 코드에 성별 필터가 없다        → 아래 1번이 잡는다
    (2) 스냅샷에 성별이 안 담겼다             → 2번
    (3) 골든 코디의 presentation_group이 틀렸다 → 4번

특히 1번은 지금 돌고 있는 모듈로 실제 필터를 만들어 본다. 소스를 읽는 게 아니라
실행하므로, 컨테이너가 옛 이미지를 물고 있으면 그 자리에서 드러난다.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

OK = "  [OK]  "
FAIL = "  [실패] "
WARN = "  [주의] "


class Command(BaseCommand):
    help = "이미 생성된 오늘의 룩 한 건의 근거를 단계별로 되짚는다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--look-id", help="특정 DailyLook UUID")
        parser.add_argument("--user-id", type=int, help="그 사용자의 가장 최근 행")

    def handle(self, *args: Any, **options: Any) -> None:
        self._check_deployed_code()
        look = self._pick(options)
        if look is None:
            return
        self._report_snapshot(look)
        self._report_candidates(look)
        self._report_qdrant(look)

    # ── 1. 지금 돌고 있는 코드 ─────────────────────────
    def _check_deployed_code(self) -> None:
        """소스가 아니라 **실행 결과**로 확인한다.

        "코드는 고쳤는데 이미지를 안 다시 만들었다"가 이 프로젝트에서 반복해서
        났던 실패다. 파일을 읽어 문자열을 찾는 방식은 그걸 못 잡는다 —
        컨테이너 안의 파일은 이미지에 구워진 옛 파일이기 때문이다.
        """
        from apps.recommend.services import build_stamp

        self.stdout.write(self.style.MIGRATE_HEADING("1. 지금 이 컨테이너의 코드"))
        self.stdout.write(OK + build_stamp.describe())
        self.stdout.write(
            "        ※ 이 커맨드는 api 컨테이너에서 돕니다. 추천을 실제로 만드는 건 "
            "daily-look-worker입니다 — 두 지문이 같은지 반드시 확인하세요:\n"
            "          docker compose logs daily-look-worker | grep 기동"
        )
        try:
            from apps.recommend.services.retriever import RetrievalRequest, build_filter

            built = build_filter(RetrievalRequest(gender="male"))
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(FAIL + f"build_filter 호출 실패: {type(exc).__name__}: {exc}")
            return

        conditions = [
            c for c in (getattr(built, "must", None) or [])
            if getattr(c, "key", "") == "presentation_group"
        ]
        if not conditions:
            self.stdout.write(
                FAIL + "gender='male'로 필터를 만들어도 presentation_group 조건이 "
                "붙지 않습니다. **이 컨테이너는 성별 필터가 없는 코드로 돌고 있습니다.**"
            )
            self.stdout.write(
                "        → git pull 후 다시 만드세요: "
                "docker compose build api daily-look-worker && "
                "docker compose up -d api daily-look-worker"
            )
            return
        allowed = sorted(getattr(conditions[0].match, "any", []) or [])
        self.stdout.write(OK + f"성별 필터 있음 (male → presentation_group in {allowed})")

    # ── 2~3. 대상 행 ───────────────────────────────────
    def _pick(self, options):
        from apps.recommend.models import DailyLook

        queryset = DailyLook.objects.order_by("-created_at")
        if options["look_id"]:
            queryset = queryset.filter(pk=options["look_id"])
        elif options["user_id"]:
            queryset = queryset.filter(user_id=options["user_id"])
        look = queryset.first()
        if look is None:
            self.stdout.write(FAIL + "조건에 맞는 daily_looks 행이 없습니다.")
            return None
        return look

    def _report_snapshot(self, look) -> None:
        from apps.recommend.services.gender import (
            allowed_presentation_groups,
            normalize_gender,
        )

        self.stdout.write(self.style.MIGRATE_HEADING("2. 대상 행"))
        self.stdout.write(
            f"        look_id={look.pk} user_id={look.user_id} "
            f"date={look.look_date} status={look.status}"
        )
        self.stdout.write(f"        생성 {look.created_at} / 갱신 {look.updated_at}")
        if look.error:
            self.stdout.write(f"        error={look.error}")

        raw = (look.body or {}).get("gender")
        gender = normalize_gender(raw)
        groups = allowed_presentation_groups(gender)
        self.stdout.write(self.style.MIGRATE_HEADING("3. 그때 쓴 성별"))
        self.stdout.write(
            f"        body.gender={raw!r} → 해석 {gender or '(미상)'} "
            f"→ 허용 {list(groups) or '(제한 없음)'}"
        )
        if not gender:
            self.stdout.write(
                WARN + "이 행은 성별 없이 만들어졌습니다. 필터가 걸리지 않았습니다."
            )

    def _report_candidates(self, look) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("4. 그때 뽑힌 후보"))
        candidates = look.candidates or []
        if not candidates:
            self.stdout.write("        후보 스냅샷이 없습니다 (EMPTY이거나 옛 행).")
            return
        for index, candidate in enumerate(candidates):
            group = candidate.get("presentation_group")
            mark = "★" if index == 0 else " "
            self.stdout.write(
                f"      {mark} golden_id={candidate.get('golden_id')} "
                f"score={candidate.get('score')} "
                f"group={group if group is not None else '(스냅샷에 없음 — 옛 행)'} "
                f"point_id={candidate.get('point_id')}"
            )

    # ── 5. 지금 Qdrant에 있는 실제 라벨 ────────────────
    def _report_qdrant(self, look) -> None:
        """후보 스냅샷이 아니라 **현재 벡터스토어의 값**을 직접 읽는다.

        스냅샷은 그때의 기록이라 재적재로 값이 바뀌었으면 다르다. 태깅이 안 된
        코디인지, 태깅이 틀린 코디인지는 여기서만 갈린다.
        """
        from qdrant_client import models as qm

        from apps.recommend.services.qdrant import GOLDEN_OUTFIT_COLLECTION, get_client

        golden_id = str((look.result or {}).get("golden_id", ""))
        if not golden_id:
            first = (look.candidates or [None])[0]
            golden_id = str((first or {}).get("golden_id", ""))
        if not golden_id:
            return

        self.stdout.write(
            self.style.MIGRATE_HEADING(f"5. 지금 Qdrant의 {golden_id} 라벨")
        )
        try:
            client = get_client()
            points, _ = client.scroll(
                collection_name=GOLDEN_OUTFIT_COLLECTION,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="golden_id", match=qm.MatchValue(value=golden_id)
                        )
                    ]
                ),
                with_payload=True,
                with_vectors=False,
                limit=1,
            )
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(FAIL + f"Qdrant 조회 실패: {type(exc).__name__}: {exc}")
            return

        if not points:
            self.stdout.write(
                WARN + "그 golden_id가 지금 컬렉션에 없습니다 (재적재로 "
                "dataset_version이 바뀌었을 수 있습니다)."
            )
            return

        payload = points[0].payload or {}
        group = str(payload.get("presentation_group") or "")
        self.stdout.write(
            f"        presentation_group={group or '(미분류)'} "
            f"tag_confidence={payload.get('tag_confidence')} "
            f"tag_schema_version={payload.get('tag_schema_version')!r}"
        )
        self.stdout.write(f"        style={payload.get('style')} season={payload.get('season')}")
        for item in (payload.get("items") or [])[:8]:
            self.stdout.write(
                f"          - {item.get('item_name', '')} "
                f"({item.get('category_large', '')}/{item.get('category_small', '')})"
            )

        raw = (look.body or {}).get("gender")
        from apps.recommend.services.gender import allowed_presentation_groups

        groups = allowed_presentation_groups(raw)
        if groups and group and group not in groups:
            self.stdout.write(
                FAIL + f"라벨({group})이 허용 집합{list(groups)} 밖인데도 결과에 "
                "올랐습니다. 필터가 실제로 걸리지 않았다는 뜻입니다 — 1번을 보세요."
            )
        elif groups and not group:
            self.stdout.write(
                FAIL + "이 코디는 라벨이 없습니다(미분류). 필터가 걸렸다면 애초에 "
                "후보에 오를 수 없습니다 — 1번을 보세요."
            )
        elif groups:
            self.stdout.write(
                WARN + f"라벨({group})은 허용 집합 안입니다. 즉 필터는 정상 동작했고, "
                "**태깅이 틀린 것**입니다. 이 코디를 다시 태깅해야 합니다."
            )
