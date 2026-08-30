from __future__ import annotations

from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.chat.models import (
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatRunPersona,
    ChatSession,
)
from apps.chat.services.experimental_hypotheses import (
    ExperimentalHypothesis,
    ExperimentalHypothesisBatch,
    ExperimentAxis,
    ExperimentReasonCode,
)
from apps.chat.services.experimental_hypothesis_fallback import (
    ExperimentalHypothesisSource,
    ResolvedExperimentalHypotheses,
)
from apps.chat.services.openai_adapter import (
    RecommendationConditions,
    TurnAnalysis,
)
from apps.chat.services.recommendation_pipeline import ChatRecommendationPipeline
from apps.chat.services.stylist_personas import load_stylist_personas
from apps.chat.services.stylist_recommendation_pipeline import (
    StylistRecommendationPipeline,
)
from apps.recommend.models import RecommendationResult
from apps.recommend.services.item_retriever import ItemSource
from apps.recommend.services.outfit_types import (
    CompositionBatch,
    OutfitComposition,
    OutfitItem,
    RecommendationMode,
)
from apps.recommend.services.retriever import OutfitCandidate, RetrievalResult
from apps.recommend.services.validator import OutfitValidationResult

User = get_user_model()


class StylistRecommendationPipelineTests(TestCase):
    PERSONA_IDS = ("minimal", "experimental", "practical")

    def setUp(self) -> None:
        user = User.objects.create_user(username="persona-pipeline")
        identity = ChatIdentity.objects.create(
            user=user,
            identity_type=ChatIdentity.IdentityType.MEMBER,
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
            content="비 오는 가을날 출근 코디를 추천해줘",
        )
        self.run = ChatRun.objects.create(
            session=session,
            request_message=message,
            response_mode=ChatSession.ResponseMode.STYLIST,
            persona_ids=list(self.PERSONA_IDS),
            persona_versions={persona_id: 1 for persona_id in self.PERSONA_IDS},
            persona_prompt_versions={
                persona_id: f"{persona_id}-v1" for persona_id in self.PERSONA_IDS
            },
            stylist_config_version="1.0",
        )
        catalog = load_stylist_personas()
        self.executions = {
            persona_id: ChatRunPersona.objects.create(
                run=self.run,
                persona_id=persona_id,
                persona_version=1,
                prompt_version=f"{persona_id}-v1",
                display_order=display_order,
                strategy_snapshot=self._strategy_snapshot(
                    catalog.get(persona_id).strategy_profile
                ),
            )
            for display_order, persona_id in enumerate(self.PERSONA_IDS, start=1)
        }

    @staticmethod
    def _strategy_snapshot(profile) -> dict:
        return {
            "objectives": list(profile.objectives),
            "search_directives": list(profile.search_directives),
            "score_weights": [
                {"metric": row.metric, "weight": row.weight}
                for row in profile.score_weights
            ],
            "hypothesis_count": profile.hypothesis_count,
        }

    @staticmethod
    def _context() -> dict:
        return {
            "profile": {
                "pursuit": {
                    "preferred": {"styles": ["캐주얼"]},
                    "avoided": {"colors": ["레드"]},
                }
            },
            "weather": {
                "temperature": 17,
                "apparent_temperature": 15,
                "rain_probability": 0.8,
                "wind_speed": 4,
            },
            "behavior_signals": {
                "summary": {"calendar_registrations_30d": 2},
                "repetition_avoidance": {
                    "recent_recommendations": {
                        "slots": [{"slot": "BOTTOM", "count": 3}]
                    }
                },
                "source_data": {
                    "recent_recommendations": {"runs": []},
                    "calendar_wear": {
                        "worn_items": [],
                        "not_worn_in_30d_items": [],
                    },
                },
            },
            "session": {"mode": "NEW_ITEM", "conditions": {}},
            "current_request": "비 오는 가을날 출근 코디를 추천해줘",
        }

    @staticmethod
    def _analysis() -> TurnAnalysis:
        return TurnAnalysis(
            action="RECOMMEND",
            target_mode="CURRENT",
            search_query="비 오는 가을 출근 캐주얼 코디",
            conditions=RecommendationConditions(
                occasion="출근",
                occasion_kind="FORMAL",
                season="가을",
                presentation_groups=[],
                styles=["캐주얼"],
                colors=[],
                fits=[],
                avoided_styles=[],
                avoided_colors=["레드"],
                excluded_source_ids=[],
                budget=150_000,
            ),
            clarification_question="",
            response_text="",
        )

    @staticmethod
    def _resolved_hypotheses() -> ResolvedExperimentalHypotheses:
        return ResolvedExperimentalHypotheses(
            batch=ExperimentalHypothesisBatch(
                hypotheses=(
                    ExperimentalHypothesis(
                        change_axes=(ExperimentAxis.BOTTOM_SILHOUETTE,),
                        preserve_axes=(ExperimentAxis.TOP_STYLE,),
                        reason_code=(ExperimentReasonCode.RECENT_SILHOUETTE_REPETITION),
                    ),
                    ExperimentalHypothesis(
                        change_axes=(ExperimentAxis.MATERIAL_MIX,),
                        preserve_axes=(ExperimentAxis.COLOR_FAMILY,),
                        reason_code=(
                            ExperimentReasonCode.SAME_COLOR_MATERIAL_VARIATION
                        ),
                    ),
                )
            ),
            source=ExperimentalHypothesisSource.RULE_FALLBACK,
        )

    @staticmethod
    def _composition(
        *,
        source_id: str,
        metrics: dict[str, float],
    ) -> OutfitComposition:
        item = OutfitItem(
            slot_id="TOP",
            template_point_id="template-top",
            category_large="상의",
            layer_role="top",
            source_type=ItemSource.PRODUCT,
            source_id=source_id,
            source_collection="products_naver_v1",
            point_id=f"point-{source_id}",
            image_ref=f"https://example.com/{source_id}.jpg",
            price=50_000,
            score=0.9,
            reasons=("골든 아이템과 유사",),
            payload={
                "title": source_id,
                "styles": ["캐주얼"],
                "colors": ["네이비"],
                "fits": ["레귤러핏"],
                "metrics": metrics,
            },
        )
        return OutfitComposition(
            mode=RecommendationMode.NEW_ITEM,
            items=(item,),
            missing_slot_ids=(),
            total_product_price=50_000,
        )

    @staticmethod
    def _multi_item_composition(items: dict[str, str]) -> OutfitComposition:
        return OutfitComposition(
            mode=RecommendationMode.NEW_ITEM,
            items=tuple(
                OutfitItem(
                    slot_id=slot,
                    template_point_id=f"template-{slot.lower()}",
                    category_large=slot,
                    layer_role="",
                    source_type=ItemSource.PRODUCT,
                    source_id=source_id,
                    source_collection="products_naver_v1",
                    point_id=f"point-{source_id}",
                    image_ref=f"https://example.com/{source_id}.jpg",
                    price=10_000,
                    score=0.9,
                    reasons=("골든 아이템과 유사",),
                    payload={
                        "title": source_id,
                        "styles": ["캐주얼"],
                        "colors": ["네이비"],
                        "fits": ["레귤러핏"],
                    },
                )
                for slot, source_id in items.items()
            ),
            missing_slot_ids=(),
            total_product_price=len(items) * 10_000,
        )

    def _service(self) -> tuple[StylistRecommendationPipeline, Mock, Mock, Mock]:
        active_personas: list[str] = []
        golden = Mock()

        def retrieve(request):
            if "정돈된 색상" in request.query_text:
                persona_id = "minimal"
            elif "최근 추천과 다른 관계" in request.query_text:
                persona_id = "experimental"
            else:
                persona_id = "practical"
            active_personas.append(persona_id)
            return RetrievalResult(
                candidates=(
                    OutfitCandidate(
                        point_id=f"outfit-{persona_id}",
                        golden_id=f"golden-{persona_id}",
                        score=80,
                        similarity=0.8,
                        payload={
                            "item_point_ids": ["template-top"],
                            "dataset_version": "goldenset-v1",
                            "tag_confidence": 0.8,
                        },
                    ),
                ),
                search_mode="text",
            )

        golden.retrieve.side_effect = retrieve
        composer = Mock()

        def compose(_request):
            persona_id = active_personas.pop(0)
            if persona_id == "minimal":
                compositions = (
                    self._composition(
                        source_id="minimal-complex",
                        metrics={
                            "visual_focus_count": 4,
                            "layer_complexity": 0.9,
                            "pattern_detail_density": 0.9,
                        },
                    ),
                    self._composition(
                        source_id="minimal-simple",
                        metrics={
                            "visual_focus_count": 1,
                            "layer_complexity": 0.1,
                            "pattern_detail_density": 0.1,
                        },
                    ),
                )
            elif persona_id == "practical":
                compositions = (
                    self._composition(
                        source_id="practical-weather",
                        metrics={
                            "temperature_fit": 0.9,
                            "apparent_temperature_fit": 0.9,
                            "precipitation_fit": 0.9,
                            "wind_fit": 0.9,
                        },
                    ),
                )
            else:
                compositions = (
                    self._composition(
                        source_id="experimental-novel",
                        metrics={"novelty": 0.9, "cross_style": 0.8},
                    ),
                )
            return CompositionBatch(
                mode=RecommendationMode.NEW_ITEM,
                compositions=compositions,
            )

        composer.compose.side_effect = compose
        validator = Mock()
        validator.validate.side_effect = lambda composition, **_: (
            OutfitValidationResult(
                issues=(),
                effective_total_product_price=composition.total_product_price,
            )
        )
        hypothesis_resolver = Mock()
        hypothesis_resolver.resolve.return_value = self._resolved_hypotheses()
        base_pipeline = ChatRecommendationPipeline(
            golden_retriever=golden,
            item_retriever=Mock(retrieve=Mock(return_value=object())),
            wardrobe_composer=Mock(),
            new_item_composer=composer,
            validator=validator,
        )
        return (
            StylistRecommendationPipeline(
                recommendation_pipeline=base_pipeline,
                hypothesis_resolver=hypothesis_resolver,
            ),
            golden,
            composer,
            validator,
        )

    def test_each_persona_runs_independent_search_composition_and_validation(
        self,
    ) -> None:
        context = self._context()
        analysis = self._analysis()
        service, golden, composer, validator = self._service()
        shared_context = service.build_context(
            run=self.run,
            context=context,
            analysis=analysis,
        )

        results = {
            persona_id: service.execute_persona(
                run=self.run,
                persona_execution=self.executions[persona_id],
                context=context,
                analysis=analysis,
                strategy_context=shared_context,
            )
            for persona_id in self.PERSONA_IDS
        }

        requests = [call.args[0] for call in golden.retrieve.call_args_list]
        self.assertEqual(len({request.query_text for request in requests}), 3)
        self.assertEqual([request.limit for request in requests], [20, 24, 18])
        self.assertTrue(
            all(
                request.occasion == "출근" and request.season == "가을"
                for request in requests
            )
        )
        self.assertIn("미니멀", requests[0].pursuit["preferred"]["styles"])
        self.assertTrue(
            all("레드" in request.pursuit["avoided"]["colors"] for request in requests)
        )
        self.assertEqual(composer.compose.call_count, 3)
        self.assertEqual(validator.validate.call_count, 4)
        self.assertTrue(
            all(
                call.kwargs["context"].season == "가을"
                and call.kwargs["context"].occasion == "출근"
                and call.kwargs["context"].total_budget == 150_000
                for call in validator.validate.call_args_list
            )
        )
        self.assertEqual(
            results["minimal"]
            .ranked_candidates[0]
            .candidate.composition.items[0]
            .source_id,
            "minimal-simple",
        )
        self.assertEqual(
            {
                result.generated.candidates[0].golden.golden_id
                for result in results.values()
            },
            {"golden-minimal", "golden-experimental", "golden-practical"},
        )
        self.assertFalse(RecommendationResult.objects.exists())

        experimental = self.executions["experimental"]
        experimental.refresh_from_db()
        self.assertEqual(
            experimental.hypothesis_snapshot["source"],
            "RULE_FALLBACK",
        )

    def test_persona_top_k_keeps_core_distinct_candidate_not_accessory_variants(
        self,
    ) -> None:
        context = self._context()
        analysis = self._analysis()
        service, _golden, composer, _validator = self._service()
        composer.compose.side_effect = None
        composer.compose.return_value = CompositionBatch(
            mode=RecommendationMode.NEW_ITEM,
            compositions=(
                self._multi_item_composition(
                    {
                        "TOP": "shared-top",
                        "BOTTOM": "shared-bottom",
                        "ACCESSORY": "accessory-1",
                    }
                ),
                self._multi_item_composition(
                    {
                        "TOP": "shared-top",
                        "BOTTOM": "shared-bottom",
                        "ACCESSORY": "accessory-2",
                    }
                ),
                self._multi_item_composition(
                    {
                        "TOP": "shared-top",
                        "BOTTOM": "shared-bottom",
                        "ACCESSORY": "accessory-3",
                    }
                ),
                self._multi_item_composition(
                    {
                        "TOP": "distinct-top",
                        "BOTTOM": "distinct-bottom",
                        "ACCESSORY": "accessory-4",
                    }
                ),
            ),
        )

        result = service.execute_persona(
            run=self.run,
            persona_execution=self.executions["minimal"],
            context=context,
            analysis=analysis,
        )

        self.assertEqual(len(result.ranked_candidates), 2)
        self.assertEqual(
            [
                row.candidate.composition.items[0].source_id
                for row in result.ranked_candidates
            ],
            ["shared-top", "distinct-top"],
        )
