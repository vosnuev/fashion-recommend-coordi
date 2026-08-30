from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatRun, ChatRunPersona, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services.recent_recommendations import (
    CurrentRunOwnershipMismatch,
    MemberRecentRecommendationsRequired,
    load_recent_recommendations,
)
from apps.recommend.models import (
    GoldenTemplateSnapshot,
    OutfitComposition,
    OutfitCompositionItem,
    RecommendationFeedback,
    RecommendationResult,
    SavedOutfit,
)

User = get_user_model()


class RecentRecommendationLoaderTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="recent-recommendation-member")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.session = ChatSession.objects.create(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.sequence = 0

    def _run(
        self,
        *,
        response_mode: str = ChatSession.ResponseMode.DEFAULT,
        persona_ids: list[str] | None = None,
    ) -> ChatRun:
        self.sequence += 1
        message = ChatMessage.objects.create(
            session=self.session,
            sequence=self.sequence,
            role=ChatMessage.Role.USER,
            content=f"추천 요청 {self.sequence}",
        )
        persona_ids = persona_ids or []
        return ChatRun.objects.create(
            session=self.session,
            request_message=message,
            status=ChatRun.Status.SUCCEEDED,
            response_mode=response_mode,
            persona_ids=persona_ids,
            persona_versions={persona_id: 1 for persona_id in persona_ids},
            persona_prompt_versions={
                persona_id: "stylist-v1" for persona_id in persona_ids
            },
            stylist_config_version="1.0" if persona_ids else "",
        )

    def _result(
        self,
        run: ChatRun,
        *,
        persona_id: str = "",
        display_order: int = 0,
        recommended_at=None,
    ) -> RecommendationResult:
        execution = None
        response_mode = RecommendationResult.ResponseMode.DEFAULT
        persona_version = None
        if persona_id:
            response_mode = RecommendationResult.ResponseMode.STYLIST
            execution = ChatRunPersona.objects.create(
                run=run,
                persona_id=persona_id,
                persona_version=1,
                prompt_version="stylist-v1",
                display_order=display_order,
                strategy_snapshot={"persona_id": persona_id},
            )
            persona_version = 1
        result = RecommendationResult.objects.create(
            identity=self.identity,
            session=self.session,
            run=run,
            persona_execution=execution,
            response_mode=response_mode,
            persona_id=persona_id,
            persona_version=persona_version,
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="goldenset-test-v1",
        )
        if recommended_at is not None:
            RecommendationResult.objects.filter(pk=result.pk).update(
                created_at=recommended_at
            )
            result.refresh_from_db()
        return result

    @staticmethod
    def _card(
        result: RecommendationResult,
        *,
        status: str = OutfitComposition.Status.VALIDATED,
        source_id: str = "product-1",
    ) -> OutfitComposition:
        card = OutfitComposition.objects.create(
            result=result,
            rank=1,
            status=status,
            composition_fingerprint="a" * 64,
            validation_reasons=[],
            warnings=[],
        )
        OutfitCompositionItem.objects.create(
            composition=card,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
            source_id=source_id,
            source_collection="naver_products",
            source_point_id=f"point-{source_id}",
            template_item_point_id="template-top",
            image_ref=f"images/{source_id}.jpg",
            reasons=[],
            item_snapshot={
                "style": ["미니멀"],
                "color": "네이비",
                "fit": ["레귤러핏"],
            },
        )
        return card

    def test_loads_exactly_ten_previous_runs_and_excludes_current_run(self) -> None:
        base_time = timezone.now() - timedelta(days=20)
        history_runs = []
        for index in range(11):
            run = self._run()
            result = self._result(
                run,
                recommended_at=base_time + timedelta(days=index),
            )
            self._card(result, source_id=f"product-{index}")
            history_runs.append(run)
        current_run = self._run()
        current_result = self._result(current_run, recommended_at=timezone.now())
        self._card(current_result, source_id="current-product")

        history = load_recent_recommendations(
            identity=self.identity,
            current_run=current_run,
        )

        self.assertEqual(history["run_limit"], 10)
        self.assertEqual(len(history["runs"]), 10)
        loaded_ids = [row["run_id"] for row in history["runs"]]
        self.assertNotIn(str(current_run.id), loaded_ids)
        self.assertNotIn(str(history_runs[0].id), loaded_ids)
        self.assertEqual(loaded_ids[0], str(history_runs[-1].id))

    def test_groups_stylists_by_run_and_structures_signals_and_repetitions(
        self,
    ) -> None:
        stylist_run = self._run(
            response_mode=ChatSession.ResponseMode.STYLIST,
            persona_ids=["minimal", "practical"],
        )
        practical = self._result(
            stylist_run,
            persona_id="practical",
            display_order=3,
        )
        minimal = self._result(
            stylist_run,
            persona_id="minimal",
            display_order=1,
        )
        practical_card = self._card(practical, source_id="shared-product")
        minimal_saved_card = self._card(minimal, source_id="shared-product")
        saved = SavedOutfit.objects.create(
            user=self.user,
            composition=minimal_saved_card,
        )
        GoldenTemplateSnapshot.objects.create(
            result=minimal,
            golden_id="golden-1",
            point_id="point-golden-1",
            retrieval_score=0.9,
            payload_snapshot={"styles": ["모던"], "color_family": "딥"},
            reasons=[],
        )
        RecommendationFeedback.objects.create(
            composition=practical_card,
            reaction=RecommendationFeedback.Reaction.LIKE,
            reason_codes=["STYLE"],
            comment="자주 입을 수 있어요",
        )
        current_run = self._run()

        history = load_recent_recommendations(
            identity=self.identity,
            current_run=current_run,
        )

        self.assertEqual(len(history["runs"]), 1)
        results = history["runs"][0]["results"]
        self.assertEqual(
            [result["persona_id"] for result in results],
            ["minimal", "practical"],
        )
        minimal_card = results[0]["cards"][0]
        practical_payload = results[1]["cards"][0]
        self.assertEqual(minimal_card["major_slots"], ["TOP"])
        self.assertEqual(minimal_card["styles"], ["모던", "미니멀"])
        self.assertEqual(minimal_card["colors"], ["딥", "네이비"])
        self.assertEqual(minimal_card["fits"], ["레귤러핏"])
        self.assertTrue(minimal_card["is_saved"])
        self.assertEqual(minimal_card["saved_at"], saved.created_at.isoformat())
        self.assertFalse(practical_payload["is_saved"])
        self.assertIsNone(practical_payload["saved_at"])
        self.assertEqual(practical_payload["feedback"]["reaction"], "LIKE")
        self.assertTrue(history["saved_signal_available"])
        self.assertEqual(history["repetitions"]["items"][0]["count"], 2)
        self.assertEqual(history["repetitions"]["combinations"][0]["count"], 2)
        self.assertEqual(history["repetitions"]["slots"], [{"slot": "TOP", "count": 2}])

    def test_ignores_runs_without_validated_cards(self) -> None:
        rejected_run = self._run()
        rejected_result = self._result(rejected_run)
        self._card(
            rejected_result,
            status=OutfitComposition.Status.REJECTED,
        )

        history = load_recent_recommendations(
            identity=self.identity,
            current_run=self._run(),
        )

        self.assertEqual(history["runs"], [])

    def test_requires_member_identity_and_matching_current_run(self) -> None:
        current_run = self._run()
        guest_identity = identity_service.issue_guest_identity().identity
        guest_session = ChatSession.objects.create(
            identity=guest_identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        guest_message = ChatMessage.objects.create(
            session=guest_session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="게스트 요청",
        )
        guest_run = ChatRun.objects.create(
            session=guest_session,
            request_message=guest_message,
        )

        with self.assertRaises(MemberRecentRecommendationsRequired):
            load_recent_recommendations(
                identity=guest_identity,
                current_run=guest_run,
            )
        with self.assertRaises(CurrentRunOwnershipMismatch):
            load_recent_recommendations(
                identity=self.identity,
                current_run=guest_run,
            )
        self.assertEqual(current_run.session.identity_id, self.identity.id)
