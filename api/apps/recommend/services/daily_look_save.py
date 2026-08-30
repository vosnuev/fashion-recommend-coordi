"""오늘의 룩 '저장' — 그날의 추천을 사용자 룩북에 담는다.

**골든 코디를 가리키기만 한다.** 사진 룩북(create_from_photo)은 사용자 사진을
S3에 올리고 옷장 파이프라인을 태우지만, 여기서 담는 것은 이미 골든셋 버킷에
있는 코디다. 복사하면 같은 사진이 담은 사용자 수만큼 늘어나고, GPU 파이프라인을
태우면 이미 태깅이 끝난 옷을 다시 태깅하게 된다. 둘 다 순수한 낭비다.

그래서 이 경로에는 처리 상태가 없다 — 저장은 DB 쓰기 한 번으로 끝나고
LookbookPost.status 는 처음부터 COMPLETED 다.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from apps.lookbook.models import LookbookPost
from apps.lookbook.services import lookbook_service
from apps.recommend.models import DailyLook
from apps.recommend.services import daily_look as daily_look_service

logger = logging.getLogger(__name__)


class DailyLookNotSavableError(Exception):
    """아직(또는 영영) 담을 수 없는 추천.

    status 를 그대로 들고 다닌다 — 뷰가 "생성 중이에요"와 "추천이 없어요"를
    갈라 안내해야 하고, 그 판단 근거는 프론트가 폴링에 쓰는 값과 같아야 한다.
    """

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


#: 오늘 나가지 않은 코디를 담으려 한 경우. 정의는 daily_look 에 있다 — 저장과
#: 가상 피팅이 **같은 규칙으로** 룩을 고르기 위해서다(daily_look.pick_result).
#: 이 이름으로도 쓸 수 있게 남겨 둔다(뷰·테스트가 여기서 잡는다).
GoldenLookNotInTodayError = daily_look_service.GoldenLookNotInTodayError


def _image_ref(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", ""
    return str(value.get("s3_bucket") or ""), str(value.get("s3_key") or "")


def _cover_image(result: dict[str, Any]) -> tuple[str, str]:
    """룩북 카드의 표지. 홈 카드와 **같은 우선순위**를 쓴다.

    착용 이미지 → 원본 코디 사진 → 첫 아이템 사진. 홈에서 보고 담았는데 룩북에
    다른 사진이 서 있으면 같은 룩인지 알아볼 수 없다.

    착용 이미지가 아직 만들어지지 않은 시점에 담으면 표지는 아이템 사진으로
    굳는다. 담는 순간의 모습을 남기는 것이 이 기능의 뜻이라 나중에 덮어쓰지
    않는다 — 사용자가 담아 둔 카드가 어느 날 갑자기 다른 사진이 되는 편이 더 나쁘다.
    """

    for key in ("render_image", "outfit_image"):
        bucket, s3_key = _image_ref(result.get(key))
        if s3_key:
            return bucket, s3_key
    for item in result.get("items") or []:
        bucket, s3_key = _image_ref(item)
        if s3_key:
            return bucket, s3_key
    return "", ""


def _items(result: dict[str, Any]) -> list[lookbook_service.GoldenLookItem]:
    items: list[lookbook_service.GoldenLookItem] = []
    for raw in result.get("items") or []:
        if not isinstance(raw, dict):
            continue
        items.append(
            lookbook_service.GoldenLookItem(
                item_key=str(raw.get("item_key") or ""),
                name=str(raw.get("name") or ""),
                category=str(raw.get("category") or ""),
                sub_category=str(raw.get("sub_category") or ""),
                layer_role=str(raw.get("layer_role") or ""),
                color=str(raw.get("color") or ""),
                s3_bucket=str(raw.get("s3_bucket") or ""),
                s3_key=str(raw.get("s3_key") or ""),
            )
        )
    return items


def save_to_lookbook(
    user, *, look_date: date | None = None, golden_id: str = ""
) -> tuple[LookbookPost, bool]:
    """그날의 오늘의 룩을 룩북에 담는다.

    `golden_id`를 주면 '다른 룩'으로 돌려보던 그 후보를 담는다. 생략하면 대표 룩.

    Returns: (룩북, 새로 담았는지). 이미 담아 둔 코디면 (기존 룩북, False).

    Raises:
        DailyLookNotSavableError — 추천이 없거나 아직 완성되지 않은 경우.
        GoldenLookNotInTodayError — 오늘 이 사용자에게 나가지 않은 코디.
    """

    look_date = look_date or daily_look_service.today(user)
    look = DailyLook.objects.filter(user=user, look_date=look_date).first()
    if look is None:
        raise DailyLookNotSavableError("MISSING")
    if look.status != DailyLook.Status.SUCCEEDED:
        raise DailyLookNotSavableError(look.status)

    # 담기 직전에 착용 이미지를 한 번 더 확인한다. 생성은 하지 않는다(수십 초).
    # 홈이 조회한 뒤 워커가 이미지를 마저 만들어 뒀을 수 있고, 그 몇 초 차이로
    # 표지가 아이템 사진으로 굳으면 사용자는 이유를 알 수 없다.
    try:
        daily_look_service.refresh_render(look)
        # '다른 룩'을 담는 경우 표지는 후보 쪽에 있다. 같은 이유로 한 번 더 본다.
        daily_look_service.refresh_alternatives(look)
    except Exception:  # noqa: BLE001 — 표지 보정 실패가 저장을 막으면 안 된다
        logger.warning("오늘의 룩 %s 표지 보정 실패 (저장은 계속)", look.pk)

    result = daily_look_service.pick_result(look, golden_id)
    chosen_golden_id = str(result.get("golden_id") or "")
    if not chosen_golden_id:
        # SUCCEEDED 인데 golden_id 가 없다면 결과 JSON 이 깨진 것이다. 담아 봐야
        # 어느 코디인지 되짚을 수 없으므로 담지 않는다.
        logger.error("오늘의 룩 %s: SUCCEEDED 인데 golden_id 가 없습니다", look.pk)
        raise DailyLookNotSavableError("MISSING")

    bucket, image_key = _cover_image(result)
    return lookbook_service.create_from_golden_look(
        user=user,
        golden_id=chosen_golden_id,
        image_bucket=bucket,
        image_key=image_key,
        # 룩북 카드의 문구. 홈 카드와 룩 상세가 쓰는 값과 같다.
        schedule=str(result.get("headline") or ""),
        # 오늘의 룩이 이미 룩북 어휘(LOOKBOOK_TAGS)로 만들어 둔 태그다.
        # 여기서 다시 만들면 두 화면의 태그가 갈린다.
        hashtags=list(result.get("tags") or []),
        items=_items(result),
    )
