from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

from django.apps import apps as django_apps
from django.db.migrations import RunPython
from django.test import SimpleTestCase, TestCase

from apps.chat.models import (
    ChatIdentity,
    ChatMessage,
    ChatRun,
    ChatRunPersona,
    ChatSession,
    MemberStylistSelection,
)
from apps.recommend.models import RecommendationResult


class StylistModelSchemaContractTests(SimpleTestCase):
    EXPECTED_TABLES: ClassVar = {
        MemberStylistSelection: "member_stylist_selection",
        ChatSession: "chat_session",
        ChatRun: "chat_run",
        ChatRunPersona: "chat_run_persona",
        RecommendationResult: "recommendation_result",
    }

    def test_stylist_models_use_explicit_table_names_and_comments(self) -> None:
        for model, expected_table in self.EXPECTED_TABLES.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model._meta.db_table, expected_table)
                self.assertTrue(model._meta.db_table_comment)

    def test_every_local_column_has_database_comment(self) -> None:
        for model in self.EXPECTED_TABLES:
            for field in model._meta.local_fields:
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertTrue(
                        field.db_comment,
                        f"{model.__name__}.{field.name}에 db_comment가 없습니다.",
                    )


class MultiStylistResultDataMigrationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.migration = importlib.import_module(
            "apps.recommend.migrations.0012_multi_stylist_recommendation_results"
        )

    def test_existing_results_are_backfilled_as_default_results(self) -> None:
        manager = Mock()
        historical_model = SimpleNamespace(objects=manager)
        historical_apps = Mock()
        historical_apps.get_model.return_value = historical_model

        self.migration.backfill_default_results(historical_apps, Mock())

        historical_apps.get_model.assert_called_once_with(
            "recommend",
            "RecommendationResult",
        )
        manager.update.assert_called_once_with(
            response_mode="DEFAULT",
            persona_id="",
            persona_version=None,
            persona_explanation="",
            validated_reason_codes=[],
            strategy_snapshot={},
            persona_execution_id=None,
        )

    def test_backfill_runs_before_multi_stylist_constraints(self) -> None:
        operations = self.migration.Migration.operations
        backfill_index = next(
            index
            for index, operation in enumerate(operations)
            if isinstance(operation, RunPython)
        )
        constraint_indexes = [
            index
            for index, operation in enumerate(operations)
            if operation.__class__.__name__ == "AddConstraint"
        ]

        self.assertTrue(constraint_indexes)
        self.assertLess(backfill_index, min(constraint_indexes))


class MultiStylistResultDatabaseMigrationTests(TestCase):
    def test_backfill_updates_existing_database_row_without_losing_ownership(
        self,
    ) -> None:
        identity = ChatIdentity.objects.create(
            identity_type=ChatIdentity.IdentityType.GUEST,
            guest_token_hash="a" * 64,
            expires_at="2099-01-01T00:00:00Z",
        )
        session = ChatSession.objects.create(
            identity=identity,
            mode=ChatSession.Mode.NEW_ITEM,
            response_mode=ChatSession.ResponseMode.STYLIST,
            selected_persona_ids=["minimal"],
        )
        message = ChatMessage.objects.create(
            session=session,
            sequence=1,
            role=ChatMessage.Role.USER,
            content="추천해줘",
        )
        run = ChatRun.objects.create(
            session=session,
            request_message=message,
            response_mode=ChatSession.ResponseMode.STYLIST,
            persona_ids=["minimal"],
            persona_versions={"minimal": 1},
            persona_prompt_versions={"minimal": "minimal-v1"},
            stylist_config_version="1.0",
        )
        execution = ChatRunPersona.objects.create(
            run=run,
            persona_id="minimal",
            persona_version=1,
            prompt_version="minimal-v1",
            display_order=1,
        )
        result = RecommendationResult.objects.create(
            identity=identity,
            session=session,
            run=run,
            persona_execution=execution,
            response_mode=RecommendationResult.ResponseMode.STYLIST,
            persona_id="minimal",
            persona_version=1,
            persona_explanation="기존 설명",
            validated_reason_codes=["WEATHER_MATCH"],
            strategy_snapshot={"candidate_count": 3},
            mode=RecommendationResult.Mode.NEW_ITEM,
            dataset_version="legacy-v1",
        )
        migration = importlib.import_module(
            "apps.recommend.migrations.0012_multi_stylist_recommendation_results"
        )

        migration.backfill_default_results(django_apps, None)
        result.refresh_from_db()

        self.assertEqual(
            result.response_mode,
            RecommendationResult.ResponseMode.DEFAULT,
        )
        self.assertEqual(result.persona_id, "")
        self.assertIsNone(result.persona_version)
        self.assertIsNone(result.persona_execution_id)
        self.assertEqual(result.validated_reason_codes, [])
        self.assertEqual(result.strategy_snapshot, {})
        self.assertEqual(result.identity_id, identity.pk)
        self.assertEqual(result.session_id, session.pk)
        self.assertEqual(result.run_id, run.pk)
        self.assertEqual(result.mode, RecommendationResult.Mode.NEW_ITEM)
        self.assertEqual(result.dataset_version, "legacy-v1")
