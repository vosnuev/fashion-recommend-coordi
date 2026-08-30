from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.chat.models import ChatRunPersona, ChatSession
from apps.chat.services import identity as identity_service
from apps.chat.services import orchestrator, sessions
from apps.recommend.models import (
    OutfitComposition,
    OutfitCompositionItem,
    OutfitRenderJob,
    RecommendationResult,
    SavedOutfit,
)

User = get_user_model()


class StylistResultResponseTests(APITestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="stylist-result-member")
        self.identity = identity_service.get_or_create_member_identity(self.user)
        self.session = sessions.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.NEW_ITEM,
        )
        self.session.response_mode = ChatSession.ResponseMode.STYLIST
        self.session.selected_persona_ids = [
            "minimal",
            "experimental",
            "practical",
        ]
        self.session.save(
            update_fields=["response_mode", "selected_persona_ids", "updated_at"]
        )
        _message, _message_created, self.run, _run_created = (
            orchestrator.submit_message_and_create_run(
                identity=self.identity,
                session_id=self.session.pk,
                content="출근 코디를 추천해줘",
                client_message_id="stylist-result-response",
            )
        )
        self.client.force_authenticate(self.user)

    def test_run_response_exposes_partial_results_in_fixed_order(self) -> None:
        minimal = self.run.persona_executions.get(persona_id="minimal")
        experimental = self.run.persona_executions.get(persona_id="experimental")
        practical = self.run.persona_executions.get(persona_id="practical")
        now = timezone.now()
        ChatRunPersona.objects.filter(pk=minimal.pk).update(
            status=ChatRunPersona.Status.SUCCEEDED,
            started_at=now,
            completed_at=now,
            latency_ms=120,
        )
        ChatRunPersona.objects.filter(pk=experimental.pk).update(
            status=ChatRunPersona.Status.RUNNING,
            started_at=now,
        )
        ChatRunPersona.objects.filter(pk=practical.pk).update(
            status=ChatRunPersona.Status.FAILED,
            error_code="STYLIST_PERSONA_TIMEOUT",
            error_message="추천 처리 시간이 초과되었습니다.",
            started_at=now,
            completed_at=now,
            latency_ms=500,
        )
        result = RecommendationResult.objects.create(
            identity=self.identity,
            session=self.session,
            run=self.run,
            persona_execution=minimal,
            response_mode=RecommendationResult.ResponseMode.STYLIST,
            persona_id="minimal",
            persona_version=minimal.persona_version,
            persona_explanation="색상 조화 기준으로 차분하게 정리했어요.",
            validated_reason_codes=["MINIMAL_COLOR_COHESION"],
            strategy_snapshot=minimal.strategy_snapshot,
            mode=self.session.mode,
            dataset_version="goldenset-v1",
        )
        card = OutfitComposition.objects.create(
            result=result,
            rank=1,
            status=OutfitComposition.Status.VALIDATED,
            composition_fingerprint="a" * 64,
            total_product_price=59_000,
            validation_reasons=[{"code": "VALID", "message": "검증 통과"}],
            reference_match={
                "schema_version": "1.0",
                "match_type": "VISUAL_SIMILAR",
                "selection_role": "PINNED_REFERENCE_ANCHOR",
                "source_type": "PRODUCT",
                "source_id": "product-1",
                "source_collection": "products-v1",
                "source_point_id": "point-1",
                "template_item_point_id": "template-1",
                "score": 0.91,
                "reasons": ["공유 옷 이미지와 유사함"],
            },
            warnings=[],
        )
        OutfitCompositionItem.objects.create(
            composition=card,
            position=1,
            slot="TOP",
            source_type=OutfitCompositionItem.SourceType.PRODUCT,
            source_id="product-1",
            source_collection="products-v1",
            source_point_id="point-1",
            template_item_point_id="template-1",
            replacement_score=0.91,
            image_ref="images/product-1.jpg",
            price_snapshot=59_000,
            reasons=["스타일과 카테고리가 일치함"],
            item_snapshot={"name": "크루넥 니트", "category": "상의"},
        )
        job = OutfitRenderJob.objects.create(
            composition=card,
            status=OutfitRenderJob.Status.QUEUED,
            composition_fingerprint="a" * 64,
            render_fingerprint="b" * 64,
        )
        SavedOutfit.objects.create(user=self.user, composition=card)

        response = self.client.get(reverse("chat:run-detail", args=[self.run.pk]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["results"]
        self.assertEqual(
            [row["persona_id"] for row in rows],
            ["minimal", "experimental", "practical"],
        )
        self.assertEqual(rows[0]["display_name"], "미니멀")
        self.assertEqual(rows[0]["status"], "SUCCEEDED")
        self.assertEqual(str(rows[0]["result_id"]), str(result.pk))
        self.assertEqual(str(rows[0]["card"]["card_id"]), str(card.pk))
        self.assertEqual(rows[0]["card"]["items"][0]["display_name"], "크루넥 니트")
        self.assertEqual(
            rows[0]["card"]["reference_match"]["match_type"],
            "VISUAL_SIMILAR",
        )
        self.assertEqual(
            rows[0]["card"]["reference_match"]["source_id"],
            "product-1",
        )
        self.assertEqual(rows[0]["card"]["image"]["status"], "QUEUED")
        self.assertEqual(str(rows[0]["card"]["image"]["job_id"]), str(job.pk))
        self.assertTrue(rows[0]["card"]["is_saved"])
        self.assertEqual(
            rows[0]["validated_reason_codes"],
            ["MINIMAL_COLOR_COHESION"],
        )
        self.assertIsNone(rows[1]["result_id"])
        self.assertIsNone(rows[1]["card"])
        self.assertIsNone(rows[1]["error"])
        self.assertEqual(
            rows[2]["error"],
            {
                "code": "STYLIST_PERSONA_TIMEOUT",
                "message": "추천 처리 시간이 초과되었습니다.",
            },
        )

    def test_default_run_keeps_empty_results_array(self) -> None:
        default_session = sessions.create_session(
            identity=self.identity,
            mode=ChatSession.Mode.WARDROBE_BASED,
        )
        _message, _message_created, run, _run_created = (
            orchestrator.submit_message_and_create_run(
                identity=self.identity,
                session_id=default_session.pk,
                content="기본 추천",
                client_message_id="default-result-response",
            )
        )

        response = self.client.get(reverse("chat:run-detail", args=[run.pk]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["response_mode"], "DEFAULT")
        self.assertEqual(response.data["results"], [])
