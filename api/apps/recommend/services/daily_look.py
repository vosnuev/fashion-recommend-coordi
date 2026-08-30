"""오늘의 룩 생성 서비스.

    ensure_today_look()  홈 진입·조회 시점: 그날 행이 없으면 만들고 큐에 넣는다
                         (EMPTY로 끝난 행은 프로필이 바뀌었으면 다시 큐에 넣는다)
    claim() / run()      워커에서: 리트리버 → Gemini → 결과 기록

코디 평가(services/analysis.py)와 뼈대는 같지만 시작점이 다르다. 저쪽은 사용자가
사진을 올려야 시작하고, 이쪽은 **사용자 입력이 없다.** 그날 처음 홈 화면에
들어오는 순간(GET /api/v1/home/) 자동으로 걸리고, 재료는 미리 저장된
체형·추구미와 그 시점 날씨다.

멱등성은 DB가 보장한다. (user, look_date) 유니크 제약이 있으므로 여러 기기에서
동시에 홈을 열어도 행은 하나다. 서비스는 IntegrityError를 '이미 있음'으로 읽는다 —
select 후 insert하는 방식은 그 사이에 다른 요청이 끼어들면 깨진다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.lookbook.contracts import LOOKBOOK_STYLE_TAGS, LOOKBOOK_TPO_TAGS
from apps.recommend.models import DailyLook
from apps.recommend.services import gemini, outfit_render, render_artifacts
from apps.recommend.services import queue as queue_service
from apps.recommend.services.body_profile import build_profile
from apps.recommend.services.gender import normalize_gender
from apps.recommend.services.mixed_outfit_render import (
    OutfitRenderRequest,
    RenderItemReference,
    RenderSource,
)
from apps.recommend.services.outfit_context import (
    build_analysis_context,
    build_profile_context,
)
from apps.recommend.services.retriever import RetrievalRequest, retrieve_outfits
from apps.recommend.services.style_rules import load_body_rules
from apps.recommend.services import vocabulary

logger = logging.getLogger(__name__)

#: LLM에 넘길 후보 수. 너무 많으면 프롬프트가 길어지고 모델이 고르는 근거가 흐려진다.
CANDIDATE_LIMIT = 5

#: 최근 며칠간 나간 코디를 다시 추천하지 않을지. 하루 1건이므로 최대 5개
#: 골든 코디가 제외 대상이 된다.
RECENT_EXCLUDE_DAYS = 5

#: 홈 카드에 다는 태그 최대 개수. 카드 한 줄에 들어가는 한도이고, 넘치면 가로
#: 스크롤이 생겨 "더 있나?"를 만든다 — 태그는 부가 정보라 그럴 값어치가 없다.
MAX_TAGS = 4

#: '다른 룩'으로 돌려볼 차순위 후보 수 (채택된 1위는 별도).
#:
#: 후보마다 착용 이미지를 한 장씩 만들어야 해서 이 값이 곧 생성 비용이다. 다만
#: 이미지는 **골든 코디당 한 번**만 만들고 모든 사용자가 공유하므로, 사용자가
#: 늘어도 비용은 골든셋 크기에서 멈춘다. 2로 둔 근거는 사람이 카드를 돌려보는
#: 횟수다 — 세 벌을 넘기면 대개 그만 본다. 0으로 두면 기능이 꺼진다.
ALTERNATIVE_LIMIT = int(os.getenv("DAILY_LOOK_ALTERNATIVE_LIMIT", "2"))


def today(user=None) -> date:
    """추천이 속한 날짜. 서비스 타임존(Asia/Seoul) 기준의 '오늘'.

    UTC로 계산하면 한국 시간 오전 9시 이전 접속이 전날로 묶여 사용자는
    "어제 룩이 그대로 나온다"고 느낀다.
    """
    return timezone.localdate()


def _recent_golden_ids(user, look_date: date) -> frozenset[str]:
    """이 사용자에게 최근 RECENT_EXCLUDE_DAYS일 동안 **실제로 나간** 골든 코디 id.

    '나간 것'의 기준은 채택된 결과(result.golden_id)다. 후보 목록(candidates)까지
    빼면 하루에 5개씩 소진돼 골든셋이 작을 때 며칠 만에 뺄 코디가 없어진다 —
    사용자가 본 것은 1위 하나뿐이므로 그것만 반복으로 친다.

    오늘 행(look_date 당일)은 넣지 않는다. FAILED 재시도로 같은 날 run()이 다시
    돌 때, 아직 결과도 없는 자기 자신 때문에 후보가 좁아지면 안 된다.
    """
    rows = DailyLook.objects.filter(
        user=user,
        status=DailyLook.Status.SUCCEEDED,
        look_date__gte=look_date - timedelta(days=RECENT_EXCLUDE_DAYS),
        look_date__lt=look_date,
    ).values_list("result", flat=True)
    return frozenset(
        str(row["golden_id"])
        for row in rows
        if isinstance(row, dict) and row.get("golden_id")
    )


def ensure_today_look(user, *, lat: float | None = None, lon: float | None = None):
    """그날 행이 없으면 만들고 큐에 넣는다. 이미 있으면 그대로 돌려준다.

    예외가 하나 있다. EMPTY로 끝난 행은 프로필이 그 뒤로 바뀌었으면 다시 큐에
    넣는다(_requeue_if_profile_changed). created는 그때도 False다 — 행을 새로
    만든 것은 아니기 때문이다.

    Returns: (DailyLook, created)
    """
    look_date = today(user)
    existing = DailyLook.objects.filter(user=user, look_date=look_date).first()
    if existing is not None:
        if existing.status == DailyLook.Status.EMPTY:
            _requeue_if_profile_changed(existing, lat=lat, lon=lon)
        return existing, False

    context = build_analysis_context(user, lat=lat, lon=lon)
    body = context.get("body")
    profile = build_profile(body)

    try:
        with transaction.atomic():
            look = DailyLook.objects.create(
                user=user,
                look_date=look_date,
                status=DailyLook.Status.QUEUED,
                weather=context.get("weather") or {},
                body=body,
                body_profile=_profile_snapshot(profile),
                pursuit=context.get("pursuit"),
            )
    except IntegrityError:
        # 다른 요청이 한 발 먼저 만들었다. 경합은 정상 흐름이므로 조용히 그것을 쓴다.
        existing = DailyLook.objects.filter(user=user, look_date=look_date).first()
        if existing is None:
            raise
        return existing, False

    try:
        queue_service.push(
            {"look_id": str(look.pk)}, spec=queue_service.DAILY_LOOK
        )
        look.enqueued_at = timezone.now()
        look.save(update_fields=["enqueued_at", "updated_at"])
    except Exception:  # noqa: BLE001 — 큐가 죽어도 행은 남기고 워커가 쓸어담는다
        logger.exception("오늘의 룩 %s 큐 적재 실패", look.pk)
    return look, True


def _profile_fingerprint(body: Any, pursuit: Any) -> str:
    """체형·추구미 스냅샷의 지문. 값이 같으면 같은 문자열이 나온다."""
    payload = json.dumps(
        {"body": body, "pursuit": pursuit},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _requeue_if_profile_changed(
    look: DailyLook,
    *,
    lat: float | None,
    lon: float | None,
) -> bool:
    """EMPTY로 끝난 그날의 룩을, 프로필이 바뀌었으면 다시 큐에 넣는다.

    Returns: 다시 걸었으면 True.

    EMPTY의 원인은 대개 미완성 프로필이다 — 성별이 없으면 run()이 아예 만들지
    않고, 조건이 좁으면 후보가 0건이다. 그런데 행은 (user, look_date)에 하루
    한 건이라, 화면이 안내한 대로 프로필을 채우고 홈에 돌아와도 종일 같은 EMPTY
    행이 나갔다. 사용자 입장에서는 하라는 대로 했는데 화면이 그대로다.

    조건은 "프로필이 바뀌었는가" 하나로 둔다.
    - EMPTY라고 무조건 다시 돌리면 홈에 들어올 때마다 리트리버가 도는데,
      입력이 그대로면 결과도 그대로다(비용만 든다).
    - '성별이 채워졌는가'로 좁히면 성별은 있는데 후보가 0건이라 EMPTY가 된
      사용자가 추구미의 기피축을 풀어도 다음 날까지 기다리게 된다.
    - 날씨는 판단에서 뺀다. 분 단위로 바뀌므로 넣으면 매 진입이 곧 재큐잉이다.
      다만 **다시 걸 때는** 그 시점 날씨로 스냅샷을 갱신한다 — 아침에 EMPTY가
      난 룩을 저녁에 다시 만드는데 아침 기온을 쓸 이유가 없다.

    SUCCEEDED·FAILED는 건드리지 않는다. 성공한 룩은 하루 한 벌이라는 약속이
    있고, FAILED는 조회 API 문서가 "자동 재시도하지 않는다"고 못박은 상태다
    (사용자가 '다시 시도'를 누르는 자리가 따로 있다).
    """
    profile = build_profile_context(look.user)
    if _profile_fingerprint(profile["body"], profile["pursuit"]) == _profile_fingerprint(
        look.body, look.pursuit
    ):
        return False

    context = build_analysis_context(look.user, lat=lat, lon=lon)
    body = context.get("body")
    now = timezone.now()
    # 상태 조건을 건 update로 바꾼다. 여러 기기에서 동시에 홈을 열면 같은 행을
    # 두 번 걸 수 있는데, 그러면 워커가 같은 작업을 두 번 돌린다. 생성 때 유니크
    # 제약이 하던 역할을 여기서는 이 조건이 한다.
    changed = DailyLook.objects.filter(
        pk=look.pk, status=DailyLook.Status.EMPTY
    ).update(
        status=DailyLook.Status.QUEUED,
        weather=context.get("weather") or {},
        body=body,
        body_profile=_profile_snapshot(build_profile(body)),
        pursuit=context.get("pursuit"),
        candidates=[],
        error="",
        finished_at=None,
        enqueued_at=None,
        updated_at=now,
    )
    if not changed:
        return False

    try:
        queue_service.push({"look_id": str(look.pk)}, spec=queue_service.DAILY_LOOK)
        DailyLook.objects.filter(pk=look.pk).update(enqueued_at=now, updated_at=now)
    except Exception:  # noqa: BLE001 — 행은 QUEUED로 남고 워커 --sweep이 주워간다
        logger.exception("오늘의 룩 %s 재적재 실패", look.pk)

    # 호출부(홈·조회 API)는 이 인스턴스를 그대로 직렬화한다. 갱신 전 값을 들고
    # 있으면 방금 다시 건 룩이 화면에는 계속 EMPTY로 보인다.
    look.refresh_from_db()
    logger.info("오늘의 룩 %s: 프로필 변경으로 재생성 접수", look.pk)
    return True


def _profile_snapshot(profile) -> dict[str, Any]:
    return {
        "silhouette": profile.silhouette,
        "bmi_band": profile.bmi_band,
        "bmi": profile.bmi,
        "ratios": dict(profile.ratios),
        "known": list(profile.known),
        "missing": list(profile.missing),
        "describe": profile.describe(),
    }


def claim(look_id: str) -> DailyLook | None:
    """작업을 집어 PROCESSING으로 전환한다. 이미 끝난 건이면 None."""
    with transaction.atomic():
        look = DailyLook.objects.select_for_update().filter(pk=look_id).first()
        if look is None:
            return None
        if look.status in DailyLook.TERMINAL_STATUSES:
            # 재시도로 같은 작업이 두 번 올 수 있다. 끝난 건은 다시 만들지 않는다.
            return None
        look.status = DailyLook.Status.PROCESSING
        look.attempts += 1
        look.started_at = timezone.now()
        look.finished_at = None
        look.save(
            update_fields=[
                "status",
                "attempts",
                "started_at",
                "finished_at",
                "updated_at",
            ]
        )
        return look


def run(look: DailyLook) -> None:
    """리트리버로 후보를 뽑고 Gemini에 코디 구성·근거 생성을 맡긴다."""
    from apps.recommend.services.body_profile import BodyProfile, canonical_silhouette

    snapshot = look.retrieval_context()
    saved = snapshot.get("body_profile") or {}
    profile = BodyProfile(
        silhouette=canonical_silhouette(saved.get("silhouette", "unknown")),
        bmi_band=saved.get("bmi_band", "unknown"),
        bmi=saved.get("bmi"),
        ratios=dict(saved.get("ratios") or {}),
    )

    rules = load_body_rules()
    gender = normalize_gender((snapshot.get("body") or {}).get("gender"))

    # 성별을 모르면 추천을 만들지 않는다.
    #
    # 예전에는 그냥 필터 없이 검색해서, 성별이 비어 있는 사용자에게 아무 코디나
    # 나갔다. 사용자 입장에서 그건 "덜 맞는 추천"이 아니라 틀린 추천이다 —
    # 남성에게 여성복이 나오면 기능 전체의 신뢰가 무너진다. 검색 계층은 범용이라
    # 제약을 스스로 만들지 않으므로, 그 판단은 오늘의 룩이 여기서 내린다.
    if not gender:
        look.status = DailyLook.Status.EMPTY
        look.finished_at = timezone.now()
        look.rules_version = rules.schema_version
        look.candidates = []
        look.error = (
            "성별 정보가 없어 오늘의 룩을 만들지 않았습니다. "
            "체형 정보(PUT /users/me/body/basic)에 성별을 저장한 뒤 다시 로그인하세요."
        )
        look.save(update_fields=["candidates", "rules_version", "status", "error",
                                 "finished_at", "updated_at"])
        logger.warning("오늘의 룩 %s: 성별 미상으로 생성 중단", look.pk)
        return

    candidates = retrieve_outfits(
        RetrievalRequest(
            body=profile,
            pursuit=snapshot.get("pursuit"),
            weather=snapshot.get("weather"),
            gender=gender,
            limit=CANDIDATE_LIMIT,
            # 최근 며칠 안에 이미 나간 코디는 top k에서 빼고 다음 순위로 채운다.
            # 골든셋·규칙이 그대로면 순위도 그대로라, 이게 없으면 매일 같은
            # 코디가 1위로 뽑혀 "오늘의" 룩이 아니게 된다.
            exclude_golden_ids=_recent_golden_ids(look.user, look.look_date),
        ),
        rules=rules,
    )

    look.candidates = [_candidate_snapshot(c) for c in candidates]
    look.rules_version = rules.schema_version

    if not candidates:
        # 실패와 구분한다. 프론트는 "잠시 후 다시"가 아니라 "프로필을 채워주세요"를
        # 띄워야 하고, 워커는 재시도해봐야 같은 결과다.
        look.status = DailyLook.Status.EMPTY
        look.finished_at = timezone.now()
        # 무엇이 없어서 0건인지 남긴다. 사용자에게는 다 똑같이 "추천 없음"이지만,
        # 운영자에게는 '적재가 안 됐다'와 '이 사용자 조건이 좁다'가 전혀 다른 문제다.
        avoided = (snapshot.get("pursuit") or {}).get("avoided") or {}
        look.error = (
            "조건에 맞는 골든 코디 후보가 없습니다 "
            f"(성별={gender or '미지정'}, "
            f"체형={profile.silhouette}/{profile.bmi_band}, "
            f"기피축={sorted(k for k, v in avoided.items() if v) or '없음'})"
        )
        look.save(update_fields=["candidates", "rules_version", "status", "error",
                                 "finished_at", "updated_at"])
        logger.info("오늘의 룩 %s: 후보 0건", look.pk)
        return

    # ── 여기까지가 추천의 성립 조건이다 ──────────────────────
    # 코디는 리트리버가 정한다. 1위를 그대로 채택하고, 문장이 붙기 전에 상태를
    # SUCCEEDED로 확정한다. 예전에는 Gemini가 죽으면 FAILED가 되어, 멀쩡히 찾아둔
    # 코디가 있는데도 사용자는 아무것도 못 봤다.
    chosen = candidates[0]
    look.result = _build_result(chosen, snapshot)
    # '다른 룩'으로 돌려볼 차순위 후보. result와 같은 스키마로 만들어 두면 프론트가
    # 카드 한 벌을 그리는 코드를 그대로 재사용한다.
    #
    # 문장은 템플릿으로 둔다(generated_by=template). LLM을 후보 수만큼 부르면
    # 호출이 세 배가 되는데, 사용자가 '다른 룩'을 눌러 실제로 읽는 문장은 대개
    # 한 벌치다. 대표 룩만 다듬는다.
    look.alternatives = [
        _build_result(c, snapshot) for c in candidates[1 : 1 + ALTERNATIVE_LIMIT]
    ]
    look.status = DailyLook.Status.SUCCEEDED
    look.finished_at = timezone.now()
    look.error = ""
    look.save(
        update_fields=["candidates", "rules_version", "result", "alternatives",
                       "status", "error", "finished_at", "updated_at"]
    )

    # ── 여기부터는 있으면 좋은 것 ────────────────────────────
    # 셋 다 실패해도 추천은 이미 SUCCEEDED다. 화면은 아이템 카드로 성립한다.
    _attach_render(look, chosen, gender)
    _enrich_with_copy(look, chosen, snapshot)
    # 후보 이미지는 **여기서 만들지 않는다.** 대표 룩 한 장에도 수십 초가 걸리는데
    # 후보까지 이어 만들면 워커가 1대 고정이라 뒷사람 추천이 그만큼 밀린다.
    _schedule_alternative_renders(look)


def _daily_render_request(
    look: DailyLook,
    gender: str,
) -> OutfitRenderRequest | None:
    return _render_request(look.result or {}, f"daily-look:{look.pk}", gender)


def _render_request(
    result: dict[str, Any],
    composition_id: str,
    gender: str,
) -> OutfitRenderRequest | None:
    """결과 JSON 하나를 공통 렌더 파이프라인의 요청으로 옮긴다.

    대표 룩과 '다른 룩' 후보가 같은 함수를 쓴다. 지문은 아이템 구성과 모델 성별로만
    만들어지므로(composition_id는 안 들어간다) 같은 코디는 누가 어느 자리에서
    요청하든 이미지 한 장을 공유한다 — 후보로 나갔던 코디가 다음 날 1위가 돼도
    다시 만들지 않는다.
    """
    items = result.get("items") or []
    references = []
    for position, item in enumerate(items, start=1):
        image_ref = str(item.get("s3_key") or "").strip()
        if not image_ref:
            continue
        references.append(
            RenderItemReference(
                item_id=str(item.get("item_key") or position),
                position=position,
                slot=f"{item.get('category') or 'ITEM'}:{position}",
                source_type=RenderSource.GOLDENSET_ITEM,
                image_ref=image_ref,
                source_bucket=str(item.get("s3_bucket") or ""),
            )
        )
    if not references:
        return None

    presentation = {"male": "man", "female": "woman"}.get(
        normalize_gender(gender),
        "",
    )
    contract = {
        "golden_id": result.get("golden_id", ""),
        "presentation": presentation,
        "items": [
            {
                "position": row.position,
                "item_id": row.item_id,
                "image_ref": row.image_ref,
                "source_bucket": row.source_bucket,
            }
            for row in references
        ],
    }
    raw = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return OutfitRenderRequest(
        composition_id=composition_id,
        composition_fingerprint=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        items=tuple(references),
        subject_presentation=presentation,
    )


def _common_render_image(
    result: dict[str, Any], composition_id: str, gender: str
) -> dict[str, Any] | None:
    """공통 렌더 파이프라인(채팅 추천과 같은 S3·지문 캐시)으로 만들어 참조만 돌려준다."""
    request = _render_request(result, composition_id, gender)
    if request is None or not settings.OUTFIT_RENDER_RESULT_BUCKET:
        return None
    try:
        entry, _ = render_artifacts.get_or_render(request)
    except Exception:
        logger.warning(
            "%s 공통 렌더 실패, 기존 daily 렌더러로 대체합니다.",
            composition_id,
            exc_info=True,
        )
        return None
    return {
        "s3_bucket": entry.output_s3_bucket,
        "s3_key": entry.output_s3_key,
        "media_type": entry.output_media_type,
        "render_fingerprint": entry.render_fingerprint,
    }


def _attach_common_render(look: DailyLook, gender: str) -> bool:
    """채팅 추천과 같은 결과 S3·지문 캐시 계약으로 오늘의 룩을 렌더한다."""
    image = _common_render_image(look.result or {}, f"daily-look:{look.pk}", gender)
    if image is None:
        return False
    result = dict(look.result)
    result["render_image"] = image
    look.result = result
    look.save(update_fields=["result", "updated_at"])
    return True


def _attach_render(look: DailyLook, candidate, gender: str = "") -> None:
    """정면 착용 이미지를 붙인다. 이미 만들어 둔 코디면 생성 없이 참조만 얻는다.

    성별을 함께 넘긴다. 유니섹스 코디는 남녀 모두에게 추천되므로 그 사용자에
    맞는 모델로 그려야 하고, 성별별로 따로 저장·재사용한다.
    """
    if _attach_common_render(look, gender):
        return

    payload = candidate.payload
    try:
        reference = outfit_render.ensure_render(
            bucket=str(payload.get("source_bucket", "")),
            items=list(payload.get("items", [])),
            gender=gender,
        )
    except Exception as exc:  # noqa: BLE001 — 이미지 실패가 추천을 되돌리면 안 된다
        logger.warning("오늘의 룩 %s 착용 이미지 실패: %s", look.pk, exc)
        look.error = f"착용 이미지 생성 실패(추천은 정상): {exc}"[:2000]
        look.save(update_fields=["error", "updated_at"])
        return

    if reference is None:
        return
    result = dict(look.result)
    result["render_image"] = reference.as_dict()
    look.result = result
    look.save(update_fields=["result", "updated_at"])


# ── 조회 시점의 착용 이미지 보정 ──────────────────────────
#
# 착용 이미지 생성은 생성 시점에 실패해도 다음 시행에서 성공하는 일이 잦다
# (제공자 일시 오류·타임아웃). 그런데 결과 JSON은 생성이 끝날 때 한 번만 쓰이므로,
# 그 한 번이 실패하면 이미지가 S3에 생긴 뒤에도 행은 계속 비어 있다. 사용자는
# 그날 내내 대표 이미지를 못 본다.
#
# 그래서 조회할 때마다 한 번 더 본다. 조회는 폴링으로 자주 들어오므로 두 가지를
# 분리했다.
#
#   1. 이미 S3에 있는가  → HEAD 한두 번. 조회 경로에서 바로 확인하고 붙인다.
#   2. 아직 없다         → 생성은 수십 초라 요청을 잡아둘 수 없다. 큐에 넣되
#                          쿨다운을 걸어 폴링마다 재생성이 쌓이지 않게 한다.

#: 같은 코디의 재생성을 다시 걸기까지의 최소 간격. 프론트가 2초마다 폴링해도
#: 이 간격 안에서는 한 번만 걸린다. 락은 코디(= 착용 이미지 키) 단위라 같은
#: 코디를 받은 여러 사용자가 동시에 눌러도 생성은 한 번이다.
RENDER_RETRY_COOLDOWN_SECONDS = int(
    os.getenv("DAILY_LOOK_RENDER_RETRY_COOLDOWN_SECONDS", "600")
)

#: 큐 페이로드의 작업 종류. 없으면 기존처럼 전체 생성이다 (하위호환).
JOB_RENDER = "render"

#: '다른 룩' 후보들의 착용 이미지만 만드는 작업.
JOB_RENDER_ALTERNATIVES = "render_alternatives"

#: 후보 이미지 생성을 다시 걸기까지의 최소 간격. 락은 룩 행 단위다 — 대표 룩의
#: 재생성 락(코디 단위)과 달리, 여기서 만드는 것은 여러 코디라 하나로 묶는다.
ALTERNATIVE_RENDER_COOLDOWN_SECONDS = int(
    os.getenv("DAILY_LOOK_ALTERNATIVE_RENDER_COOLDOWN_SECONDS", "600")
)


def _render_source(result: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """결과 JSON에서 착용 이미지 생성에 필요한 재료를 되살린다.

    Qdrant를 다시 조회하지 않는다. 결과에 이미 아이템의 버킷·키·분류가 들어
    있고, 벡터스토어는 재적재로 내용이 바뀔 수 있어 그때의 기록이 더 정확하다.

    _build_result()가 category_large를 `category`로 줄여 담으므로 여기서 원래
    이름으로 되돌린다 — 참조를 고를 때 그 값으로 우선순위를 매긴다.
    """
    items = result.get("items") or []
    bucket = ""
    restored: list[dict[str, Any]] = []
    for item in items:
        if not item.get("s3_key"):
            continue
        bucket = bucket or str(item.get("s3_bucket") or "")
        restored.append(
            {
                "s3_key": item.get("s3_key"),
                "s3_bucket": item.get("s3_bucket"),
                "category_large": item.get("category"),
                "item_name": item.get("name"),
            }
        )
    return bucket, restored


def refresh_render(look: DailyLook) -> bool:
    """조회 시점에 착용 이미지를 한 번 더 확인해 붙인다.

    Returns: 행을 갱신했으면 True.

    생성은 하지 않는다. 이 함수는 사용자의 요청 스레드에서 돌고, 이미지 생성은
    수십 초가 걸린다. 대신 이미 만들어져 있으면 붙이고, 없으면 재생성을 큐에
    맡긴다.
    """
    if look.status != DailyLook.Status.SUCCEEDED:
        return False
    result = look.result or {}
    if not result or result.get("render_image"):
        return False

    common_request = _daily_render_request(
        look,
        normalize_gender((look.body or {}).get("gender")),
    )
    if common_request is not None and settings.OUTFIT_RENDER_RESULT_BUCKET:
        try:
            render_fingerprint = render_artifacts.fingerprint(
                common_request.composition_fingerprint,
                common_request.subject_presentation,
            )
            if entry := render_artifacts.find_cached(render_fingerprint):
                result = dict(result)
                result["render_image"] = {
                    "s3_bucket": entry.output_s3_bucket,
                    "s3_key": entry.output_s3_key,
                    "media_type": entry.output_media_type,
                    "render_fingerprint": entry.render_fingerprint,
                }
                look.result = result
                look.error = (
                    ""
                    if look.error.startswith("착용 이미지 생성 실패")
                    else look.error
                )
                look.save(update_fields=["result", "error", "updated_at"])
                return True
        except Exception:
            logger.warning(
                "오늘의 룩 %s 공통 렌더 캐시 확인 실패", look.pk, exc_info=True
            )

    bucket, items = _render_source(result)
    if not bucket or not items:
        return False

    gender = normalize_gender((look.body or {}).get("gender"))
    reference = outfit_render.existing_render(
        bucket, str(items[0]["s3_key"]), gender
    )
    if reference is None:
        _schedule_render_retry(look, bucket, str(items[0]["s3_key"]), gender)
        return False

    result = dict(result)
    result["render_image"] = reference.as_dict()
    look.result = result
    fields = ["result", "updated_at"]
    # 이미지가 붙었으면 그때의 실패 메시지는 사실이 아니다. 남겨두면 운영자가
    # 멀쩡한 행을 계속 문제로 읽는다.
    if look.error.startswith("착용 이미지 생성 실패"):
        look.error = ""
        fields.append("error")
    look.save(update_fields=fields)
    logger.info(
        "오늘의 룩 %s 착용 이미지 보정: s3://%s/%s",
        look.pk, reference.s3_bucket, reference.s3_key,
    )
    return True


def _schedule_render_retry(
    look: DailyLook, bucket: str, item_key: str, gender: str = ""
) -> None:
    """아직 없으면 재생성을 큐에 건다. 쿨다운 안에서는 한 번만.

    락이 없으면 프론트 폴링(기본 2초)마다 생성 작업이 쌓여 요금이 폭주한다.
    락 키를 사용자가 아니라 **착용 이미지 키**로 잡는 이유는, 같은 골든 코디를
    받은 사용자가 여럿이어도 만들 이미지는 하나이기 때문이다.
    """
    if not settings.DAILY_LOOK_RENDER_ENABLED:
        return
    # 성별을 키에 넣는다. 같은 코디라도 남성용·여성용 이미지는 별개라,
    # 하나로 묶으면 한쪽이 쿨다운에 막혀 영영 안 만들어진다.
    lock_key = f"daily_look:render_retry:{bucket}:{item_key}:{gender or 'none'}"
    try:
        client = queue_service.get_client()
        acquired = client.set(
            lock_key, "1", nx=True, ex=RENDER_RETRY_COOLDOWN_SECONDS
        )
        if not acquired:
            return
        queue_service.push(
            {"look_id": str(look.pk), "job": JOB_RENDER}, spec=queue_service.DAILY_LOOK
        )
    except Exception:  # noqa: BLE001 — 보정 실패가 조회를 막으면 안 된다
        logger.warning("오늘의 룩 %s 착용 이미지 재생성 예약 실패", look.pk, exc_info=True)
        return
    logger.info("오늘의 룩 %s 착용 이미지 재생성 예약", look.pk)


def run_render_only(look_id: str) -> bool:
    """워커에서: 추천은 건드리지 않고 착용 이미지만 다시 만든다.

    claim()을 쓰지 않는다. 그 함수는 SUCCEEDED면 None을 돌려주는데, 여기서
    다루는 건 정확히 **이미 성공한 행**이다. 상태도 바꾸지 않는다 — 이미지가
    없다고 해서 사용자에게 '생성 중'을 다시 보여줄 이유가 없다.

    Returns: 이미지를 붙였으면 True.
    """
    look = DailyLook.objects.filter(pk=look_id).first()
    if look is None or look.status != DailyLook.Status.SUCCEEDED:
        return False
    result = look.result or {}
    if result.get("render_image"):
        return False

    gender = normalize_gender((look.body or {}).get("gender"))
    if _attach_common_render(look, gender):
        return True

    bucket, items = _render_source(result)
    if not bucket or not items:
        return False

    try:
        reference = outfit_render.ensure_render(
            bucket=bucket,
            items=items,
            gender=gender,
        )
    except Exception as exc:  # noqa: BLE001 — 이미지 실패가 추천을 되돌리면 안 된다
        logger.warning("오늘의 룩 %s 착용 이미지 재생성 실패: %s", look.pk, exc)
        look.error = f"착용 이미지 생성 실패(추천은 정상): {exc}"[:2000]
        look.save(update_fields=["error", "updated_at"])
        return False

    if reference is None:
        return False

    result = dict(result)
    result["render_image"] = reference.as_dict()
    look.result = result
    fields = ["result", "updated_at"]
    if look.error.startswith("착용 이미지 생성 실패"):
        look.error = ""
        fields.append("error")
    look.save(update_fields=fields)
    logger.info("오늘의 룩 %s 착용 이미지 재생성 완료", look.pk)
    return True


def _build_tags(payload: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    """룩북과 **같은 어휘**로 카드 태그를 만든다 (apps/lookbook/contracts.py).

    예전에는 프론트가 아이템 이름을 `#`만 붙여 태그처럼 보이게 했다. 그러면
    `#블랙스트레이트데님팬츠` 같은 덩어리가 나오고, 룩북 필터 칩과 어휘가 달라
    같은 서비스 안에서 태그라는 말이 두 가지를 뜻하게 된다.

    출처는 두 단계다.

    1. **골든 코디 자신의 라벨** — Qdrant `outfit_goldenset` payload 의
       `occasion`(TPO) / `style`(스타일). 무엇을 추천했는지 그대로 말하는 값이라
       가장 정확하다. 축이 다르므로 어휘도 갈라서 검사한다: occasion 에서 온
       "미니멀"이나 style 에서 온 "출근"은 라벨링 사고이므로 통과시키지 않는다.
    2. **사용자 추구미** — `pursuit.preferred.styles`(영문 코드)를
       `vocabulary.STYLE` 로 한글화. 1번이 비었을 때만 쓴다.

    2번이 필요한 이유: 골든 코디의 season/style/occasion 은 analyses.jsonl 에서
    오는데 그 분석이 유료라 기본으로 꺼져 있어, 적재된 포인트가 전부 빈 배열인
    시기가 있었다(retriever.py 의 하드 필터 주석과 같은 사정). 추구미는 프로필
    입력이라 골든셋 태깅 상태와 무관하게 채워져 있어 화면이 성립한다.
    다만 2번으로 내려가면 **같은 사용자에게 매일 같은 태그**가 붙는다 — 태그가
    늘 똑같다면 그건 골든셋 태깅이 비었다는 신호로 읽으면 된다.

    하나도 못 만들면 빈 배열을 돌려준다. 프론트는 이때 태그 줄을 통째로 숨긴다 —
    어색한 태그를 지어내는 것보다 없는 편이 낫다.
    """
    tags: list[str] = []

    def add(values: Any, allowed: frozenset[str]) -> None:
        for raw in values or []:
            label = str(raw).strip()
            if label in allowed and label not in tags:
                tags.append(label)

    add(payload.get("occasion"), LOOKBOOK_TPO_TAGS)
    add(payload.get("style"), LOOKBOOK_STYLE_TAGS)

    if not tags:
        preferred = (snapshot.get("pursuit") or {}).get("preferred") or {}
        # translate()는 카테고리 전체를 받지만 여기서는 스타일 축만 쓴다.
        # 색·핏까지 넣으면 룩북 어휘에 없는 라벨만 잔뜩 만들고 전부 버리게 된다.
        translated = vocabulary.translate({"styles": preferred.get("styles") or []})
        add(sorted(translated.tags.get("style") or ()), LOOKBOOK_STYLE_TAGS)

    return tags[:MAX_TAGS]


def _build_result(candidate, snapshot: dict[str, Any]) -> dict[str, Any]:
    """LLM 없이도 화면을 그릴 수 있는 결과를 만든다.

    이미지 URL은 **넣지 않는다.** presigned URL은 만료되므로 조회 시점에
    만들어야 한다 — DB에 구워 넣으면 며칠 뒤 죽은 링크가 남는다. 대신 버킷과
    키를 담아 두고 직렬화 단계가 서명한다.

    아이템 이미지는 원본 사진이 아니라 파이프라인이 만든 흰 배경 파생물이라,
    원본이 노출 불가(exposable=False)여도 보여줄 수 있다.
    """
    payload = candidate.payload
    bucket = str(payload.get("source_bucket", ""))
    rule_notes = [r.text for r in candidate.reasons if r.source == "rule"]

    return {
        "golden_id": candidate.golden_id,
        "tags": _build_tags(payload, snapshot),
        "headline": _template_headline(snapshot),
        "rationale_ko": _template_rationale(rule_notes, snapshot),
        "styling_tips": [],
        # 문장을 누가 썼는지 프론트가 알 수 있게 한다 (템플릿이면 담백한 톤이다).
        "generated_by": "template",
        # 정면 착용 이미지. 코디당 한 번만 만들고 재사용하므로, 여기서는
        # 자리만 비워 두고 _attach_render()가 채운다.
        "render_image": None,
        # 원본 코디 사진은 사용권이 열린 것만 내보낸다.
        "outfit_image": (
            {"s3_bucket": bucket, "s3_key": str(payload.get("source_key", ""))}
            if payload.get("exposable") and payload.get("source_key")
            else None
        ),
        "items": [
            {
                "item_key": item.get("item_key", ""),
                "name": item.get("item_name", ""),
                "category": item.get("category_large", ""),
                "sub_category": item.get("category_small", ""),
                "layer_role": item.get("layer_role", ""),
                "color": item.get("color", ""),
                "s3_bucket": bucket,
                "s3_key": item.get("s3_key", ""),
                "note": "",
            }
            for item in payload.get("items", [])
        ],
    }


def _template_headline(snapshot: dict[str, Any]) -> str:
    weather = snapshot.get("weather") or {}
    temperature = weather.get("temperature")
    if temperature is None:
        return "오늘의 추천 코디"
    return f"{round(float(temperature))}도, 오늘은 이렇게"


def _template_rationale(rule_notes: list[str], snapshot: dict[str, Any]) -> str:
    """규칙표 문장을 이어 붙인다.

    규칙표의 reason이 이미 한국어 문장이라 그대로 재료가 된다. LLM이 붙으면 이걸
    더 자연스럽게 다듬는 것이지, 없다고 못 쓸 내용은 아니다.
    """
    profile = snapshot.get("body_profile") or {}
    parts: list[str] = []
    if describe := profile.get("describe"):
        parts.append(f"{describe} 기준으로 골랐어요.")
    if rule_notes:
        # 같은 근거가 여러 아이템에서 나올 수 있어 앞의 두 개만 쓴다.
        parts.append(" ".join(note.rstrip(".") + "." for note in rule_notes[:2]))
    weather = snapshot.get("weather") or {}
    if (temperature := weather.get("temperature")) is not None:
        parts.append(f"기온은 {round(float(temperature))}도입니다.")
    return " ".join(parts) or "오늘 조건에 맞춰 골랐어요."


def _enrich_with_copy(look: DailyLook, candidate, snapshot: dict[str, Any]) -> None:
    """문장을 LLM으로 다듬는다. 실패해도 추천은 그대로 남는다.

    매일 같은 사용자에게 나가는 기능이라 템플릿만으로는 사흘이면 "또 같은 말"이
    된다. 그래서 LLM을 쓰되, 없어도 기능이 성립하도록 순서를 뒤에 뒀다.
    """
    try:
        copy = gemini.write_daily_look_copy(
            outfit=_candidate_for_llm(candidate), context=snapshot
        )
    except Exception as exc:  # noqa: BLE001 — 문장 실패가 추천을 되돌리면 안 된다
        logger.warning("오늘의 룩 %s 문장 생성 실패 (템플릿 유지): %s", look.pk, exc)
        look.error = f"문장 생성 실패(추천은 정상): {type(exc).__name__}: {exc}"[:2000]
        look.save(update_fields=["error", "updated_at"])
        return

    notes = {
        str(row.get("item_key")): str(row.get("note", ""))
        for row in copy.parsed.get("items") or []
    }
    result = dict(look.result)
    result.update(
        {
            "headline": copy.parsed.get("headline") or result["headline"],
            "rationale_ko": copy.parsed.get("rationale_ko") or result["rationale_ko"],
            "styling_tips": copy.parsed.get("styling_tips") or [],
            "generated_by": "llm",
        }
    )
    for item in result["items"]:
        item["note"] = notes.get(item["item_key"], "")

    look.result = result
    look.llm_model = copy.model
    look.llm_request = copy.request
    look.llm_response = copy.response
    look.llm_latency_ms = copy.latency_ms
    look.save(
        update_fields=["result", "llm_model", "llm_request", "llm_response",
                       "llm_latency_ms", "updated_at"]
    )


def _candidate_snapshot(candidate) -> dict[str, Any]:
    """DB에 남길 후보 요약. 벡터와 payload 전체는 넣지 않는다 (행이 비대해진다)."""
    return {
        "point_id": candidate.point_id,
        "golden_id": candidate.golden_id,
        "score": candidate.score,
        "similarity": candidate.similarity,
        # 성별 사고가 재발하면 이 한 칸만 보면 된다. 후보에 무엇이 통과했는지
        # 남기지 않았던 탓에 지난번엔 Qdrant를 따로 뒤져야 했다.
        "presentation_group": str(candidate.payload.get("presentation_group") or ""),
        "reasons": [
            {"source": r.source, "delta": r.delta, "text": r.text}
            for r in candidate.reasons
        ],
        "item_keys": list(candidate.payload.get("item_keys", [])),
    }


# ── '다른 룩' 후보의 착용 이미지 ────────────────────────────
#
# 후보도 카드로 그려지므로 대표 룩과 같은 이미지가 필요하다. 다만 생성 시점을
# 추천 경로에서 떼어냈다. 대표 룩 한 장에도 수십 초가 걸리는데 후보까지 이어
# 만들면, 워커가 1대 고정이라 그동안 뒷사람의 추천이 통째로 밀린다.
#
# 이미지는 **골든 코디당 한 장**이고 지문 캐시·S3 키가 모두 코디 기준이라,
# 후보로 나갔던 코디가 다음 날 다른 사용자의 1위가 되어도 다시 만들지 않는다.


def _missing_alternative_renders(look: DailyLook) -> list[int]:
    """착용 이미지가 아직 없는 후보의 인덱스. 참조로 쓸 아이템이 없으면 세지 않는다."""
    return [
        index
        for index, alternative in enumerate(look.alternatives or [])
        if isinstance(alternative, dict)
        and not alternative.get("render_image")
        and alternative.get("items")
    ]


def _schedule_alternative_renders(look: DailyLook) -> None:
    """후보 착용 이미지 생성을 큐에 건다. 쿨다운 안에서는 한 번만.

    락이 없으면 프론트 폴링마다 작업이 쌓여 요금이 폭주한다(대표 룩 재생성과
    같은 사고). 락 키는 **룩 행 단위**다 — 대표 룩의 락은 코디 단위지만 여기서
    만드는 것은 여러 코디라 하나로 묶는 편이 세기 쉽다.
    """
    if not settings.DAILY_LOOK_RENDER_ENABLED:
        return
    if not _missing_alternative_renders(look):
        return
    try:
        client = queue_service.get_client()
        acquired = client.set(
            f"daily_look:alt_render:{look.pk}",
            "1",
            nx=True,
            ex=ALTERNATIVE_RENDER_COOLDOWN_SECONDS,
        )
        if not acquired:
            return
        queue_service.push(
            {"look_id": str(look.pk), "job": JOB_RENDER_ALTERNATIVES},
            spec=queue_service.DAILY_LOOK,
        )
    except Exception:  # noqa: BLE001 — 후보 이미지 예약 실패가 추천을 되돌리면 안 된다
        logger.warning("오늘의 룩 %s 후보 착용 이미지 예약 실패", look.pk, exc_info=True)
        return
    logger.info("오늘의 룩 %s 후보 착용 이미지 생성 예약", look.pk)


def _render_image_for(
    result: dict[str, Any], composition_id: str, gender: str
) -> dict[str, Any] | None:
    """결과 하나의 착용 이미지를 보장한다. 이미 있으면 만들지 않고 참조만 얻는다."""
    if image := _common_render_image(result, composition_id, gender):
        return image
    bucket, items = _render_source(result)
    if not bucket or not items:
        return None
    reference = outfit_render.ensure_render(bucket=bucket, items=items, gender=gender)
    return reference.as_dict() if reference is not None else None


def _existing_render_image(
    result: dict[str, Any], composition_id: str, gender: str
) -> dict[str, Any] | None:
    """**생성하지 않고** 이미 만들어져 있는 것만 찾는다 (조회 경로용)."""
    request = _render_request(result, composition_id, gender)
    if request is not None and settings.OUTFIT_RENDER_RESULT_BUCKET:
        try:
            entry = render_artifacts.find_cached(
                render_artifacts.fingerprint(
                    request.composition_fingerprint, request.subject_presentation
                )
            )
        except Exception:  # noqa: BLE001
            entry = None
            logger.warning("%s 공통 렌더 캐시 확인 실패", composition_id, exc_info=True)
        if entry is not None:
            return {
                "s3_bucket": entry.output_s3_bucket,
                "s3_key": entry.output_s3_key,
                "media_type": entry.output_media_type,
                "render_fingerprint": entry.render_fingerprint,
            }

    bucket, items = _render_source(result)
    if not bucket or not items:
        return None
    reference = outfit_render.existing_render(bucket, str(items[0]["s3_key"]), gender)
    return reference.as_dict() if reference is not None else None


def _save_alternatives(look: DailyLook, alternatives: list[dict[str, Any]]) -> None:
    """후보 배열만 갱신한다.

    ``look.save()`` 를 쓰지 않는 이유: 조회 경로(refresh_render)가 같은 순간
    요청 스레드에서 result 를 다시 쓰고 있을 수 있다. 인스턴스를 통째로 저장하면
    이 워커가 들고 있는 낡은 result 로 그 갱신을 덮는다.
    """
    DailyLook.objects.filter(pk=look.pk).update(
        alternatives=alternatives, updated_at=timezone.now()
    )
    look.alternatives = alternatives


def run_alternative_renders(look_id: str) -> int:
    """워커에서: '다른 룩' 후보들의 착용 이미지를 만든다.

    Returns: 새로 채운 개수.

    후보 하나가 실패해도 나머지는 만든다. 부가 기능이라 전부-아니면-전무로
    다룰 이유가 없고, 한 코디의 참조 이미지가 깨졌다고 다른 코디까지 못 볼
    까닭도 없다.
    """
    look = DailyLook.objects.filter(pk=look_id).first()
    if look is None or look.status != DailyLook.Status.SUCCEEDED:
        return 0

    gender = normalize_gender((look.body or {}).get("gender"))
    alternatives = list(look.alternatives or [])
    filled = 0
    for index in _missing_alternative_renders(look):
        alternative = dict(alternatives[index])
        golden_id = str(alternative.get("golden_id") or index)
        try:
            image = _render_image_for(
                alternative, f"daily-look:{look.pk}:alt:{golden_id}", gender
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "오늘의 룩 %s 후보 %s 착용 이미지 실패: %s", look.pk, golden_id, exc
            )
            continue
        if image is None:
            continue
        alternative["render_image"] = image
        alternatives[index] = alternative
        filled += 1

    if filled:
        _save_alternatives(look, alternatives)
        logger.info("오늘의 룩 %s 후보 착용 이미지 %d장 완료", look.pk, filled)
    return filled


def refresh_alternatives(look: DailyLook) -> bool:
    """조회 시점에 후보 착용 이미지를 한 번 더 확인해 붙인다.

    Returns: 행을 갱신했으면 True.

    대표 룩의 refresh_render 와 같은 이유다 — 한 번 실패한 생성이 다음 시행에
    성공해도, 결과 JSON 은 그때 한 번만 쓰이므로 행은 계속 비어 있다. 여기서도
    생성은 하지 않고(수십 초) 이미 있는지만 보고, 없으면 큐에 맡긴다.
    """
    if look.status != DailyLook.Status.SUCCEEDED:
        return False
    missing = _missing_alternative_renders(look)
    if not missing:
        return False

    gender = normalize_gender((look.body or {}).get("gender"))
    alternatives = list(look.alternatives or [])
    filled = 0
    for index in missing:
        alternative = dict(alternatives[index])
        golden_id = str(alternative.get("golden_id") or index)
        try:
            image = _existing_render_image(
                alternative, f"daily-look:{look.pk}:alt:{golden_id}", gender
            )
        except Exception:  # noqa: BLE001 — 보정 실패가 조회를 막으면 안 된다
            logger.warning(
                "오늘의 룩 %s 후보 %s 착용 이미지 확인 실패",
                look.pk, golden_id, exc_info=True,
            )
            continue
        if image is None:
            continue
        alternative["render_image"] = image
        alternatives[index] = alternative
        filled += 1

    if filled:
        _save_alternatives(look, alternatives)
    # 아직 없는 후보가 남아 있으면 생성을 예약한다 (쿨다운 안에서는 한 번만).
    _schedule_alternative_renders(look)
    return bool(filled)


class GoldenLookNotInTodayError(Exception):
    """오늘 이 사용자에게 나가지 않은 코디를 지목한 경우.

    '아직 안 됐다'(생성 중)와 다르다. 폴링해도 달라지지 않고, 대개 어제 화면을
    열어 둔 채 버튼을 누른 것이다.
    """

    def __init__(self, golden_id: str) -> None:
        super().__init__(golden_id)
        self.golden_id = golden_id


def pick_result(look: DailyLook, golden_id: str = "") -> dict[str, Any]:
    """그날의 룩 하나를 고른다. 대표 룩이거나 '다른 룩' 후보 중 하나다.

    **클라이언트가 고르되 목록은 서버가 정한다.** golden_id를 그대로 믿으면 남의
    코디도, 어제의 코디도 집힌다. 그래서 이 사용자의 오늘 행에 실제로 실려 나간
    것들(result + alternatives) 안에서만 찾는다.

    저장(룩북)과 가상 피팅이 같은 함수를 쓴다 — 두 기능이 서로 다른 룩을 고르면
    사용자는 화면에서 본 것과 다른 결과를 받는다.

    Raises: GoldenLookNotInTodayError
    """
    result = look.result or {}
    golden_id = (golden_id or "").strip()
    if not golden_id or golden_id == str(result.get("golden_id") or ""):
        return result
    for alternative in look.alternatives or []:
        if isinstance(alternative, dict) and str(
            alternative.get("golden_id") or ""
        ) == golden_id:
            return alternative
    raise GoldenLookNotInTodayError(golden_id)


def _candidate_for_llm(candidate) -> dict[str, Any]:
    """LLM 프롬프트에 넣을 형태. 이미지 대신 태그와 근거만 넘긴다.

    골든 원본은 대개 exposable=False라 사용자에게 그대로 보여줄 수 없다. 모델도
    사진을 볼 필요가 없다 — 조합과 근거를 말로 풀어내는 일이라 태그로 충분하고,
    멀티모달 호출보다 훨씬 싸다.
    """
    payload = candidate.payload
    return {
        "golden_id": candidate.golden_id,
        "score": candidate.score,
        "style": payload.get("style", []),
        "season": payload.get("season", []),
        "occasion": payload.get("occasion", []),
        "items": [
            {
                "item_key": item.get("item_key"),
                "name": item.get("item_name"),
                "category": item.get("category_large"),
                "sub_category": item.get("category_small"),
                "layer_role": item.get("layer_role"),
                "color": item.get("color"),
            }
            for item in payload.get("items", [])
        ],
        "rule_notes": [r.text for r in candidate.reasons if r.source == "rule"],
        "preference_notes": [
            r.text for r in candidate.reasons if r.source == "preference"
        ],
    }


def mark_failed(look: DailyLook, error: str) -> None:
    look.status = DailyLook.Status.FAILED
    look.error = error[:2000]
    look.finished_at = timezone.now()
    look.save(update_fields=["status", "error", "finished_at", "updated_at"])
