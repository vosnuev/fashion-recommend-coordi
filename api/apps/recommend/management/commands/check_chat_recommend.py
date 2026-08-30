"""채팅 추천이 골든 코디를 찾을 수 있는 상태인지 단계별로 점검한다.

    python manage.py check_chat_recommend
    python manage.py check_chat_recommend --user-id 3        # 그 사용자 성별까지 반영
    python manage.py check_chat_recommend --query "비 오는 날 출근룩"

check_daily_look과 왜 따로 있는가. 저쪽의 리트리버 점검은 `RetrievalRequest(limit=3)`
으로, **dataset_version·dataset_status 필터를 아예 걸지 않는다.** 채팅은 반대로
CHAT_GOLDENSET_DATASET_VERSION·CHAT_GOLDENSET_DATASET_STATUSES를 must 조건으로 건다.
그래서 "check_daily_look은 후보 N건이라고 하는데 채팅만 0건"이 성립하고, 실제로
그 상태가 한동안 운영에서 발견되지 않았다.

채팅에서 후보가 0건이면 파이프라인이 GoldenOutfitNotFound를 던지고, 그 예외는
오케스트레이터의 응답 메시지 생성 앞에서 터진다. 즉 **답변 메시지 자체가 만들어지지
않은 채 run이 FAILED로 끝난다.** 사용자에게는 "대답을 못 만든다"와 "골든셋을 못
찾는다"가 동시에 보이지만 원인은 하나다.

이 커맨드는 그 하나의 원인을 좁힌다. 설정값을 읽어 보여주는 데서 그치지 않고,
적재된 payload의 실제 분포를 세어 **설정과 대조하고**, 필터를 한 겹씩 쌓아가며
어느 조건에서 건수가 0으로 떨어지는지 짚는다.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

OK = "  [OK]  "
FAIL = "  [실패] "
WARN = "  [주의] "

#: payload 분포를 셀 때 한 번에 긁어올 포인트 수.
_SCROLL_BATCH = 256


class Command(BaseCommand):
    help = "채팅 추천이 골든 코디를 검색할 수 있는 상태인지 단계별로 점검한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--user-id",
            type=int,
            help="이 사용자의 성별을 반영해 성별 하드 필터까지 확인",
        )
        parser.add_argument(
            "--query",
            default="",
            help="텍스트 검색까지 확인할 질의문 (임베딩 API가 설정된 경우)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.recommend.services import build_stamp

        self.stdout.write(self.style.MIGRATE_HEADING("0. 이 컨테이너의 추천 코드 지문"))
        self.stdout.write(OK + build_stamp.describe())
        self.stdout.write(
            "        → 채팅 추천을 실제로 만드는 건 chat-worker입니다. 지문이 다르면 "
            "로직이 아니라 배포 문제입니다 (docker compose logs chat-worker | head)."
        )

        steps = (
            ("1. 채팅 골든셋 설정", self._check_settings),
            ("2. Qdrant 연결·컬렉션", self._check_qdrant),
            ("3. 적재된 payload 실측 분포", self._check_payload),
            ("4. 필터 단계별 생존 건수", self._check_filter_layers),
            ("5. 질의 임베딩 설정", self._check_text_embedding),
            ("6. 채팅과 같은 조건으로 실제 검색", self._check_retrieval),
        )
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

        self.stdout.write(self.style.SUCCESS("\n점검 완료."))

    # ── 1 ──────────────────────────────────────────────
    def _check_settings(self, options) -> bool:
        """채팅이 must로 거는 두 값. 비어 있으면 필터가 통째로 빠진다."""
        from django.conf import settings

        from apps.goldenset.models import GoldenDataset

        version = settings.CHAT_GOLDENSET_DATASET_VERSION
        statuses = tuple(settings.CHAT_GOLDENSET_DATASET_STATUSES)

        self.stdout.write(
            OK + f"CHAT_GOLDENSET_DATASET_VERSION={version or '(비어 있음)'}"
        )
        self.stdout.write(
            OK + f"CHAT_GOLDENSET_DATASET_STATUSES={list(statuses) or '(비어 있음)'}"
        )
        if not version or not statuses:
            self.stdout.write(
                WARN + "비어 있는 값은 필터에서 빠집니다. 로컬에서는 그래서 잘 되고 "
                "운영에서만 0건이 되는 비대칭이 생깁니다 — 운영과 같은 값을 넣고 "
                "다시 돌려 보세요."
            )

        invalid = {
            str(value).strip().upper()
            for value in statuses
            if str(value).strip()
        } - set(GoldenDataset.Status.values)
        if invalid:
            self.stdout.write(
                FAIL + f"지원하지 않는 상태: {sorted(invalid)}. "
                f"허용값은 {list(GoldenDataset.Status.values)}입니다."
            )
            self.stdout.write(
                "        → 예전 기본값 PUBLISHED는 GoldenDataset에 없는 값이라 "
                "어떤 코디와도 매칭되지 않습니다. ACTIVE로 바꾸세요."
            )
            return False
        return True

    # ── 2 ──────────────────────────────────────────────
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

        for name in (GOLDEN_OUTFIT_COLLECTION, GOLDEN_ITEM_COLLECTION):
            if not client.collection_exists(name):
                self.stdout.write(FAIL + f"컬렉션 없음: {name}")
                self.stdout.write("        → python manage.py init_qdrant")
                return False
            count = client.get_collection(name).points_count
            self.stdout.write(OK + f"{name}: {count}개 포인트")
            if name == GOLDEN_OUTFIT_COLLECTION and not count:
                self.stdout.write(
                    FAIL + "골든 코디가 한 건도 없습니다. 필터를 아무리 맞춰도 0건입니다."
                )
                self.stdout.write(
                    "        → GPU 서버에서 적재하세요: ./run_goldenset_sync.sh"
                )
                return False
        return True

    # ── 3 ──────────────────────────────────────────────
    def _check_payload(self, options) -> bool:
        """설정값이 아니라 **적재된 값**을 센다. 둘의 차이가 이 이슈의 본체다.

        여기서는 어긋난 축을 '의심'으로만 올리고 멈추지 않는다. 확정은 4단계가
        한다 — 분포만 보고 단정하면 "status도 다르고 version도 다른데 실제로
        어느 쪽이 후보를 죽였나"를 못 가른다.
        """
        from django.conf import settings

        counts = self._scroll_counts(
            ("dataset_version", "status", "dataset_status", "presentation_group")
        )

        version = settings.CHAT_GOLDENSET_DATASET_VERSION
        statuses = {
            str(value).strip().upper()
            for value in settings.CHAT_GOLDENSET_DATASET_STATUSES
            if str(value).strip()
        }

        self.stdout.write(OK + f"dataset_version: {self._summary(counts['dataset_version'])}")
        if version and version not in counts["dataset_version"]:
            self.stdout.write(
                WARN + f"설정한 {version!r} 버전으로 적재된 코디가 없습니다."
            )
            self.stdout.write(
                "        → 적재 시 쓴 GOLDEN_DATASET_VERSION과 같은 값으로 "
                "CHAT_GOLDENSET_DATASET_VERSION을 맞추세요."
            )

        for key in ("status", "dataset_status"):
            self.stdout.write(OK + f"{key}: {self._summary(counts[key])}")
        loaded = {
            str(label).strip().upper()
            for label in (*counts["status"], *counts["dataset_status"])
            if str(label).strip() and label != "(없음)"
        }
        if statuses and loaded and not (statuses & loaded):
            self.stdout.write(
                WARN + f"설정 {sorted(statuses)} 과 적재값 {sorted(loaded)} 이 "
                "하나도 겹치지 않습니다. 상태 필터에서 전량 탈락합니다."
            )
            self.stdout.write(
                "        → 재임베딩 없이 승격: python manage.py "
                f"set_goldenset_qdrant_status --dataset-version {version or '<버전>'} "
                f"--status {sorted(statuses)[0]} --dry-run"
            )

        self.stdout.write(
            OK + f"presentation_group: {self._summary(counts['presentation_group'])}"
        )
        unlabelled = counts["presentation_group"].get("(없음)", 0)
        if unlabelled:
            self.stdout.write(
                WARN + f"{unlabelled}건이 미분류입니다. 성별이 등록된 사용자에게는 "
                "이 코디가 전부 걸러집니다 (게스트만 추천되는 증상)."
            )
            self.stdout.write(
                "        → 태깅 후 동기화: ./run_goldenset_tagging.sh (API 서버) → "
                "./run_goldenset_sync.sh (GPU 서버)"
            )
        return True

    def _scroll_counts(self, fields: tuple[str, ...]) -> dict[str, dict[str, int]]:
        from apps.recommend.services.qdrant import GOLDEN_OUTFIT_COLLECTION, get_client

        client = get_client()
        counts: dict[str, dict[str, int]] = {field: {} for field in fields}
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=GOLDEN_OUTFIT_COLLECTION,
                with_payload=list(fields),
                with_vectors=False,
                limit=_SCROLL_BATCH,
                offset=offset,
            )
            for point in points:
                payload = point.payload or {}
                for field in fields:
                    label = str(payload.get(field) or "") or "(없음)"
                    counts[field][label] = counts[field].get(label, 0) + 1
            if offset is None:
                break
        return counts

    @staticmethod
    def _summary(counts: dict[str, int]) -> str:
        if not counts:
            return "(포인트 없음)"
        return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))

    # ── 4 ──────────────────────────────────────────────
    def _check_filter_layers(self, options) -> bool:
        """필터를 한 겹씩 쌓으며 센다. 몇 번째 줄에서 0이 되는지가 곧 원인이다."""
        from django.conf import settings

        from apps.recommend.services.qdrant import GOLDEN_OUTFIT_COLLECTION, get_client
        from apps.recommend.services.retriever import RetrievalRequest, build_filter

        gender = self._gender(options)
        version = settings.CHAT_GOLDENSET_DATASET_VERSION
        statuses = tuple(settings.CHAT_GOLDENSET_DATASET_STATUSES)

        layers: list[tuple[str, RetrievalRequest]] = [
            ("필터 없음", RetrievalRequest()),
            (
                f"+ dataset_version={version or '(미설정)'}",
                RetrievalRequest(dataset_version=version),
            ),
            (
                f"+ dataset_statuses={list(statuses) or '(미설정)'}",
                RetrievalRequest(dataset_version=version, dataset_statuses=statuses),
            ),
            (
                f"+ 성별={gender or '(미지정)'}",
                RetrievalRequest(
                    dataset_version=version,
                    dataset_statuses=statuses,
                    gender=gender,
                ),
            ),
        ]

        client = get_client()
        previous: int | None = None
        culprit = ""
        for label, request in layers:
            search_filter = build_filter(request)
            count = int(
                client.count(
                    collection_name=GOLDEN_OUTFIT_COLLECTION,
                    count_filter=search_filter,
                    exact=True,
                ).count
            )
            marker = OK if count else FAIL
            delta = "" if previous is None else f" (직전 대비 {count - previous:+d})"
            self.stdout.write(marker + f"{label}: {count}건{delta}")
            if not count and previous:
                culprit = culprit or label
            previous = count

        if culprit:
            self.stdout.write(
                FAIL + f"'{culprit}' 조건에서 후보가 전부 사라집니다. "
                "이 조건이 채팅 실패의 직접 원인입니다."
            )
            return False
        if previous == 0:
            self.stdout.write(
                FAIL + "필터 없이도 0건입니다. 컬렉션에 골든 코디가 적재되지 않았습니다."
            )
            return False
        return True

    # ── 5 ──────────────────────────────────────────────
    def _check_text_embedding(self, options) -> bool:
        """0건의 원인은 아니지만, 없으면 사용자 문장이 검색에 전혀 반영되지 않는다."""
        from django.conf import settings

        if not settings.TEXT_EMBEDDING_API_URL:
            self.stdout.write(
                WARN + "TEXT_EMBEDDING_API_URL이 비어 있습니다. 파이프라인이 텍스트 "
                "벡터 검색을 포기하고 필터 스크롤로 떨어집니다."
            )
            self.stdout.write(
                "        → 후보가 0건이 되지는 않지만, 무엇을 물어도 같은 코디가 "
                "나옵니다. GPU 스택의 text-embedding-api를 띄우고 값을 채우세요."
            )
            return True
        self.stdout.write(
            OK + f"{settings.TEXT_EMBEDDING_API_URL} "
            f"(dim={settings.TEXT_EMBEDDING_EXPECTED_DIM}, "
            f"timeout={settings.TEXT_EMBEDDING_TIMEOUT_SECONDS}s)"
        )
        return True

    # ── 6 ──────────────────────────────────────────────
    def _check_retrieval(self, options) -> bool:
        """채팅 파이프라인과 같은 RetrievalRequest로 실제 점수화까지 돌린다."""
        from django.conf import settings

        from apps.recommend.services.retriever import (
            RetrievalRequest,
            retrieve_outfits,
        )

        gender = self._gender(options)
        candidates = retrieve_outfits(
            RetrievalRequest(
                gender=gender,
                query_text=options["query"],
                dataset_version=settings.CHAT_GOLDENSET_DATASET_VERSION,
                dataset_statuses=tuple(settings.CHAT_GOLDENSET_DATASET_STATUSES),
                limit=5,
                hard_filter=True,
                exposable_only=False,
            )
        )
        if not candidates:
            self.stdout.write(
                FAIL + "후보 0건 — 채팅은 GOLDEN_OUTFIT_NOT_FOUND로 실패하고 "
                "답변 메시지를 만들지 못합니다."
            )
            return False

        self.stdout.write(OK + f"후보 {len(candidates)}건")
        for candidate in candidates:
            group = str(candidate.payload.get("presentation_group") or "(미분류)")
            self.stdout.write(
                f"        golden_id={candidate.golden_id} score={candidate.score} "
                f"group={group} items={len(candidate.payload.get('items') or [])}"
            )
        return True

    # ── 공통 ───────────────────────────────────────────
    def _gender(self, options) -> str:
        """--user-id가 있으면 그 사용자의 성별, 없으면 빈 문자열(제한 없음)."""
        if not options.get("user_id"):
            return ""
        from django.contrib.auth import get_user_model

        from apps.recommend.services.gender import normalize_gender
        from apps.recommend.services.outfit_context import build_analysis_context

        user = get_user_model().objects.filter(pk=options["user_id"]).first()
        if user is None:
            return ""
        context = build_analysis_context(user, lat=None, lon=None)
        return normalize_gender((context.get("body") or {}).get("gender"))
