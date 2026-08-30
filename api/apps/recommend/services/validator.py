"""최종 코디 조합을 이미지 생성 전에 검사하는 규칙 기반 Validator."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations
from typing import Any, Protocol

from apps.catalog.models import ElevenProduct, NaverProduct
from apps.recommend.services.body_profile import BodyProfile
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)
from apps.recommend.services.qdrant import collection_spec
from apps.recommend.services.style_rules import load_body_rules, load_weather_rules
from apps.wardrobe.models import WardrobeItem


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True)
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    slot_id: str = ""
    source_type: ItemSource | None = None
    source_id: str = ""


@dataclass(frozen=True)
class SourceEligibility:
    eligible: bool
    code: str = ""
    message: str = ""
    current_price: int | None = None


class EligibilityGateway(Protocol):
    """변경 가능한 운영 DB 상태를 배치로 확인하는 경계."""

    def check(
        self,
        items: tuple[OutfitItem, ...],
        *,
        user_id: int | None,
    ) -> Mapping[tuple[str, str, str], SourceEligibility]: ...


@dataclass(frozen=True)
class ReferenceValidationContract:
    """공유 원본은 배제하고 선택된 대체 anchor는 포함시키는 최종 검증 계약."""

    original_wardrobe_item_ids: tuple[str, ...]
    original_qdrant_point_ids: tuple[str, ...]
    anchor_identity: tuple[str, str, str]


@dataclass(frozen=True)
class ValidationContext:
    user_id: int | None = None
    body: BodyProfile | None = None
    season: str = ""
    weather: Mapping[str, Any] | None = None
    occasion: str = ""
    total_budget: int | None = None
    category_budgets: Mapping[str, int] = field(default_factory=dict)
    required_slot_ids: tuple[str, ...] = ()
    excluded_source_ids: tuple[str, ...] = ()
    preferred_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    avoided_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    contextual_avoided_tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    incompatible_color_pairs: tuple[tuple[str, str], ...] = ()
    require_image: bool = True
    reference: ReferenceValidationContract | None = None


@dataclass(frozen=True)
class OutfitValidationResult:
    issues: tuple[ValidationIssue, ...]
    effective_total_product_price: int

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def can_render(self) -> bool:
        return self.valid

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity is ValidationSeverity.WARNING
        )


def _normalize(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip().casefold()
        return {text} if text else set()
    if isinstance(value, (list, tuple, set, frozenset)):
        result: set[str] = set()
        for item in value:
            result.update(_normalize(item))
        return result
    return {str(value).strip().casefold()}


def _values(item: OutfitItem, field_name: str) -> set[str]:
    values = _normalize(item.payload.get(field_name))
    if field_name == "category_large":
        values.update(_normalize(item.category_large))
    elif field_name == "layer_role":
        values.update(_normalize(item.layer_role))
    return values


def _season_from_context(context: ValidationContext) -> str:
    if context.season.strip():
        return context.season.strip()
    if not context.weather:
        return ""
    try:
        temperature = float(context.weather.get("temperature"))
    except (TypeError, ValueError):
        return ""
    if temperature >= 23:
        return "여름"
    if temperature >= 17:
        return "간절기"
    if temperature >= 9:
        return "가을"
    return "겨울"


def _temperature(weather: Mapping[str, Any] | None) -> float | None:
    if not weather:
        return None
    try:
        return float(weather.get("temperature"))
    except (TypeError, ValueError):
        return None


def _season_matches(actual: set[str], expected: str) -> bool:
    normalized = expected.casefold()
    if normalized in actual:
        return True
    if normalized == "간절기":
        return bool(actual & {"봄", "가을", "간절기"})
    return False


def _product_source(item: OutfitItem) -> str:
    source = str(item.payload.get("source") or "").strip().casefold()
    if source in {"naver", "eleven"}:
        return source
    if item.source_collection == collection_spec("products_naver").name:
        return "naver"
    if item.source_collection == collection_spec("products_eleven").name:
        return "eleven"
    return ""


class DjangoEligibilityGateway:
    """Qdrant payload를 신뢰하지 않고 현재 PostgreSQL 행을 재확인한다."""

    def check(
        self,
        items: tuple[OutfitItem, ...],
        *,
        user_id: int | None,
    ) -> dict[tuple[str, str, str], SourceEligibility]:
        result: dict[tuple[str, str, str], SourceEligibility] = {}
        wardrobe_items = [
            item for item in items if item.source_type is ItemSource.WARDROBE
        ]
        product_items = [
            item for item in items if item.source_type is ItemSource.PRODUCT
        ]

        self._check_wardrobe(wardrobe_items, user_id=user_id, result=result)
        self._check_products(product_items, result=result)
        for item in items:
            if item.source_type is ItemSource.GOLDENSET_ITEM:
                result[item.identity] = SourceEligibility(eligible=True)
        return result

    @staticmethod
    def _check_wardrobe(
        items: list[OutfitItem],
        *,
        user_id: int | None,
        result: dict[tuple[str, str, str], SourceEligibility],
    ) -> None:
        parsed_ids: dict[str, uuid.UUID] = {}
        for item in items:
            try:
                parsed_ids[item.source_id] = uuid.UUID(item.source_id)
            except (TypeError, ValueError, AttributeError):
                result[item.identity] = SourceEligibility(
                    eligible=False,
                    code="WARDROBE_ITEM_INVALID_ID",
                    message="옷장 아이템 ID 형식이 올바르지 않습니다.",
                )

        rows = {
            str(row.id): row
            for row in WardrobeItem.objects.filter(id__in=parsed_ids.values())
        }
        for item in items:
            if item.identity in result:
                continue
            row = rows.get(item.source_id)
            if row is None:
                status = SourceEligibility(
                    eligible=False,
                    code="WARDROBE_ITEM_NOT_FOUND",
                    message="옷장에서 삭제되었거나 존재하지 않는 아이템입니다.",
                )
            elif user_id is None or row.user_id != user_id:
                status = SourceEligibility(
                    eligible=False,
                    code="WARDROBE_ITEM_FORBIDDEN",
                    message="현재 사용자가 소유한 옷장 아이템이 아닙니다.",
                )
            elif not row.confirmed:
                status = SourceEligibility(
                    eligible=False,
                    code="WARDROBE_ITEM_UNCONFIRMED",
                    message="사용자가 아직 확정하지 않은 옷장 아이템입니다.",
                )
            else:
                status = SourceEligibility(eligible=True)
            result[item.identity] = status

    @staticmethod
    def _check_products(
        items: list[OutfitItem],
        *,
        result: dict[tuple[str, str, str], SourceEligibility],
    ) -> None:
        naver_ids = [
            item.source_id for item in items if _product_source(item) == "naver"
        ]
        eleven_ids = [
            item.source_id for item in items if _product_source(item) == "eleven"
        ]
        naver = NaverProduct.objects.in_bulk(
            naver_ids,
            field_name="naver_product_id",
        )
        eleven = ElevenProduct.objects.in_bulk(
            eleven_ids,
            field_name="eleven_product_id",
        )

        for item in items:
            source = _product_source(item)
            if source == "naver":
                status = DjangoEligibilityGateway._naver_status(
                    naver.get(item.source_id)
                )
            elif source == "eleven":
                status = DjangoEligibilityGateway._eleven_status(
                    eleven.get(item.source_id)
                )
            else:
                status = SourceEligibility(
                    eligible=False,
                    code="PRODUCT_SOURCE_UNKNOWN",
                    message="상품 출처를 확인할 수 없습니다.",
                )
            result[item.identity] = status

    @staticmethod
    def _naver_status(product: NaverProduct | None) -> SourceEligibility:
        if product is None:
            return SourceEligibility(
                eligible=False,
                code="PRODUCT_NOT_FOUND",
                message="네이버 카탈로그에서 상품을 찾을 수 없습니다.",
            )
        # 네이버 productType의 각 4개 그룹에서 3은 단종, 4는 판매예정이다.
        if product.product_type and product.product_type % 4 in {0, 3}:
            return SourceEligibility(
                eligible=False,
                code="PRODUCT_NOT_ON_SALE",
                message="단종되었거나 아직 판매 중이 아닌 네이버 상품입니다.",
            )
        return DjangoEligibilityGateway._catalog_status(
            tagging_status=product.tagging_status,
            link=product.link,
            price=product.lprice,
        )

    @staticmethod
    def _eleven_status(product: ElevenProduct | None) -> SourceEligibility:
        if product is None:
            return SourceEligibility(
                eligible=False,
                code="PRODUCT_NOT_FOUND",
                message="11번가 카탈로그에서 상품을 찾을 수 없습니다.",
            )
        return DjangoEligibilityGateway._catalog_status(
            tagging_status=product.tagging_status,
            link=product.link,
            price=product.representative_price,
        )

    @staticmethod
    def _catalog_status(
        *,
        tagging_status: str,
        link: str | None,
        price: int | None,
    ) -> SourceEligibility:
        if tagging_status != "tagged":
            return SourceEligibility(
                eligible=False,
                code="PRODUCT_NOT_READY",
                message="추천용 태깅이 완료되지 않은 상품입니다.",
            )
        if not link:
            return SourceEligibility(
                eligible=False,
                code="PRODUCT_LINK_MISSING",
                message="구매 링크가 없는 상품입니다.",
            )
        if price is None or price < 0:
            return SourceEligibility(
                eligible=False,
                code="PRODUCT_PRICE_MISSING",
                message="현재 가격을 확인할 수 없는 상품입니다.",
            )
        return SourceEligibility(eligible=True, current_price=int(price))


class OutfitValidator:
    def __init__(
        self, *, eligibility_gateway: EligibilityGateway | None = None
    ) -> None:
        self.eligibility_gateway = eligibility_gateway or DjangoEligibilityGateway()

    def validate(
        self,
        composition: OutfitComposition,
        *,
        context: ValidationContext | None = None,
    ) -> OutfitValidationResult:
        context = context or ValidationContext()
        self._validate_context(context)
        issues: list[ValidationIssue] = []

        self._validate_slots(composition, context, issues)
        self._validate_duplicates(composition.items, issues)
        self._validate_sources(composition, context, issues)
        self._validate_items(composition.items, context, issues)
        self._validate_categories(composition.items, issues)
        self._validate_layer_order(composition.items, issues)
        self._validate_context_rules(composition.items, context, issues)
        self._validate_body_and_weather_rules(composition.items, context, issues)

        eligibility = self.eligibility_gateway.check(
            composition.items,
            user_id=context.user_id,
        )
        effective_price = self._validate_eligibility_and_budget(
            composition,
            context,
            eligibility,
            issues,
        )
        return OutfitValidationResult(
            issues=tuple(issues),
            effective_total_product_price=effective_price,
        )

    @staticmethod
    def _validate_context(context: ValidationContext) -> None:
        if context.total_budget is not None and (
            not isinstance(context.total_budget, int)
            or isinstance(context.total_budget, bool)
            or context.total_budget < 0
        ):
            raise ValueError("total_budget은 0 이상의 정수여야 합니다.")
        if any(
            not isinstance(category, str)
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount < 0
            for category, amount in context.category_budgets.items()
        ):
            raise ValueError("category_budgets는 대분류별 0 이상의 정수여야 합니다.")
        reference = context.reference
        if reference is not None and (
            not reference.original_wardrobe_item_ids
            or not reference.original_qdrant_point_ids
            or len(reference.anchor_identity) != 3
            or any(not str(value).strip() for value in reference.anchor_identity)
        ):
            raise ValueError(
                "공유 레퍼런스 검증에는 원본 제외 ID와 anchor identity가 필요합니다."
            )

    @staticmethod
    def _validate_slots(
        composition: OutfitComposition,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        selected = {item.slot_id for item in composition.items}
        required = {slot.slot_id for slot in composition.slots if slot.required}
        required.update(context.required_slot_ids)
        # 선택 슬롯이 비는 건 경고지 실패가 아니다. slots가 비어 있는 조합
        # (옷장 Composer 등)에서는 optional도 비어 예전 동작이 그대로 유지된다.
        optional = {
            slot.slot_id
            for slot in composition.slots
            if not slot.required and slot.slot_id not in context.required_slot_ids
        }
        missing = (set(composition.missing_slot_ids) | (required - selected)) - optional
        for slot_id in sorted(missing):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="REQUIRED_SLOT_MISSING",
                    message=f"필수 슬롯 '{slot_id}'에 선택된 아이템이 없습니다.",
                    slot_id=slot_id,
                )
            )

    @staticmethod
    def _validate_duplicates(
        items: tuple[OutfitItem, ...],
        issues: list[ValidationIssue],
    ) -> None:
        slot_counts = Counter(item.slot_id for item in items)
        for slot_id, count in slot_counts.items():
            if count > 1:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "DUPLICATE_SLOT",
                        f"슬롯 '{slot_id}'에 {count}개 아이템이 배치됐습니다.",
                        slot_id=slot_id,
                    )
                )

        identities = Counter(item.identity for item in items)
        for identity, count in identities.items():
            if count <= 1:
                continue
            source_type, _, source_id = identity
            issues.append(
                ValidationIssue(
                    ValidationSeverity.ERROR,
                    "DUPLICATE_ITEM",
                    f"같은 아이템 '{source_id}'이 {count}개 슬롯에 중복 배치됐습니다.",
                    source_type=ItemSource(source_type),
                    source_id=source_id,
                )
            )

    @staticmethod
    def _validate_sources(
        composition: OutfitComposition,
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        allowed = {
            RecommendationMode.WARDROBE_BASED: {ItemSource.WARDROBE},
            RecommendationMode.NEW_ITEM: {ItemSource.WARDROBE, ItemSource.PRODUCT},
        }.get(composition.mode, set())
        for item in composition.items:
            issue_kwargs = {
                "slot_id": item.slot_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
            }
            if item.source_type not in allowed:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "MODE_SOURCE_INVALID",
                        (
                            f"{composition.mode.value} 모드에서 허용되지 않는 "
                            "최종 아이템 출처입니다."
                        ),
                        **issue_kwargs,
                    )
                )

        if composition.mode is RecommendationMode.NEW_ITEM and not any(
            item.source_type is ItemSource.PRODUCT for item in composition.items
        ):
            issues.append(
                ValidationIssue(
                    ValidationSeverity.ERROR,
                    "NEW_ITEM_PRODUCT_REQUIRED",
                    "신규 상품 추천에는 판매 상품이 최소 한 개 필요합니다.",
                )
            )

        reference = context.reference
        if reference is None:
            return
        original_ids = set(reference.original_wardrobe_item_ids)
        original_points = set(reference.original_qdrant_point_ids)
        for item in composition.items:
            payload_item_id = str(item.payload.get("item_id") or "")
            if (
                item.source_id in original_ids
                or payload_item_id in original_ids
                or item.point_id in original_points
            ):
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "SHARED_REFERENCE_SOURCE_LEAKED",
                        "참고용 친구 옷 원본은 최종 코디에 포함될 수 없습니다.",
                        slot_id=item.slot_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                    )
                )
        if not any(
            item.identity == reference.anchor_identity for item in composition.items
        ):
            issues.append(
                ValidationIssue(
                    ValidationSeverity.ERROR,
                    "REFERENCE_ANCHOR_MISSING",
                    "공유 옷을 참고해 선택한 고정 아이템이 최종 코디에 없습니다.",
                )
            )

    @staticmethod
    def _validate_items(
        items: tuple[OutfitItem, ...],
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        excluded_ids = set(context.excluded_source_ids)
        avoided = {
            field_name: _normalize(values)
            for field_name, values in context.avoided_tags.items()
        }
        for item in items:
            issue_kwargs = {
                "slot_id": item.slot_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
            }
            if context.require_image and not item.image_ref:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "ITEM_IMAGE_MISSING",
                        "최종 이미지 생성에 사용할 아이템 이미지가 없습니다.",
                        **issue_kwargs,
                    )
                )
            if item.source_id in excluded_ids:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "EXPLICIT_ITEM_EXCLUDED",
                        "사용자가 명시적으로 제외한 아이템입니다.",
                        **issue_kwargs,
                    )
                )

            actual_category = _normalize(item.payload.get("category_large"))
            expected_category = _normalize(item.category_large)
            if (
                actual_category
                and expected_category
                and not (actual_category & expected_category)
            ):
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "CATEGORY_SLOT_MISMATCH",
                        f"슬롯 카테고리 '{item.category_large}'와 아이템 카테고리가 다릅니다.",
                        **issue_kwargs,
                    )
                )

            for field_name, forbidden in avoided.items():
                matched = _values(item, field_name) & forbidden
                if matched:
                    issues.append(
                        ValidationIssue(
                            ValidationSeverity.ERROR,
                            "EXPLICIT_TAG_EXCLUDED",
                            f"명시적 제외 조건 {field_name}={sorted(matched)}을 포함합니다.",
                            **issue_kwargs,
                        )
                    )

    @staticmethod
    def _validate_categories(
        items: tuple[OutfitItem, ...],
        issues: list[ValidationIssue],
    ) -> None:
        categories = Counter(item.category_large.strip().casefold() for item in items)
        for category in ("하의", "신발", "원피스/세트"):
            if categories[category.casefold()] > 1:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "CATEGORY_CONFLICT",
                        f"'{category}' 카테고리가 중복 배치됐습니다.",
                    )
                )

    @staticmethod
    def _validate_layer_order(
        items: tuple[OutfitItem, ...],
        issues: list[ValidationIssue],
    ) -> None:
        role_rank = {
            "inner": 1,
            "이너": 1,
            "기본 상의": 1,
            "top": 1,
            "mid": 2,
            "미드": 2,
            "레이어드 상의": 2,
            "outer": 3,
            "아우터": 3,
        }
        layered: list[tuple[int, int, OutfitItem]] = []
        for item in items:
            role = item.layer_role.strip().casefold()
            if not role:
                role = str(item.payload.get("layer_role") or "").strip().casefold()
            rank = role_rank.get(role)
            raw_order = item.payload.get("layer_order")
            if rank is None or raw_order in (None, ""):
                continue
            try:
                order = int(raw_order)
            except (TypeError, ValueError):
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "LAYER_ORDER_INVALID",
                        "레이어 순서가 정수가 아닙니다.",
                        slot_id=item.slot_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                    )
                )
                continue
            layered.append((rank, order, item))

        for left, right in combinations(layered, 2):
            if left[0] < right[0] and left[1] >= right[1]:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "LAYER_ORDER_CONFLICT",
                        f"{left[2].slot_id}과 {right[2].slot_id}의 안쪽·바깥쪽 레이어 순서가 충돌합니다.",
                    )
                )
            elif right[0] < left[0] and right[1] >= left[1]:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "LAYER_ORDER_CONFLICT",
                        f"{right[2].slot_id}과 {left[2].slot_id}의 안쪽·바깥쪽 레이어 순서가 충돌합니다.",
                    )
                )

    @staticmethod
    def _validate_context_rules(
        items: tuple[OutfitItem, ...],
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        season = _season_from_context(context)
        if season:
            for item in items:
                actual = _values(item, "season")
                if actual and not _season_matches(actual, season):
                    issues.append(
                        ValidationIssue(
                            ValidationSeverity.WARNING,
                            "SEASON_MISMATCH",
                            f"{season} 조건과 아이템 계절 태그가 다릅니다.",
                            slot_id=item.slot_id,
                            source_type=item.source_type,
                            source_id=item.source_id,
                        )
                    )

        occasion = context.occasion.strip().casefold()
        usages = set().union(
            *(_values(item, "usage") | _values(item, "occasion") for item in items)
        )
        if occasion and usages and occasion not in usages:
            issues.append(
                ValidationIssue(
                    ValidationSeverity.WARNING,
                    "OCCASION_MISMATCH",
                    f"코디의 용도 태그에 '{context.occasion.strip()}'이 없습니다.",
                )
            )

        for field_name, values in context.contextual_avoided_tags.items():
            forbidden = _normalize(values)
            for item in items:
                matched = _values(item, field_name) & forbidden
                if matched:
                    issues.append(
                        ValidationIssue(
                            ValidationSeverity.WARNING,
                            "CONTEXT_RULE_MISMATCH",
                            f"컨텍스트 비권장 조건 {field_name}={sorted(matched)}을 포함합니다.",
                            slot_id=item.slot_id,
                            source_type=item.source_type,
                            source_id=item.source_id,
                        )
                    )

        for field_name, values in context.preferred_tags.items():
            preferred = _normalize(values)
            if preferred and not any(
                _values(item, field_name) & preferred for item in items
            ):
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.WARNING,
                        "PREFERRED_TAG_MISSING",
                        f"선호 조건 {field_name}={sorted(preferred)}이 코디에 반영되지 않았습니다.",
                    )
                )

        colors = set().union(*(_values(item, "color") for item in items))
        for first, second in context.incompatible_color_pairs:
            pair = {first.strip().casefold(), second.strip().casefold()}
            if len(pair) == 2 and pair.issubset(colors):
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.WARNING,
                        "COLOR_HARMONY_WARNING",
                        f"색상 규칙에서 '{first}'와 '{second}' 조합을 비권장합니다.",
                    )
                )

    @staticmethod
    def _validate_body_and_weather_rules(
        items: tuple[OutfitItem, ...],
        context: ValidationContext,
        issues: list[ValidationIssue],
    ) -> None:
        body_rules = load_body_rules()
        axis = body_rules.for_profile(context.body or BodyProfile())
        for item in items:
            tags = {
                **item.payload,
                "category_large": item.category_large
                or item.payload.get("category_large"),
                "layer_role": item.layer_role or item.payload.get("layer_role"),
            }
            issue_kwargs = {
                "slot_id": item.slot_id,
                "source_type": item.source_type,
                "source_id": item.source_id,
            }
            for rule in axis.avoid:
                if not rule.matches(tags):
                    continue
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR
                        if rule.hard
                        else ValidationSeverity.WARNING,
                        "BODY_RULE_HARD_EXCLUDED" if rule.hard else "BODY_FIT_WARNING",
                        rule.reason,
                        **issue_kwargs,
                    )
                )

        weather_rules = load_weather_rules()
        band = weather_rules.band_for(_temperature(context.weather))
        if band is None:
            return
        for item in items:
            tags = {
                **item.payload,
                "category_large": item.category_large
                or item.payload.get("category_large"),
                "layer_role": item.layer_role or item.payload.get("layer_role"),
            }
            for rule in band.discourage:
                if rule.matches(tags):
                    issues.append(
                        ValidationIssue(
                            ValidationSeverity.WARNING,
                            "WEATHER_RULE_WARNING",
                            rule.reason,
                            slot_id=item.slot_id,
                            source_type=item.source_type,
                            source_id=item.source_id,
                        )
                    )

    @staticmethod
    def _validate_eligibility_and_budget(
        composition: OutfitComposition,
        context: ValidationContext,
        eligibility: Mapping[tuple[str, str, str], SourceEligibility],
        issues: list[ValidationIssue],
    ) -> int:
        total = 0
        for item in composition.items:
            status = eligibility.get(item.identity)
            if status is None:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        "SOURCE_ELIGIBILITY_NOT_CHECKED",
                        "아이템의 최신 소유·판매 상태를 확인하지 못했습니다.",
                        slot_id=item.slot_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                    )
                )
                continue
            if not status.eligible:
                issues.append(
                    ValidationIssue(
                        ValidationSeverity.ERROR,
                        status.code or "SOURCE_ITEM_INELIGIBLE",
                        status.message or "현재 사용할 수 없는 아이템입니다.",
                        slot_id=item.slot_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                    )
                )
                continue
            if item.source_type is ItemSource.PRODUCT:
                current_price = status.current_price
                if current_price is None:
                    issues.append(
                        ValidationIssue(
                            ValidationSeverity.ERROR,
                            "PRODUCT_PRICE_NOT_CHECKED",
                            "상품의 현재 가격을 확인하지 못했습니다.",
                            slot_id=item.slot_id,
                            source_type=item.source_type,
                            source_id=item.source_id,
                        )
                    )
                    continue
                total += current_price
                category = item.category_large or str(
                    item.payload.get("category_large") or ""
                )
                category_budget = context.category_budgets.get(category)
                if category_budget is not None and current_price > category_budget:
                    issues.append(
                        ValidationIssue(
                            ValidationSeverity.ERROR,
                            "CATEGORY_BUDGET_EXCEEDED",
                            f"{category} 상품 가격 {current_price}원이 "
                            f"예산 {category_budget}원을 초과합니다.",
                            slot_id=item.slot_id,
                            source_type=item.source_type,
                            source_id=item.source_id,
                        )
                    )
                if item.price is not None and item.price != current_price:
                    issues.append(
                        ValidationIssue(
                            ValidationSeverity.WARNING,
                            "PRODUCT_PRICE_CHANGED",
                            f"상품 가격이 {item.price}원에서 {current_price}원으로 변경됐습니다.",
                            slot_id=item.slot_id,
                            source_type=item.source_type,
                            source_id=item.source_id,
                        )
                    )

        if composition.total_product_price != total:
            issues.append(
                ValidationIssue(
                    ValidationSeverity.WARNING,
                    "COMPOSITION_PRICE_STALE",
                    f"조합 가격 {composition.total_product_price}원과 현재 가격 {total}원이 다릅니다.",
                )
            )
        if context.total_budget is not None and total > context.total_budget:
            issues.append(
                ValidationIssue(
                    ValidationSeverity.ERROR,
                    "TOTAL_BUDGET_EXCEEDED",
                    f"현재 상품 합계 {total}원이 예산 {context.total_budget}원을 초과합니다.",
                )
            )
        return total
