from __future__ import annotations

from datetime import date
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.chat.services.behavior_signals import load_user_behavior_signals


class UserBehaviorSignalServiceTests(SimpleTestCase):
    AS_OF = date(2026, 8, 15)

    @staticmethod
    def _recent_recommendations() -> dict:
        return {
            "run_limit": 10,
            "runs": [
                {
                    "run_id": "run-1",
                    "recommended_at": "2026-08-14T12:00:00+09:00",
                    "results": [
                        {
                            "result_id": "result-1",
                            "persona_id": "minimal",
                            "cards": [
                                {
                                    "composition_id": "card-like",
                                    "styles": ["미니멀"],
                                    "colors": ["네이비"],
                                    "fits": ["레귤러핏"],
                                    "items": [{"source_id": "product-1"}],
                                    "feedback": {
                                        "reaction": "LIKE",
                                        "reason_codes": ["STYLE"],
                                        "comment": "마음에 들어요",
                                        "updated_at": "2026-08-14T12:05:00+09:00",
                                    },
                                },
                                {
                                    "composition_id": "card-dislike",
                                    "styles": ["캐주얼"],
                                    "colors": ["레드"],
                                    "fits": ["오버핏"],
                                    "items": [{"source_id": "product-2"}],
                                    "feedback": {
                                        "reaction": "DISLIKE",
                                        "reason_codes": ["COLOR", "FIT"],
                                        "comment": "색과 핏이 아쉬워요",
                                        "updated_at": "2026-08-14T12:06:00+09:00",
                                    },
                                },
                                {
                                    "composition_id": "card-no-feedback",
                                    "styles": [],
                                    "colors": [],
                                    "fits": [],
                                    "items": [],
                                    "feedback": None,
                                },
                            ],
                        }
                    ],
                }
            ],
            "repetitions": {
                "items": [{"source_id": "product-1", "count": 2}],
                "combinations": [],
                "slots": [{"slot": "TOP", "count": 2}],
            },
            "saved_signal_available": True,
        }

    @staticmethod
    def _saved_outfits() -> dict:
        return {
            "history_limit": 100,
            "history_scope": "LATEST_100_SAVED_OUTFITS",
            "events": [
                {
                    "saved_outfit_id": "saved-1",
                    "saved_at": "2026-08-14T12:07:00+09:00",
                    "result_id": "result-1",
                    "composition_id": "card-like",
                    "persona_id": "minimal",
                    "outfit": {"styles": ["미니멀"], "items": []},
                }
            ],
            "feature_counts": {},
        }

    @staticmethod
    def _product_clicks() -> dict:
        return {
            "history_limit": 100,
            "history_scope": "LATEST_100_PRODUCT_CLICKS",
            "events": [
                {
                    "product_click_id": "click-1",
                    "clicked_at": "2026-08-14T12:04:00+09:00",
                    "engagement_duration_ms": 42_000,
                    "engagement_recorded_at": "2026-08-14T12:04:42+09:00",
                    "result_id": "result-1",
                    "composition_id": "card-like",
                    "persona_id": "minimal",
                    "item": {"source_id": "product-1"},
                },
                {
                    "product_click_id": "click-2",
                    "clicked_at": "2026-08-14T12:08:00+09:00",
                    "engagement_duration_ms": None,
                    "engagement_recorded_at": None,
                    "result_id": "result-1",
                    "composition_id": "card-no-feedback",
                    "persona_id": "minimal",
                    "item": {"source_id": "product-3"},
                },
            ],
            "feature_counts": {},
        }

    @staticmethod
    def _calendar_wear() -> dict:
        return {
            "as_of_date": "2026-08-15",
            "entry_counts": {"7d": 2, "14d": 3, "30d": 4},
            "linked_item_occurrence_counts": {"7d": 3, "14d": 5, "30d": 7},
            "recent_entries": [
                {
                    "calendar_id": "calendar-1",
                    "worn_on": "2026-08-15",
                    "status": "COMPLETED",
                    "source_type": "WARDROBE_SELECTED",
                    "tpo": ["DAILY"],
                    "linked_item_count": 2,
                    "items": [
                        {"wardrobe_item_id": "wardrobe-1"},
                        {"wardrobe_item_id": "wardrobe-2"},
                    ],
                },
                {
                    "calendar_id": "calendar-2",
                    "worn_on": "2026-08-14",
                    "status": "PROCESSING",
                    "source_type": "PHOTO_UPLOAD",
                    "tpo": [],
                    "linked_item_count": 0,
                    "items": [],
                },
            ],
            "worn_items": [
                {
                    "wardrobe_item_id": "wardrobe-1",
                    "wear_counts": {"7d": 2, "14d": 3, "30d": 4},
                    "last_worn_on": "2026-08-15",
                }
            ],
            "repeated_combinations_30d": [
                {
                    "wardrobe_item_ids": ["wardrobe-1", "wardrobe-2"],
                    "count": 2,
                }
            ],
            "not_worn_in_30d_items": [
                {"wardrobe_item_id": "wardrobe-3", "last_worn_on": None}
            ],
        }

    @patch("apps.chat.services.behavior_signals.load_product_click_history")
    @patch("apps.chat.services.behavior_signals.load_saved_outfit_history")
    @patch("apps.chat.services.behavior_signals.load_calendar_wear_history")
    @patch("apps.chat.services.behavior_signals.load_recent_recommendations")
    def test_loads_each_source_once_and_preserves_signal_strengths(
        self,
        recent_loader,
        calendar_loader,
        saved_loader,
        click_loader,
    ) -> None:
        recent_loader.return_value = self._recent_recommendations()
        calendar_loader.return_value = self._calendar_wear()
        saved_loader.return_value = self._saved_outfits()
        click_loader.return_value = self._product_clicks()
        identity = Mock(name="identity")
        current_run = Mock(name="current_run")

        result = load_user_behavior_signals(
            identity=identity,
            current_run=current_run,
            as_of=self.AS_OF,
        )

        recent_loader.assert_called_once_with(
            identity=identity,
            current_run=current_run,
        )
        calendar_loader.assert_called_once_with(
            identity=identity,
            as_of=self.AS_OF,
        )
        saved_loader.assert_called_once_with(identity=identity)
        click_loader.assert_called_once_with(identity=identity)
        self.assertEqual(result["schema_version"], "1.1")
        self.assertEqual(result["as_of_date"], "2026-08-15")
        self.assertEqual(result["summary"]["calendar_registrations_30d"], 4)
        self.assertEqual(result["summary"]["worn_item_occurrences_30d"], 7)

        calendar_signals = result["signals"]["strong_preferences"][
            "calendar_registrations"
        ]
        self.assertEqual(len(calendar_signals), 2)
        self.assertTrue(
            all(signal["strength"] == "STRONG" for signal in calendar_signals)
        )
        self.assertEqual(calendar_signals[1]["linked_item_count"], 0)
        self.assertEqual(calendar_signals[1]["items"], [])

        like = result["signals"]["weak_preferences"]["liked_recommendation_cards"][0]
        dislike = result["signals"]["negative_preferences"][
            "disliked_recommendation_cards"
        ][0]
        self.assertEqual(like["signal_type"], "RECOMMENDATION_LIKE")
        self.assertEqual(like["strength"], "WEAK")
        self.assertEqual(like["outfit"]["styles"], ["미니멀"])
        self.assertEqual(dislike["signal_type"], "RECOMMENDATION_DISLIKE")
        self.assertEqual(dislike["strength"], "NEGATIVE")
        self.assertEqual(dislike["reason_codes"], ["COLOR", "FIT"])

        saved = result["signals"]["weak_preferences"]["saved_outfits"][0]
        clicks = result["signals"]["reference_information"]["product_clicks"]
        self.assertEqual(saved["signal_type"], "OUTFIT_SAVED")
        self.assertEqual(saved["strength"], "WEAK")
        self.assertEqual(clicks[0]["strength"], "REFERENCE")
        self.assertEqual(clicks[0]["polarity"], "NEUTRAL")
        self.assertEqual(clicks[0]["engagement_duration_ms"], 42_000)
        self.assertTrue(clicks[0]["corroborated_preference"])
        self.assertEqual(
            clicks[0]["preference_evidence"],
            ["OUTFIT_SAVED", "RECOMMENDATION_LIKE"],
        )
        self.assertFalse(clicks[1]["corroborated_preference"])
        self.assertEqual(result["summary"]["product_clicks_with_duration"], 1)

    @patch("apps.chat.services.behavior_signals.load_product_click_history")
    @patch("apps.chat.services.behavior_signals.load_saved_outfit_history")
    @patch("apps.chat.services.behavior_signals.load_calendar_wear_history")
    @patch("apps.chat.services.behavior_signals.load_recent_recommendations")
    def test_available_empty_sources_are_reported_as_zero_events(
        self,
        recent_loader,
        calendar_loader,
        saved_loader,
        click_loader,
    ) -> None:
        recent_loader.return_value = self._recent_recommendations()
        calendar_loader.return_value = self._calendar_wear()
        saved_loader.return_value = {
            **self._saved_outfits(),
            "events": [],
        }
        click_loader.return_value = {
            **self._product_clicks(),
            "events": [],
        }

        result = load_user_behavior_signals(
            identity=Mock(),
            current_run=Mock(),
            as_of=self.AS_OF,
        )

        self.assertTrue(result["collection_status"]["saved_outfits"]["available"])
        self.assertTrue(result["collection_status"]["product_clicks"]["available"])
        self.assertEqual(result["summary"]["saved_outfits"], 0)
        self.assertEqual(result["summary"]["product_clicks"], 0)
        self.assertEqual(result["signals"]["weak_preferences"]["saved_outfits"], [])
        self.assertEqual(
            result["signals"]["reference_information"]["product_clicks"], []
        )

    @patch("apps.chat.services.behavior_signals.load_product_click_history")
    @patch("apps.chat.services.behavior_signals.load_saved_outfit_history")
    @patch("apps.chat.services.behavior_signals.load_calendar_wear_history")
    @patch("apps.chat.services.behavior_signals.load_recent_recommendations")
    def test_recommendation_exposure_is_only_used_for_repetition_avoidance(
        self,
        recent_loader,
        calendar_loader,
        saved_loader,
        click_loader,
    ) -> None:
        recent = self._recent_recommendations()
        calendar = self._calendar_wear()
        recent_loader.return_value = recent
        calendar_loader.return_value = calendar
        saved_loader.return_value = self._saved_outfits()
        click_loader.return_value = self._product_clicks()

        result = load_user_behavior_signals(
            identity=Mock(),
            current_run=Mock(),
            as_of=self.AS_OF,
        )

        self.assertEqual(
            result["repetition_avoidance"]["recent_recommendations"],
            recent["repetitions"],
        )
        self.assertEqual(
            result["repetition_avoidance"]["recent_calendar_combinations"],
            calendar["repeated_combinations_30d"],
        )
        self.assertNotIn("recommendation_exposure", result["signals"])
