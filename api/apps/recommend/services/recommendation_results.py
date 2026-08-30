"""추천 카드 조회와 피드백 변경을 위한 소유권 경계 서비스."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Prefetch, QuerySet
from django.utils import timezone

from apps.chat.models import ChatIdentity
from apps.catalog.models import ElevenProduct, NaverProduct
from apps.lookbook.contracts import recommendation_card_lookbook_id
from apps.lookbook.models import LookbookPost
from apps.lookbook.services import lookbook_service
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    ProductClickEvent,
    RecommendationFeedback,
    RecommendationResult,
    SavedOutfit,
    WishlistItem,
)
from apps.recommend.services.qdrant import collection_spec

PRODUCT_CLICK_DEDUPLICATION_WINDOW = timedelta(minutes=5)


def _snapshot_text(snapshot: dict, *keys: str) -> str:
    for key in keys:
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)):
            joined = ", ".join(str(item).strip() for item in value if str(item).strip())
            if joined:
                return joined
    return ""


def _item_image(item: OutfitCompositionItem) -> tuple[str, str]:
    snapshot = item.item_snapshot if isinstance(item.item_snapshot, dict) else {}
    bucket = _snapshot_text(snapshot, "image_s3_bucket", "s3_bucket")
    key = _snapshot_text(snapshot, "image_s3_key", "s3_key")
    if not key and item.image_ref and not item.image_ref.startswith(("http://", "https://")):
        key = item.image_ref
    return bucket, key


def _save_card_to_lookbook(*, composition: OutfitComposition, user) -> None:
    """검증 추천 카드의 현재 모습을 실제 룩북 스냅샷으로 멱등 저장한다."""

    items = list(composition.items.all().order_by("position", "created_at"))
    cover_bucket = ""
    cover_key = ""
    try:
        render = composition.render_job
    except OutfitComposition.render_job.RelatedObjectDoesNotExist:
        render = None
    if render is not None and render.status == render.Status.SUCCEEDED:
        cover_bucket = render.output_s3_bucket
        cover_key = render.output_s3_key
    if not cover_key:
        for item in items:
            cover_bucket, cover_key = _item_image(item)
            if cover_key:
                break

    snapshots: list[lookbook_service.GoldenLookItem] = []
    for item in items:
        raw = item.item_snapshot if isinstance(item.item_snapshot, dict) else {}
        bucket, key = _item_image(item)
        snapshots.append(
            lookbook_service.GoldenLookItem(
                item_key=str(item.source_id),
                name=_snapshot_text(
                    raw,
                    "display_name",
                    "product_name",
                    "item_name",
                    "name",
                    "title",
                ),
                category=_snapshot_text(raw, "category_large") or item.slot,
                sub_category=_snapshot_text(raw, "category_small"),
                layer_role=_snapshot_text(raw, "layer_role"),
                color=_snapshot_text(raw, "color"),
                s3_bucket=bucket,
                s3_key=key,
            )
        )

    lookbook_service.create_from_golden_look(
        user=user,
        golden_id=recommendation_card_lookbook_id(composition.pk),
        image_bucket=cover_bucket,
        image_key=cover_key,
        schedule=(
            composition.rationale
            or composition.result.persona_explanation
            or "추천받은 코디"
        ),
        items=snapshots,
    )


def _public_compositions() -> QuerySet[OutfitComposition]:
    """검증을 통과해 사용자에게 노출 가능한 카드만 반환한다."""
    return (
        OutfitComposition.objects.filter(status=OutfitComposition.Status.VALIDATED)
        .select_related("feedback")
        .prefetch_related("items", "saved_records")
        .order_by("rank", "created_at")
    )


def owned_results(identity: ChatIdentity) -> QuerySet[RecommendationResult]:
    return (
        RecommendationResult.objects.filter(identity=identity)
        .select_related("session", "run")
        .prefetch_related(
            Prefetch(
                "compositions",
                queryset=_public_compositions(),
                to_attr="public_compositions",
            )
        )
        .order_by("-created_at")
    )


def owned_result(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
) -> RecommendationResult | None:
    return owned_results(identity).filter(pk=result_id).first()


def owned_card(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
    card_id: uuid.UUID,
) -> OutfitComposition | None:
    return (
        _public_compositions()
        .filter(
            pk=card_id,
            result_id=result_id,
            result__identity=identity,
        )
        .first()
    )


@transaction.atomic
def put_feedback(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
    card_id: uuid.UUID,
    reaction: str,
    reason_codes: list[str],
    comment: str,
) -> tuple[RecommendationFeedback | None, bool]:
    """소유 카드의 피드백을 생성하거나 전체 교체한다."""
    composition = (
        OutfitComposition.objects.select_for_update()
        .filter(
            pk=card_id,
            result_id=result_id,
            result__identity=identity,
            status=OutfitComposition.Status.VALIDATED,
        )
        .first()
    )
    if composition is None:
        return None, False
    feedback, created = RecommendationFeedback.objects.update_or_create(
        composition=composition,
        defaults={
            "reaction": reaction,
            "reason_codes": reason_codes,
            "comment": comment,
        },
    )
    return feedback, created


@transaction.atomic
def delete_feedback(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
    card_id: uuid.UUID,
) -> bool:
    deleted, _ = RecommendationFeedback.objects.filter(
        composition_id=card_id,
        composition__result_id=result_id,
        composition__result__identity=identity,
        composition__status=OutfitComposition.Status.VALIDATED,
    ).delete()
    return deleted > 0


@transaction.atomic
def save_outfit(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
    card_id: uuid.UUID,
) -> tuple[SavedOutfit | None, bool]:
    """회원이 소유한 검증 완료 코디를 멱등 저장한다."""

    if identity.user_id is None:
        return None, False
    composition = (
        OutfitComposition.objects.select_for_update()
        .select_related("result__identity")
        .filter(
            pk=card_id,
            result_id=result_id,
            result__identity=identity,
            status=OutfitComposition.Status.VALIDATED,
        )
        .first()
    )
    if composition is None:
        return None, False
    saved_outfit, created = SavedOutfit.objects.get_or_create(
        user_id=identity.user_id,
        composition=composition,
    )
    _save_card_to_lookbook(composition=composition, user=identity.user)
    return saved_outfit, created


@transaction.atomic
def delete_saved_outfit(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
    card_id: uuid.UUID,
) -> bool:
    """소유 카드가 존재하면 저장 여부와 관계없이 멱등 해제한다."""

    if identity.user_id is None:
        return False
    composition_exists = OutfitComposition.objects.filter(
        pk=card_id,
        result_id=result_id,
        result__identity=identity,
        status=OutfitComposition.Status.VALIDATED,
    ).exists()
    if not composition_exists:
        return False
    SavedOutfit.objects.filter(
        user_id=identity.user_id,
        composition_id=card_id,
    ).delete()
    LookbookPost.objects.filter(
        user_id=identity.user_id,
        golden_id=recommendation_card_lookbook_id(card_id),
    ).delete()
    return True


@transaction.atomic
def record_product_click(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
    card_id: uuid.UUID,
    item_id: uuid.UUID,
) -> tuple[ProductClickEvent | None, bool]:
    """소유 추천의 판매 상품 클릭을 5분 중복 제거 구간으로 수집한다."""

    if identity.user_id is None:
        return None, False
    item = (
        OutfitCompositionItem.objects.select_for_update()
        .select_related("composition__result__identity")
        .filter(
            pk=item_id,
            composition_id=card_id,
            composition__result_id=result_id,
            composition__result__identity=identity,
            composition__status=OutfitComposition.Status.VALIDATED,
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
        )
        .first()
    )
    if item is None:
        return None, False

    cutoff = timezone.now() - PRODUCT_CLICK_DEDUPLICATION_WINDOW
    existing = (
        ProductClickEvent.objects.filter(
            user_id=identity.user_id,
            item=item,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing, False

    result = item.composition.result
    event = ProductClickEvent.objects.create(
        user_id=identity.user_id,
        item=item,
        result_id_snapshot=result.id,
        composition_id_snapshot=item.composition_id,
        persona_id=result.persona_id,
        source_collection=item.source_collection,
        source_id=item.source_id,
    )
    return event, True


@transaction.atomic
def update_product_click_engagement(
    *,
    identity: ChatIdentity,
    product_click_id: uuid.UUID,
    duration_ms: int,
) -> ProductClickEvent | None:
    """회원 소유 클릭의 근사 체류 시간을 재시도에 안전하게 갱신한다."""

    if identity.user_id is None:
        return None
    event = (
        ProductClickEvent.objects.select_for_update()
        .filter(pk=product_click_id, user_id=identity.user_id)
        .first()
    )
    if event is None:
        return None
    if (
        event.engagement_duration_ms is not None
        and event.engagement_duration_ms >= duration_ms
    ):
        return event
    event.engagement_duration_ms = duration_ms
    event.engagement_recorded_at = timezone.now()
    event.save(
        update_fields=["engagement_duration_ms", "engagement_recorded_at"]
    )
    return event


# ── 찜(판매 상품) ────────────────────────────────────────────────

def _snapshot_text(snapshot: object, *keys: str) -> str:
    """아이템 스냅샷에서 처음 채워진 값을 꺼낸다. (serializers 의 같은 이름 함수와 같은 규칙)"""

    if not isinstance(snapshot, dict):
        return ""
    for key in keys:
        value = snapshot.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _product_source(item: OutfitCompositionItem) -> str:
    """상품이 어느 카탈로그에서 왔는지 — 컬렉션 이름으로 가른다.

    ``source_collection`` 은 추천 당시에 적어 둔 **스냅샷**이라, 컬렉션을 v2 로
    올리면 지금 설정값과 더 이상 같지 않다. 그래서 현재 이름을 먼저 맞춰 보고,
    어긋나면 이름에 남아 있는 몰 이름으로 판단한다 — 여기서 못 가르면 브랜드·
    판매처를 못 채울 뿐이라 담기 자체는 계속되게 둔다.
    """

    collection = item.source_collection or ""
    if collection == collection_spec("products_naver").name:
        return "naver"
    if collection == collection_spec("products_eleven").name:
        return "eleven"
    lowered = collection.casefold()
    if "naver" in lowered:
        return "naver"
    if "eleven" in lowered:
        return "eleven"
    return ""


def _catalog_fields(item: OutfitCompositionItem) -> dict[str, object]:
    """카탈로그 원본에서 브랜드·링크·가격을 채운다.

    추천 응답에는 브랜드가 없어서 앱이 상품명만으로 검색 주소를 만들고 있었다.
    담는 순간 원본을 한 번 읽어 두면 찜 목록은 정확한 상품으로 나갈 수 있다.
    원본을 못 찾아도 담기는 실패시키지 않는다 — 스냅샷만으로도 목록은 선다.
    """

    source = _product_source(item)
    if source == "naver":
        product = NaverProduct.objects.filter(naver_product_id=item.source_id).first()
        if product is not None:
            return {
                "brand": product.brand or "",
                "purchase_url": product.link or "",
                "image_ref": product.image_url or item.image_ref,
                "price_snapshot": item.price_snapshot or product.lprice,
                "display_name": product.title or "",
            }
    elif source == "eleven":
        product = ElevenProduct.objects.filter(
            eleven_product_id=item.source_id
        ).first()
        if product is not None:
            return {
                # 11번가 카탈로그에는 브랜드 열이 없다 — 비워 두고 앱이 상품명만 쓴다.
                "brand": "",
                "purchase_url": product.link or "",
                "image_ref": product.image_url or item.image_ref,
                "price_snapshot": (
                    item.price_snapshot or product.sale_price or product.product_price
                ),
                "display_name": product.title or "",
            }
    return {}


def wishlist_items(identity: ChatIdentity) -> QuerySet[WishlistItem]:
    """회원이 담아 둔 상품. 비회원은 담을 수 없어 빈 목록이다."""

    if identity.user_id is None:
        return WishlistItem.objects.none()
    return WishlistItem.objects.filter(user_id=identity.user_id)


@transaction.atomic
def add_wishlist_item(
    *,
    identity: ChatIdentity,
    result_id: uuid.UUID,
    card_id: uuid.UUID,
    item_id: uuid.UUID,
) -> tuple[WishlistItem | None, bool]:
    """소유 추천의 판매 상품을 찜에 담는다. 이미 담았으면 그 행을 그대로 돌려준다."""

    if identity.user_id is None:
        return None, False
    item = (
        OutfitCompositionItem.objects.select_related("composition__result__identity")
        .filter(
            pk=item_id,
            composition_id=card_id,
            composition__result_id=result_id,
            composition__result__identity=identity,
            composition__status=OutfitComposition.Status.VALIDATED,
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
        )
        .first()
    )
    if item is None:
        return None, False

    if item.source_id:
        existing = (
            WishlistItem.objects.filter(
                user_id=identity.user_id,
                source_collection=item.source_collection,
                source_id=item.source_id,
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return existing, False

    catalog = _catalog_fields(item)
    snapshot_name = _snapshot_text(
        item.item_snapshot,
        "display_name",
        "item_name",
        "product_name",
        "name",
        "title",
    )
    wish = WishlistItem.objects.create(
        user_id=identity.user_id,
        item=item,
        result_id_snapshot=item.composition.result_id,
        composition_id_snapshot=item.composition_id,
        source_collection=item.source_collection,
        source_id=item.source_id,
        # 이름은 추천이 보여준 것을 우선한다 — 화면에서 본 이름과 찜 목록이 달라지면 안 된다.
        display_name=snapshot_name or str(catalog.get("display_name") or "") or item.slot,
        brand=str(catalog.get("brand") or ""),
        price_snapshot=catalog.get("price_snapshot", item.price_snapshot),
        image_ref=str(catalog.get("image_ref") or item.image_ref or ""),
        purchase_url=str(
            catalog.get("purchase_url")
            or _snapshot_text(
                item.item_snapshot, "purchase_url", "product_url", "link", "url"
            )
        ),
        slot=item.slot,
    )
    return wish, True


@transaction.atomic
def remove_wishlist_item(
    *,
    identity: ChatIdentity,
    wish_id: uuid.UUID,
) -> bool:
    """찜 하나를 뺀다. 남의 찜은 찾지 못한 것으로 다룬다."""

    if identity.user_id is None:
        return False
    deleted, _ = WishlistItem.objects.filter(
        pk=wish_id, user_id=identity.user_id
    ).delete()
    return deleted > 0
