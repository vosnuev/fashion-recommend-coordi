from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import NaverProduct
from apps.lookbook.models import CuratedLook, CuratedLookItem
from apps.lookbook.services import discovery


class DiscoveryServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        CuratedLook.objects.create(
            external_id="woman-casual-001",
            gender=CuratedLook.Gender.WOMAN,
            category="캐주얼",
            title="여성 캐주얼 룩",
            subtitle="여성 테스트",
            cover_image_url="images/woman-casual-001.png",
            tags=["캐주얼"],
        )
        CuratedLook.objects.create(
            external_id="man-casual-001",
            gender=CuratedLook.Gender.MAN,
            category="캐주얼",
            title="남성 캐주얼 룩",
            subtitle="남성 테스트",
            cover_image_url="images/man-casual-001.png",
            tags=["캐주얼"],
        )
        CuratedLook.objects.create(
            external_id="man-hidden-001",
            gender=CuratedLook.Gender.MAN,
            category="캐주얼",
            title="비공개 남성 룩",
            subtitle="노출되지 않음",
            cover_image_url="images/man-hidden-001.png",
            tags=["캐주얼"],
            is_active=False,
        )

    def test_default_feed_returns_all_active_genders(self) -> None:
        result = discovery.list_looks(discovery.DiscoveryQuery())

        self.assertEqual(result["count"], 2)
        self.assertEqual(
            {look["gender"] for look in result["results"]}, {"WOMAN", "MAN"}
        )

    def test_woman_filter_only_returns_active_woman_looks(self) -> None:
        result = discovery.list_looks(
            discovery.DiscoveryQuery(gender="WOMAN", limit=50)
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["id"], "curated-woman-casual-001")
        self.assertEqual(result["results"][0]["gender"], "WOMAN")

    def test_man_filter_only_returns_active_man_looks(self) -> None:
        result = discovery.list_looks(
            discovery.DiscoveryQuery(gender="MAN", limit=50)
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["id"], "curated-man-casual-001")
        self.assertEqual(result["results"][0]["gender"], "MAN")

    def test_tag_and_gender_filters_are_combined(self) -> None:
        result = discovery.list_looks(
            discovery.DiscoveryQuery(gender="MAN", tag="데이트")
        )

        self.assertEqual(result, {"count": 0, "next_offset": None, "results": []})

    def test_related_products_are_limited_to_the_source_slot(self) -> None:
        look = CuratedLook.objects.get(external_id="woman-casual-001")
        item = CuratedLookItem.objects.create(
            look=look,
            slot="상의",
            category_small="티셔츠",
            name="블랙 반팔 티셔츠",
            product_url="https://example.com/original",
            image_url="https://example.com/original.jpg",
            related_keyword="블랙 반팔 티셔츠",
        )
        NaverProduct.objects.create(
            naver_product_id="same-slot-top",
            title="블랙 반팔 티셔츠",
            image_url="https://example.com/top.jpg",
            lprice=20_000,
            category_large="상의",
            category_small="티셔츠",
            collected_at=timezone.now(),
        )
        NaverProduct.objects.create(
            naver_product_id="wrong-slot-drawers",
            title="블랙 남성 드로즈",
            image_url="https://example.com/drawers.jpg",
            lprice=1_000,
            category_large="언더웨어/이너웨어",
            category_small="팬티/드로즈",
            collected_at=timezone.now(),
        )

        result = discovery._related(item)

        self.assertEqual([product["id"] for product in result], ["same-slot-top"])
        self.assertEqual(result[0]["category_large"], "상의")
        payload = discovery._look(look)["items"][0]
        self.assertEqual(payload["category_large"], "상의")
        self.assertEqual(payload["link"], "https://example.com/original")
        self.assertEqual(
            [product["id"] for product in payload["similar_products"]],
            ["same-slot-top"],
        )
        self.assertNotIn(
            f"original-{item.pk}",
            [product["id"] for product in payload["similar_products"]],
        )

    def test_related_products_are_empty_for_unknown_source_slot(self) -> None:
        look = CuratedLook.objects.get(external_id="woman-casual-001")
        item = CuratedLookItem.objects.create(
            look=look,
            slot="알 수 없는 슬롯",
            name="블랙 아이템",
            product_url="https://example.com/original",
            related_keyword="블랙 아이템",
        )
        NaverProduct.objects.create(
            naver_product_id="fallback-top",
            title="블랙 아이템 티셔츠",
            image_url="https://example.com/top.jpg",
            lprice=10_000,
            category_large="상의",
            category_small="티셔츠",
            collected_at=timezone.now(),
        )

        self.assertEqual(discovery._related(item), [])

    def test_related_products_require_at_least_two_keyword_matches(self) -> None:
        look = CuratedLook.objects.get(external_id="woman-casual-001")
        item = CuratedLookItem.objects.create(
            look=look,
            slot="액세서리",
            category_small="주얼리",
            name="블랙 진주 헤어핀",
            product_url="https://example.com/original",
            related_keyword="블랙 진주 헤어핀",
        )
        NaverProduct.objects.create(
            naver_product_id="one-keyword-match",
            title="블랙 가죽 벨트",
            image_url="https://example.com/belt.jpg",
            lprice=1_000,
            category_large="액세서리",
            category_small="벨트",
            collected_at=timezone.now(),
        )
        NaverProduct.objects.create(
            naver_product_id="two-keyword-matches",
            title="블랙 진주 귀걸이",
            image_url="https://example.com/earrings.jpg",
            lprice=20_000,
            category_large="액세서리",
            category_small="주얼리",
            collected_at=timezone.now(),
        )

        result = discovery._related(item)

        self.assertEqual(
            [product["id"] for product in result], ["two-keyword-matches"]
        )

    def test_related_products_rank_keyword_matches_before_price(self) -> None:
        look = CuratedLook.objects.get(external_id="woman-casual-001")
        item = CuratedLookItem.objects.create(
            look=look,
            slot="신발",
            category_small="부츠",
            name="블랙 버클 워커 미들부츠",
            product_url="https://example.com/original",
            related_keyword="블랙 버클 워커 미들부츠",
        )
        NaverProduct.objects.create(
            naver_product_id="cheap-two-matches",
            title="블랙 버클 첼시부츠",
            image_url="https://example.com/loafer.jpg",
            lprice=1_000,
            category_large="신발",
            category_small="부츠",
            collected_at=timezone.now(),
        )
        NaverProduct.objects.create(
            naver_product_id="expensive-four-matches",
            title="블랙 버클 워커 미들부츠",
            image_url="https://example.com/boots.jpg",
            lprice=40_000,
            category_large="신발",
            category_small="부츠",
            collected_at=timezone.now(),
        )

        result = discovery._related(item)

        self.assertEqual(
            [product["id"] for product in result],
            ["expensive-four-matches", "cheap-two-matches"],
        )

    def test_accessory_results_do_not_fill_with_bag_or_underwear(self) -> None:
        look = CuratedLook.objects.get(external_id="woman-casual-001")
        item = CuratedLookItem.objects.create(
            look=look,
            slot="액세서리",
            category_small="헤어 액세서리",
            name="블랙 퍼 머리띠",
            product_url="https://example.com/original",
            related_keyword="블랙 퍼 머리띠",
        )
        for product_id, category_large, category_small, price in (
            ("same-slot-hairband", "액세서리", "헤어 액세서리", 20_000),
            ("wrong-slot-bag", "가방", "토트백", 1_000),
            ("wrong-slot-underwear", "언더웨어/이너웨어", "팬티/드로즈", 2_000),
        ):
            NaverProduct.objects.create(
                naver_product_id=product_id,
                title="블랙 퍼 머리띠",
                image_url=f"https://example.com/{product_id}.jpg",
                lprice=price,
                category_large=category_large,
                category_small=category_small,
                collected_at=timezone.now(),
            )

        result = discovery._related(item)

        self.assertEqual([product["id"] for product in result], ["same-slot-hairband"])
        self.assertEqual(len(result), 1, "후보가 부족해도 다른 슬롯으로 채우면 안 된다.")

    def test_related_products_are_hidden_until_small_category_is_reviewed(self) -> None:
        look = CuratedLook.objects.get(external_id="woman-casual-001")
        item = CuratedLookItem.objects.create(
            look=look,
            slot="아우터",
            category_small="",
            name="브라운 가디건",
            product_url="https://example.com/original",
            related_keyword="브라운 니트 가디건",
        )
        NaverProduct.objects.create(
            naver_product_id="unreviewed-cardigan",
            title="브라운 니트 가디건",
            image_url="https://example.com/cardigan.jpg",
            link="https://example.com/cardigan",
            lprice=30_000,
            category_large="아우터",
            category_small="가디건",
            collected_at=timezone.now(),
        )

        self.assertEqual(discovery._related(item), [])

    def test_related_products_require_exact_reviewed_small_category(self) -> None:
        look = CuratedLook.objects.get(external_id="woman-casual-001")
        item = CuratedLookItem.objects.create(
            look=look,
            slot="아우터",
            category_small="가디건",
            name="블랙 니트 가디건",
            product_url="https://example.com/original",
            related_keyword="블랙 니트 가디건",
        )
        for product_id, category_small in (
            ("matching-cardigan", "가디건"),
            ("wrong-padding", "패딩"),
        ):
            NaverProduct.objects.create(
                naver_product_id=product_id,
                title="블랙 니트 가디건",
                image_url=f"https://example.com/{product_id}.jpg",
                link=f"https://example.com/{product_id}",
                lprice=30_000,
                category_large="아우터",
                category_small=category_small,
                collected_at=timezone.now(),
            )

        self.assertEqual(
            [product["id"] for product in discovery._related(item)],
            ["matching-cardigan"],
        )

    def test_original_product_and_feed_duplicates_are_excluded(self) -> None:
        woman_look = CuratedLook.objects.get(external_id="woman-casual-001")
        man_look = CuratedLook.objects.get(external_id="man-casual-001")
        for look in (woman_look, man_look):
            CuratedLookItem.objects.create(
                look=look,
                slot="아우터",
                category_small="가디건",
                name="블랙 니트 가디건",
                product_url="https://example.com/original",
                related_keyword="블랙 니트 가디건",
            )
        NaverProduct.objects.create(
            naver_product_id="original-product",
            title="블랙 니트 가디건",
            image_url="https://example.com/original.jpg",
            link="https://example.com/original",
            lprice=20_000,
            category_large="아우터",
            category_small="가디건",
            collected_at=timezone.now(),
        )
        NaverProduct.objects.create(
            naver_product_id="shared-related-product",
            title="블랙 니트 가디건",
            image_url="https://example.com/shared.jpg",
            link="https://example.com/shared",
            lprice=30_000,
            category_large="아우터",
            category_small="가디건",
            collected_at=timezone.now(),
        )

        result = discovery.list_looks(discovery.DiscoveryQuery(limit=50))
        related_ids = [
            product["id"]
            for look in result["results"]
            for item in look["items"]
            for product in item["similar_products"]
        ]

        self.assertNotIn("original-product", related_ids)
        self.assertEqual(related_ids.count("shared-related-product"), 1)
