"""쇼핑 상품의 BGE-M3 입력 텍스트와 Qdrant payload 구성."""

from __future__ import annotations

from typing import Any


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _line(label: str, value: Any) -> str | None:
    values = _values(value)
    return f"{label}: {', '.join(values)}" if values else None


def serialize_product_text(product: dict[str, Any]) -> str:
    """필드 순서가 고정된 한국어 상품 설명을 만든다.

    태그가 아직 pending이어도 카테고리·브랜드·상품명으로 기본 텍스트를 만들고,
    태깅 완료 시 collector가 작업 generation을 올려 풍부한 텍스트로 재색인한다.
    """
    category = " > ".join(
        value
        for value in (
            str(product.get("category_large") or "").strip(),
            str(product.get("category_small") or "").strip(),
        )
        if value
    )
    lines = [
        _line("카테고리", category),
        _line("스타일", product.get("style")),
        _line("색상", product.get("color")),
        _line("패턴", product.get("pattern")),
        _line("핏", product.get("fit")),
        _line("소재", product.get("material")),
        _line("소매", product.get("sleeve")),
        _line("기장", product.get("length")),
        _line("계절", product.get("season")),
        _line("용도", product.get("usage")),
        _line("레이어 역할", product.get("layer_role")),
        _line("브랜드", product.get("brand")),
        _line("상품명", product.get("title")),
    ]
    return "\n".join(line for line in lines if line)


def build_product_payload(
    product: dict[str, Any],
    *,
    embedding_version: str,
    image_s3_bucket: str,
    image_s3_key: str,
) -> dict[str, Any]:
    return {
        "source": product["source"],
        "external_product_id": product["external_product_id"],
        "product_db_id": product["id"],
        "title": product.get("title"),
        "link": product.get("link"),
        "image_url": product.get("image_url"),
        "image_s3_bucket": image_s3_bucket,
        "image_s3_key": image_s3_key,
        "price": product.get("price"),
        "mall_name": product.get("mall_name"),
        "brand": product.get("brand"),
        "category_large": product.get("category_large"),
        "category_small": product.get("category_small"),
        "season": _values(product.get("season")),
        "style": _values(product.get("style")),
        "color": _values(product.get("color")),
        "pattern": _values(product.get("pattern")),
        "fit": product.get("fit"),
        "material": _values(product.get("material")),
        "sleeve": product.get("sleeve"),
        "length": product.get("length"),
        "usage": _values(product.get("usage")),
        "layer_role": product.get("layer_role"),
        "layer_order": product.get("layer_order"),
        "tagging_status": product.get("tagging_status"),
        "embedding_version": embedding_version,
    }
