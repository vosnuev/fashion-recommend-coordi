from __future__ import annotations

from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.chat.services.behavior_event_history import (
    MemberBehaviorHistoryRequired,
    load_product_click_history,
    load_saved_outfit_history,
    summarize_behavior_features,
)


class BehaviorEventHistoryTests(SimpleTestCase):
    def test_outfit_features_are_not_double_counted_by_nested_items(self) -> None:
        summary = summarize_behavior_features(
            outfits=[
                {
                    "styles": ["미니멀"],
                    "colors": ["네이비"],
                    "fits": ["레귤러핏"],
                    "slots": ["TOP"],
                    "items": [
                        {
                            "slot": "TOP",
                            "source_type": "PRODUCT",
                            "source_collection": "naver",
                            "source_id": "101",
                            "styles": ["미니멀"],
                            "colors": ["네이비"],
                            "fits": ["레귤러핏"],
                        }
                    ],
                }
            ]
        )

        self.assertEqual(summary["styles"], [{"value": "미니멀", "count": 1}])
        self.assertEqual(summary["slots"], [{"value": "TOP", "count": 1}])
        self.assertEqual(summary["items"][0]["source_id"], "101")
        self.assertEqual(summary["items"][0]["count"], 1)

    def test_clicked_item_features_are_counted(self) -> None:
        summary = summarize_behavior_features(
            items=[
                {
                    "slot": "SHOES",
                    "source_type": "PRODUCT",
                    "source_collection": "eleven",
                    "source_id": "shoe-1",
                    "styles": ["스포티"],
                    "colors": ["화이트"],
                    "fits": [],
                }
            ]
        )

        self.assertEqual(summary["styles"], [{"value": "스포티", "count": 1}])
        self.assertEqual(summary["slots"], [{"value": "SHOES", "count": 1}])

    def test_member_only_loaders_reject_guest_before_database_access(self) -> None:
        identity = Mock(user_id=None)

        with self.assertRaises(MemberBehaviorHistoryRequired):
            load_saved_outfit_history(identity=identity)
        with self.assertRaises(MemberBehaviorHistoryRequired):
            load_product_click_history(identity=identity)
