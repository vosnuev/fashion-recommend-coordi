"""오늘의 룩 파이프라인 자가 진단.

    python manage.py check_daily_look
    python manage.py check_daily_look --user-id 3        # 그 사용자로 실제 생성까지
    python manage.py check_daily_look --user-id 3 --dry-run   # 생성은 하지 않고 검색만

왜 필요한가. 이 기능은 사용자 입력이 없어서 "안 되는 것"과 "아직 안 만들어진 것"이
겉으로 똑같아 보인다. 게다가 로그인 훅은 로그인을 막지 않으려고 예외를 삼키므로
화면에도 흔적이 남지 않는다. 그래서 연결고리를 하나씩 밟아보고 **어디서 끊겼는지**
한 줄로 말해주는 도구가 필요하다.

각 단계는 앞 단계가 성공했을 때만 의미가 있으므로 순서대로 돌고, 실패하면 그
지점에서 멈춰 다음 할 일을 알려준다.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection

OK = "  [OK]  "
FAIL = "  [실패] "
WARN = "  [주의] "


class Command(BaseCommand):
    help = "오늘의 룩 파이프라인이 실제로 동작 가능한 상태인지 단계별로 점검한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--user-id", type=int, help="이 사용자로 실제 흐름까지 확인")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="행 생성·큐 적재 없이 검색 단계까지만 확인",
        )
        parser.add_argument(
            "--regenerate",
            action="store_true",
            help="오늘 행을 지우고 다시 만든다 (--user-id 필요). 로직을 고친 뒤 "
                 "같은 날 다시 시험할 때 쓴다 — (user, look_date) 유니크 제약 때문에 "
                 "행이 남아 있으면 로그인해도 재생성되지 않는다.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.recommend.services import build_stamp

        self.stdout.write(
            self.style.MIGRATE_HEADING("0. 이 컨테이너의 추천 코드 지문")
        )
        self.stdout.write(OK + build_stamp.describe())
        self.stdout.write(
            "        → daily-look-worker 기동 로그의 지문과 다르면 로직이 아니라 "
            "배포 문제입니다 (docker compose logs daily-look-worker | head)."
        )

        steps = [
            ("1. 배포된 코드", self._check_code),
            ("2. daily_looks 테이블", self._check_table),
            ("3. 체형 규칙표", self._check_rules),
            ("4. Qdrant 연결·컬렉션", self._check_qdrant),
            ("5. 리트리버 검색", self._check_retriever),
            ("6. 작업 큐 (Redis)", self._check_queue),
            ("7. Gemini 설정", self._check_gemini),
        ]
        for title, check in steps:
            self.stdout.write(self.style.MIGRATE_HEADING(title))
            try:
                if check(options) is False:
                    self.stdout.write(
                        self.style.ERROR("\n여기서 끊겼습니다. 위 안내를 먼저 처리하세요.")
                    )
                    return
            except Exception as exc:  # noqa: BLE001 — 진단 도구가 죽으면 안 된다
                self.stdout.write(FAIL + f"{type(exc).__name__}: {exc}")
                self.stdout.write(self.style.ERROR("\n여기서 끊겼습니다."))
                return

        if options["user_id"]:
            self.stdout.write(self.style.MIGRATE_HEADING("8. 실제 생성 경로"))
            self._check_user_flow(options)

        self.stdout.write(self.style.SUCCESS("\n점검 완료."))

    # ── 1 ──────────────────────────────────────────────
    def _check_code(self, options) -> bool:
        """컨테이너 안의 코드가 최신인지. 이미지가 옛 코드면 여기서 걸린다.

        ⚠️ 이 검사는 훅의 **이름을 그대로 박아 둔다.** 이름을 바꾸면 여기도 같이
        고쳐야 한다. 2026-08-18에 `_kick_off_daily_look` → `_daily_look_payload`로
        개명하면서 이 줄을 안 고쳐, 최신 이미지에 "옛 코드입니다"라는 거짓 실패가
        났다 — 진단 도구가 진단 대상보다 낡으면 배포를 의심하느라 시간을 버린다.
        """
        import inspect

        from apps.home import views as home_views

        if not hasattr(home_views, "_daily_look_payload"):
            # 개명 전 이름이 남아 있으면 그때는 정말로 옛 이미지다.
            reason = (
                "옛 이름(_kick_off_daily_look)만 있습니다"
                if hasattr(home_views, "_kick_off_daily_look")
                else "홈 진입 훅이 없습니다"
            )
            self.stdout.write(FAIL + f"home/views.py에 {reason}.")
            self.stdout.write(
                "        → 이미지가 옛 코드입니다: "
                "docker compose --profile api build api migrate && up -d api"
            )
            return False

        source = inspect.getsource(home_views.HomeView.get)
        if "_daily_look_payload" not in source:
            self.stdout.write(FAIL + "홈 뷰가 훅을 호출하지 않습니다.")
            return False
        self.stdout.write(OK + "홈 진입 훅이 HomeView.get 안에 있습니다.")

        # 훅이 있어도 상태를 안 실어 보내면 프론트는 완성 전 구간을 스스로 조회해야
        # 하고, 그 왕복 동안 추천 자리가 비어 깜빡인다. 경고로만 둔다 — 생성 자체는
        # 되므로 여기서 진단을 멈출 이유는 없다.
        if '"daily_look"' in source:
            self.stdout.write(OK + "홈 응답이 daily_look 상태를 함께 싣습니다.")
        else:
            self.stdout.write(
                WARN + "홈 응답에 daily_look 상태가 없습니다 (프론트가 별도 조회로 폴백)."
            )
        return True

    # ── 2 ──────────────────────────────────────────────
    def _check_table(self, options) -> bool:
        from apps.recommend.models import DailyLook
        from apps.recommend.services.daily_look import today

        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.daily_looks')")
            if cursor.fetchone()[0] is None:
                self.stdout.write(FAIL + "daily_looks 테이블이 없습니다.")
                self.stdout.write("        → python manage.py migrate")
                return False

        look_date = today()
        total = DailyLook.objects.count()
        todays = DailyLook.objects.filter(look_date=look_date)
        self.stdout.write(OK + f"테이블 존재. 전체 {total}행 / 오늘({look_date}) {todays.count()}행")
        for row in todays.order_by("-created_at")[:5]:
            self.stdout.write(
                f"        user={row.user_id} status={row.status} "
                f"candidates={len(row.candidates or [])} created={row.created_at:%H:%M:%S}"
            )
        if total and not todays.exists():
            # 날짜 경계 문제를 여기서 짚어준다. DB의 CURRENT_DATE는 UTC일 수 있어
            # 손으로 조회하면 KST 기준 행을 못 찾는 경우가 있다.
            self.stdout.write(
                WARN + f"오늘 행이 없습니다. 서비스 기준 날짜는 {look_date}(Asia/Seoul)입니다 — "
                "psql의 CURRENT_DATE(UTC)와 다를 수 있습니다."
            )
        return True

    # ── 3 ──────────────────────────────────────────────
    def _check_rules(self, options) -> bool:
        from apps.recommend.services.style_rules import RULES_DIR, load_body_rules

        path = RULES_DIR / "body_fit_rules.json"
        if not path.exists():
            self.stdout.write(FAIL + f"규칙표가 없습니다: {path}")
            self.stdout.write("        → 이미지에 rules/ 가 안 들어갔습니다. 재빌드하세요.")
            return False
        rules = load_body_rules()
        self.stdout.write(
            OK + f"{rules.schema_version} / 실루엣 {len(rules.silhouette)}종 "
            f"· BMI {len(rules.bmi_band)}종 (가중치 선호 {rules.weights.preference_avoid} "
            f"vs 규칙 {rules.weights.rule_avoid})"
        )
        return True

    # ── 4 ──────────────────────────────────────────────
    def _check_qdrant(self, options) -> bool:
        from django.conf import settings

        from apps.recommend.services.qdrant import (
            GOLDEN_ITEM_COLLECTION,
            GOLDEN_OUTFIT_COLLECTION,
            get_client,
        )

        client = get_client()
        try:
            client.get_collections()
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(FAIL + f"Qdrant 접속 실패 ({settings.QDRANT_URL}): {exc}")
            self.stdout.write(
                "        → 포트 없는 https URL이면 qdrant-client가 :6333을 붙입니다. "
                ":443을 명시하세요."
            )
            return False

        self._report_presentation_groups(client)

        empty = []
        for name in (GOLDEN_OUTFIT_COLLECTION, GOLDEN_ITEM_COLLECTION):
            if not client.collection_exists(name):
                self.stdout.write(FAIL + f"컬렉션 없음: {name}")
                self.stdout.write("        → python manage.py init_qdrant")
                return False
            count = client.get_collection(name).points_count
            self.stdout.write(OK + f"{name}: {count}개 포인트")
            if not count:
                empty.append(name)
        if empty:
            self.stdout.write(
                WARN + f"{', '.join(empty)}가 비어 있습니다. 리트리버는 후보 0건을 "
                "돌려주고 오늘의 룩은 EMPTY가 됩니다 (실패가 아닙니다)."
            )
        return True

    def _report_presentation_groups(self, client) -> None:
        """성별 표현 그룹 분포. 성별 하드 필터가 무엇을 거를지 미리 보여준다.

        라벨이 없는 코디는 성별 필터가 켜진 사용자에게 전부 걸러진다. 그 사실을
        모르면 "적재가 안 됐다"와 "라벨이 없다"를 구분하지 못한다.
        """
        from apps.recommend.services.qdrant import GOLDEN_OUTFIT_COLLECTION

        counts: dict[str, int] = {}
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=GOLDEN_OUTFIT_COLLECTION,
                with_payload=["presentation_group"],
                with_vectors=False,
                limit=256,
                offset=offset,
            )
            for point in points:
                label = str((point.payload or {}).get("presentation_group") or "")
                counts[label or "(미분류)"] = counts.get(label or "(미분류)", 0) + 1
            if offset is None:
                break

        if not counts:
            return
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        self.stdout.write(OK + f"성별 표현 그룹: {summary}")
        unlabelled = counts.get("(미분류)", 0)
        if unlabelled:
            self.stdout.write(
                WARN + f"{unlabelled}건이 미분류입니다. 성별이 등록된 사용자에게는 "
                "이 코디들이 전부 걸러집니다."
            )
            self.stdout.write(
                "        → 태깅을 먼저 돌린 뒤 동기화하세요: "
                "./run_goldenset_tagging.sh (API 서버) → ./run_goldenset_sync.sh (GPU 서버)"
            )

    # ── 5 ──────────────────────────────────────────────
    def _check_retriever(self, options) -> bool:
        """체형 정보 없이 훑기만 해본다. 여기서 0건이면 골든셋 적재부터 봐야 한다."""
        from apps.recommend.services.retriever import RetrievalRequest, retrieve_outfits

        candidates = retrieve_outfits(RetrievalRequest(limit=3))
        if not candidates:
            self.stdout.write(
                WARN + "필터 없이 검색해도 후보가 0건입니다. 골든 코디가 적재되지 "
                "않았거나 dataset_version이 다릅니다."
            )
            return True
        self.stdout.write(OK + f"후보 {len(candidates)}건")
        for candidate in candidates:
            self.stdout.write(
                f"        golden_id={candidate.golden_id} score={candidate.score} "
                f"items={len(candidate.items)}"
            )
        return True

    def _report_body_rules(self, profile) -> None:
        """이 체형에 어떤 규칙이 걸리는지, 그 규칙이 쓰는 축이 무엇인지 보여준다.

        규칙이 아무리 많아도 코디 payload에 그 축이 없으면 전부 0점이다.
        실제로 그래서 모든 체형이 같은 추천을 받았다 — 규칙 수만 보면 멀쩡해
        보이므로 **어떤 축을 쓰는지**까지 같이 찍는다.
        """
        from apps.recommend.services.style_rules import load_body_rules

        axis = load_body_rules().for_profile(profile)
        fields: set[str] = set()
        for rule in axis.prefer + axis.avoid:
            fields.update(rule.match.keys())
        self.stdout.write(
            f"        적용 규칙: 선호 {len(axis.prefer)}개 / 기피 {len(axis.avoid)}개 "
            f"(사용 축: {sorted(fields) or '없음'})"
        )
        if not axis.prefer and not axis.avoid:
            self.stdout.write(
                WARN + "이 체형에 걸리는 규칙이 없습니다. 실루엣·BMI가 모두 "
                "미판정이면 체형은 추천에 전혀 반영되지 않습니다."
            )

    # ── 6 ──────────────────────────────────────────────
    def _check_queue(self, options) -> bool:
        from apps.recommend.services import queue as queue_service

        spec = queue_service.DAILY_LOOK
        try:
            client = queue_service.get_client()
            client.ping()
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(FAIL + f"Redis 접속 실패: {exc}")
            self.stdout.write(
                "        → 행은 QUEUED로 남고 워커가 못 집습니다. "
                "복구: manage.py run_daily_look_worker --sweep"
            )
            return False
        pending = client.llen(spec.pending)
        processing = client.llen(spec.processing)
        dead = client.llen(spec.dead)
        self.stdout.write(
            OK + f"pending={pending} processing={processing} dead={dead} ({spec.pending})"
        )
        if dead:
            self.stdout.write(WARN + f"dead queue에 {dead}건이 쌓여 있습니다.")
        if pending and not processing:
            self.stdout.write(
                WARN + "대기 중인 작업이 있는데 처리 중이 없습니다. 워커가 떠 있는지 "
                "확인하세요: manage.py run_daily_look_worker"
            )
        return True

    # ── 7 ──────────────────────────────────────────────
    def _check_gemini(self, options) -> bool:
        from django.conf import settings

        if not settings.GEMINI_API_KEY:
            self.stdout.write(FAIL + "GEMINI_API_KEY가 비어 있습니다.")
            self.stdout.write("        → 후보는 찾아도 문장 생성에서 FAILED가 됩니다.")
            return False
        self.stdout.write(
            OK + f"모델 {settings.GEMINI_MODEL} / 타임아웃 {settings.GEMINI_TIMEOUT_SECONDS}s"
        )
        return True

    # ── 8 ──────────────────────────────────────────────
    def _check_user_flow(self, options) -> None:
        from apps.recommend.services.body_profile import build_profile
        from apps.recommend.services.daily_look import ensure_today_look, today
        from apps.recommend.services.outfit_context import build_analysis_context
        from apps.recommend.services.retriever import (
            RetrievalRequest,
            retrieve_outfits,
        )

        user = get_user_model().objects.filter(pk=options["user_id"]).first()
        if user is None:
            self.stdout.write(FAIL + f"user_id={options['user_id']} 사용자가 없습니다.")
            return

        context = build_analysis_context(user, lat=None, lon=None)
        profile = build_profile(context.get("body"))
        self.stdout.write(f"        체형: {profile.describe()}")
        self.stdout.write(f"        판정에 쓴 치수: {list(profile.known) or '없음'}")
        self.stdout.write(f"        빠진 치수: {list(profile.missing) or '없음'}")
        self._report_body_rules(profile)
        self.stdout.write(f"        날씨: {context.get('weather')}")
        self.stdout.write(
            f"        추구미: {'있음' if context.get('pursuit') else '없음'}"
        )

        from apps.recommend.services.gender import (
            allowed_presentation_groups,
            normalize_gender,
        )

        raw_gender = (context.get("body") or {}).get("gender")
        gender = normalize_gender(raw_gender)
        groups = allowed_presentation_groups(gender)
        self.stdout.write(
            f"        성별: {gender or '미지정'} (원본 {raw_gender!r}) "
            f"→ 허용 presentation_group: {list(groups) or '없음'}"
        )
        if not groups:
            self.stdout.write(
                WARN + "성별이 없으면 오늘의 룩은 EMPTY로 끝납니다 (추천을 만들지 "
                "않습니다). PUT /users/me/body/basic 으로 성별을 저장하세요."
            )
        candidates = retrieve_outfits(
            RetrievalRequest(
                body=profile,
                pursuit=context.get("pursuit"),
                weather=context.get("weather"),
                gender=gender,
                limit=5,
            )
        )
        self.stdout.write(f"        이 사용자 기준 후보: {len(candidates)}건")
        for candidate in candidates:
            group = str(candidate.payload.get("presentation_group") or "(미분류)")
            self.stdout.write(
                f"          golden_id={candidate.golden_id} score={candidate.score} "
                f"group={group}"
            )
            # 축별 합계를 보여준다. 가중치를 조정하려면 "무엇이 순위를
            # 정하고 있는지"를 숫자로 봐야 한다 — 체형 점수가 계절·날씨에
            # 묻히는지 여기서 바로 갈린다.
            by_source: dict[str, float] = {}
            for reason in candidate.reasons:
                by_source[reason.source] = by_source.get(reason.source, 0.0) + reason.delta
            if by_source:
                summary = " ".join(f"{k}={v:+.0f}" for k, v in sorted(by_source.items()))
                self.stdout.write(f"            축별: {summary}")
            for reason in candidate.reasons[:4]:
                self.stdout.write(f"            {reason.delta:+5.0f} [{reason.source}] {reason.text}")
            if not candidate.reasons:
                self.stdout.write(
                    "            (근거 없음 — 체형 규칙이 하나도 매칭되지 않았습니다)"
                )
        if not candidates:
            self.stdout.write(
                WARN + "후보 0건 → 오늘의 룩은 EMPTY로 끝납니다. 4번의 포인트 수와 "
                "체형·기피 조건을 다시 보세요."
            )

        if options["dry_run"]:
            self.stdout.write("        (--dry-run: 행 생성·큐 적재는 건너뜁니다)")
            return

        if options["regenerate"]:
            from apps.recommend.models import DailyLook

            deleted, _ = DailyLook.objects.filter(
                user=user, look_date=today()
            ).delete()
            self.stdout.write(
                OK + f"오늘 행 {deleted}건 삭제 (재생성용). 출력 이미지는 S3에 남아 "
                "있어 다시 만들지 않고 재사용합니다."
            )

        look, created = ensure_today_look(user)
        self.stdout.write(
            OK + f"ensure_today_look: {'새로 생성' if created else '이미 있음'} "
            f"look_id={look.pk} status={look.status} date={look.look_date}"
        )
        if not created:
            self.stdout.write(
                f"        → 오늘({today()}) 행이 이미 있어 로그인해도 다시 만들지 "
                "않습니다. 다시 시험하려면 --regenerate를 붙이세요."
            )
