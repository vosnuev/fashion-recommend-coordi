from __future__ import annotations

import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.chat.models import (
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatRunPersona,
    ChatSession,
)
from apps.recommend.models import (
    GoldenTemplateSnapshot,
    OutfitComposition,
    OutfitCompositionItem,
    RecommendationResult,
)


class RecommendationModelTests(TestCase):
    PERSONA_IDS = ("minimal", "experimental", "practical")

    def _result(
        self,
        *,
        mode: str = RecommendationResult.Mode.NEW_ITEM,
        run_id: uuid.UUID | None = None,
    ) -> RecommendationResult:
        identity = ChatIdentity.objects.create(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=timezone.now() + timedelta(days=7),
        )
        session = ChatSession.objects.create(identity=identity, mode=mode)
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="추천 요청",
        )
        run = ChatRun.objects.create(
            id=run_id or uuid.uuid4(),
            session=session,
            request_message=message,
            status=ChatRun.Status.SUCCEEDED,
        )
        return RecommendationResult.objects.create(
            identity=identity,
            session=session,
            run=run,
            mode=mode,
            dataset_version="goldenset-2026-08-01",
        )

    def _stylist_run(self) -> tuple[ChatRun, list[ChatRunPersona]]:
        identity = ChatIdentity.objects.create(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=timezone.now() + timedelta(days=7),
        )
        session = ChatSession.objects.create(
            identity=identity,
            mode=ChatSession.Mode.NEW_ITEM,
            response_mode=ChatSession.ResponseMode.STYLIST,
            selected_persona_ids=list(self.PERSONA_IDS),
        )
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="스타일리스트별 추천 요청",
        )
        versions = {persona_id: 1 for persona_id in self.PERSONA_IDS}
        prompt_versions = {persona_id: "persona-v1" for persona_id in self.PERSONA_IDS}
        run = ChatRun.objects.create(
            session=session,
            request_message=message,
            status=ChatRun.Status.SUCCEEDED,
            response_mode=ChatSession.ResponseMode.STYLIST,
            persona_ids=list(self.PERSONA_IDS),
            persona_versions=versions,
            persona_prompt_versions=prompt_versions,
            stylist_config_version="1.0",
        )
        executions = [
            ChatRunPersona.objects.create(
                run=run,
                persona_id=persona_id,
                persona_version=1,
                prompt_version="persona-v1",
                display_order=display_order,
                strategy_snapshot={"persona_id": persona_id},
            )
            for display_order, persona_id in enumerate(self.PERSONA_IDS, start=1)
        ]
        return run, executions

    @staticmethod
    def _stylist_result(
        run: ChatRun,
        execution: ChatRunPersona,
    ) -> RecommendationResult:
        return RecommendationResult.objects.create(
            identity=run.session.identity,
            session=run.session,
            run=run,
            persona_execution=execution,
            response_mode=RecommendationResult.ResponseMode.STYLIST,
            persona_id=execution.persona_id,
            persona_version=execution.persona_version,
            persona_explanation=f"{execution.persona_id} 추천입니다.",
            validated_reason_codes=["WEATHER", "STYLE"],
            strategy_snapshot=execution.strategy_snapshot,
            mode=run.session.mode,
            dataset_version="goldenset-2026-08-01",
        )

    def _composition(
        self,
        result: RecommendationResult,
        *,
        rank: int = 1,
    ) -> OutfitComposition:
        return OutfitComposition.objects.create(
            result=result,
            rank=rank,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint="a" * 64,
            total_product_price=59_000,
            validation_reasons=[{"code": "VALID", "message": "검증 통과"}],
            warnings=[],
        )

    def _item(
        self,
        composition: OutfitComposition,
        *,
        position: int,
        slot: str,
        source_type: str,
        source_id: str,
        source_collection: str,
        price_snapshot: int | None = None,
    ) -> OutfitCompositionItem:
        return OutfitCompositionItem.objects.create(
            composition=composition,
            position=position,
            slot=slot,
            source_type=source_type,
            source_id=source_id,
            source_collection=source_collection,
            source_point_id=f"point-{source_id}",
            template_item_point_id=f"template-{slot}",
            replacement_score=0.91,
            image_ref=f"images/{source_id}.jpg",
            price_snapshot=price_snapshot,
            reasons=["골든 템플릿 슬롯과 카테고리·레이어가 일치함"],
            item_snapshot={"name": source_id, "slot": slot},
        )

    def test_full_recommendation_graph_preserves_template_and_item_snapshots(self):
        result = self._result()
        template = GoldenTemplateSnapshot.objects.create(
            result=result,
            golden_id="golden-outfit-17",
            point_id="outfit-point-17",
            retrieval_score=0.94,
            payload_snapshot={"style": ["미니멀"], "season": ["가을"]},
            reasons=[{"source": "preference", "delta": 8.0}],
        )
        composition = self._composition(result)
        wardrobe_item = self._item(
            composition,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.WARDROBE,
            source_id="wardrobe-101",
            source_collection="wardrobe_items",
        )
        product_item = self._item(
            composition,
            position=2,
            slot="BOTTOM",
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
            source_id="naver-202",
            source_collection="naver_products",
            price_snapshot=59_000,
        )

        result.refresh_from_db()
        self.assertEqual(result.golden_template, template)
        self.assertEqual(list(result.compositions.all()), [composition])
        self.assertEqual(
            list(composition.items.all()),
            [wardrobe_item, product_item],
        )
        self.assertEqual(product_item.price_snapshot, 59_000)
        self.assertEqual(template.payload_snapshot["style"], ["미니멀"])

    def test_recommendation_mode_matches_two_confirmed_product_modes(self):
        self.assertEqual(
            set(RecommendationResult.Mode.values),
            {"WARDROBE_BASED", "NEW_ITEM"},
        )

    def test_goldenset_item_cannot_be_saved_as_final_composition_item(self):
        composition = self._composition(self._result())
        item = OutfitCompositionItem(
            composition=composition,
            position=1,
            slot="TOP",
            source_type="GOLDENSET_ITEM",
            source_id="golden-item-1",
            source_collection="goldenset_items",
            source_point_id="golden-point-1",
            template_item_point_id="golden-point-1",
            image_ref="goldenset/item-1.jpg",
        )

        with self.assertRaises(ValidationError):
            item.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            item.save(force_insert=True)

    def test_one_chat_run_cannot_create_duplicate_default_results(self):
        result = self._result()

        with self.assertRaises(IntegrityError), transaction.atomic():
            RecommendationResult.objects.create(
                identity=result.identity,
                session=result.session,
                run=result.run,
                mode=result.mode,
                dataset_version=result.dataset_version,
            )

    def test_one_chat_run_can_store_three_stylist_results(self):
        run, executions = self._stylist_run()

        results = [self._stylist_result(run, execution) for execution in executions]
        for result in results:
            self._composition(result)

        self.assertEqual(run.recommendation_results.count(), 3)
        self.assertEqual(
            list(
                run.recommendation_results.order_by(
                    "persona_execution__display_order"
                ).values_list("persona_id", flat=True)
            ),
            list(self.PERSONA_IDS),
        )
        self.assertTrue(all(result.compositions.count() == 1 for result in results))

    def test_stylist_result_must_match_persona_execution_snapshot(self):
        run, executions = self._stylist_run()
        result = RecommendationResult(
            identity=run.session.identity,
            session=run.session,
            run=run,
            persona_execution=executions[0],
            response_mode=RecommendationResult.ResponseMode.STYLIST,
            persona_id="practical",
            persona_version=1,
            mode=run.session.mode,
            dataset_version="goldenset-2026-08-01",
        )

        with self.assertRaises(ValidationError):
            result.save()

    def test_stylist_result_cannot_store_a_second_ranked_composition(self):
        run, executions = self._stylist_run()
        result = self._stylist_result(run, executions[0])
        self._composition(result, rank=1)

        with self.assertRaises(ValidationError):
            self._composition(result, rank=2)

    def test_composition_rank_is_limited_to_one_through_three(self):
        result = self._result()

        with self.assertRaises(IntegrityError), transaction.atomic():
            OutfitComposition.objects.create(result=result, rank=4)

    def test_result_cannot_have_duplicate_composition_rank(self):
        result = self._result()
        self._composition(result, rank=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            OutfitComposition.objects.create(result=result, rank=1)

    def test_composition_rejects_duplicate_slot_and_source_item(self):
        composition = self._composition(self._result())
        self._item(
            composition,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.WARDROBE,
            source_id="wardrobe-1",
            source_collection="wardrobe_items",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._item(
                composition,
                position=2,
                slot="TOP",
                source_type=OutfitCompositionItem.SourceType.WARDROBE,
                source_id="wardrobe-2",
                source_collection="wardrobe_items",
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._item(
                composition,
                position=2,
                slot="BOTTOM",
                source_type=OutfitCompositionItem.SourceType.WARDROBE,
                source_id="wardrobe-1",
                source_collection="wardrobe_items",
            )

    def test_deleting_result_cascades_to_template_compositions_and_items(self):
        result = self._result()
        GoldenTemplateSnapshot.objects.create(
            result=result,
            golden_id="golden-1",
            point_id="point-1",
            retrieval_score=0.9,
        )
        composition = self._composition(result)
        self._item(
            composition,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.WARDROBE,
            source_id="wardrobe-1",
            source_collection="wardrobe_items",
        )

        result.delete()

        self.assertFalse(GoldenTemplateSnapshot.objects.exists())
        self.assertFalse(OutfitComposition.objects.exists())
        self.assertFalse(OutfitCompositionItem.objects.exists())
