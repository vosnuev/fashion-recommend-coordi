"""운영자 큐레이션 룩과 네이버 가격 비교 상품을 조회한다."""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Case, IntegerField, Q, Value, When

from apps.catalog.models import NaverProduct
from apps.lookbook.models import CuratedLook, CuratedLookItem
from apps.wardrobe.taxonomy import CATEGORY_LARGE, is_valid_pair

MAX_RELATED = 3
MIN_RELATED_KEYWORD_MATCHES = 2


@dataclass(frozen=True)
class DiscoveryQuery:
    query: str = ""
    tag: str = ""
    gender: str = ""
    limit: int = 20
    offset: int = 0


def _product(product: NaverProduct) -> dict:
    return {
        "id": str(product.naver_product_id),
        "category_large": product.category_large,
        "name": product.title,
        "brand": product.brand or product.mall_name or "네이버쇼핑",
        "image": product.image_url or "",
        "price": product.lprice,
        "mall_name": product.mall_name or "네이버쇼핑",
        "link": product.link or "",
    }


def _related(
    item: CuratedLookItem,
    excluded_product_ids: set[str] | None = None,
) -> list[dict]:
    slot = item.slot.strip()
    category_small = item.category_small.strip()
    if slot not in CATEGORY_LARGE or not is_valid_pair(slot, category_small):
        return []

    words = tuple(
        dict.fromkeys(
            word.strip()
            for word in item.related_keyword.split()
            if len(word.strip()) >= 2
        )
    )
    if len(words) < MIN_RELATED_KEYWORD_MATCHES:
        return []

    keyword_matches = Value(0, output_field=IntegerField())
    for word in words:
        keyword_matches += Case(
            When(title__icontains=word, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )

    products = (
        NaverProduct.objects.filter(
            category_large=slot,
            category_small=category_small,
            lprice__gt=0,
        )
        .exclude(image_url__isnull=True)
        .exclude(image_url="")
        .exclude(link=item.product_url)
        .annotate(keyword_matches=keyword_matches)
        .filter(keyword_matches__gte=MIN_RELATED_KEYWORD_MATCHES)
        .order_by("-keyword_matches", "lprice", "id")
    )
    if excluded_product_ids:
        products = products.exclude(naver_product_id__in=excluded_product_ids)
    selected = list(products[:MAX_RELATED])
    if excluded_product_ids is not None:
        excluded_product_ids.update(str(product.naver_product_id) for product in selected)
    return [_product(product) for product in selected]


def _look(
    look: CuratedLook,
    used_related_product_ids: set[str] | None = None,
) -> dict:
    items = []
    for source in look.items.all():
        items.append(
            {
                "id": f"curated-{source.pk}",
                "slot": source.slot,
                "category_large": source.slot.strip(),
                "category_small": source.category_small,
                "name": source.name,
                "brand": source.brand or "네이버쇼핑",
                "image": source.image_url,
                "price": source.price,
                "mall_name": "네이버쇼핑",
                "link": source.product_url,
                "similar_products": _related(source, used_related_product_ids),
            }
        )
    return {
        "id": f"curated-{look.external_id}",
        "gender": look.gender,
        "title": look.title,
        "subtitle": look.subtitle,
        "image": f"/api/v1/lookbooks/discover/{look.external_id}/cover/",
        "tags": look.tags,
        "total_price": sum(item["price"] or 0 for item in items),
        "items": items,
        "reasons": [
            "운영자가 선별한 네이버 쇼핑 상품으로 구성한 룩이에요.",
            "구성 아이템은 원본 상세페이지로, 비슷한 상품은 가격 비교 결과로 연결돼요.",
        ],
    }


def list_looks(params: DiscoveryQuery) -> dict:
    queryset = CuratedLook.objects.filter(is_active=True).prefetch_related("items")
    if params.gender:
        queryset = queryset.filter(gender=params.gender)
    if params.query:
        queryset = queryset.filter(
            Q(title__icontains=params.query) | Q(subtitle__icontains=params.query)
        )
    if params.tag:
        queryset = queryset.filter(tags__contains=[params.tag])
    total = queryset.count()
    page = queryset[params.offset : params.offset + params.limit]
    return {
        "count": total,
        "next_offset": params.offset + params.limit if params.offset + params.limit < total else None,
        "results": _looks_without_duplicate_related_products(page),
    }


def _looks_without_duplicate_related_products(looks) -> list[dict]:
    used_related_product_ids: set[str] = set()
    return [_look(look, used_related_product_ids) for look in looks]


def get_look(look_id: str) -> dict | None:
    external_id = look_id.removeprefix("curated-")
    look = (
        CuratedLook.objects.filter(external_id=external_id, is_active=True)
        .prefetch_related("items")
        .first()
    )
    return _look(look, set()) if look else None
