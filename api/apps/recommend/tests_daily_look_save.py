"""오늘의 룩 '저장' — 골든 코디를 룩북에 담는 경로.

사진 룩북과 달리 S3도 GPU 파이프라인도 타지 않는다. 그래서 여기서 어긋나면
사용자가 겪는 것은 "느리다"가 아니라 다음 넷 중 하나다.

- 담았는데 룩북에 안 보인다 (표지 키·버킷이 어긋남)
- 한 번 눌렀는데 두 개가 담긴다 (멱등 깨짐)
- 남의 코디가 담긴다 (골든 id를 클라이언트가 정할 수 있음)
- 아직 만들어지지도 않은 룩이 담긴다 (상태 검사 누락)
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.lookbook.contracts import LookbookLinkType, LookbookSourceType
from apps.lookbook.models import LookbookPost
from apps.recommend.models import DailyLook
from apps.recommend.services import daily_look as daily_look_service
from apps.recommend.services import daily_look_save

User = get_user_model()

GOLDEN_BUCKET = "skn28-cozy3"

RESULT = {
    "golden_id": "095",
    "headline": "28도, 오늘은 이렇게",
    "rationale_ko": "직사각형 체형 기준으로 골랐어요.",
    "tags": ["출근", "미니멀"],
    "generated_by": "llm",
    "render_image": {
        "s3_bucket": GOLDEN_BUCKET,
        "s3_key": "goldenset/derived/v1/095/render_frontal_women.jpg",
    },
    "outfit_image": {
        "s3_bucket": GOLDEN_BUCKET,
        "s3_key": "goldenset/source/095.PNG",
    },
    "items": [
        {
            "item_key": "095#000",
            "name": "화이트 셔츠",
            "category": "상의",
            "sub_category": "셔츠",
            "layer_role": "TOP",
            "color": "화이트",
            "s3_bucket": GOLDEN_BUCKET,
            "s3_key": "goldenset/derived/v1/095/item_000.png",
        },
        {
            "item_key": "095#001",
            "name": "블랙 슬랙스",
            "category": "하의",
            "sub_category": "슬랙스",
            "layer_role": "BOTTOM",
            "color": "블랙",
            "s3_bucket": GOLDEN_BUCKET,
            "s3_key": "goldenset/derived/v1/095/item_001.png",
        },
    ],
}


def _succeeded_look(user, **overrides) -> DailyLook:
    result = {**RESULT, **overrides.pop("result", {})}
    return DailyLook.objects.create(
        user=user,
        look_date=daily_look_service.today(),
        status=DailyLook.Status.SUCCEEDED,
        result=result,
        **overrides,
    )


class SaveServiceTests(TestCase):
    """서비스 계층 — 무엇이 룩북에 남는가."""

    def setUp(self):
        self.user = User.objects.create(username="save1")
        # refresh_render 는 S3 를 본다. 저장 로직 검증에 네트워크를 끌어들이지 않는다.
        patcher = patch.object(daily_look_service, "refresh_render", return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_saves_golden_look_without_touching_s3(self):
        look = _succeeded_look(self.user)

        post, created = daily_look_save.save_to_lookbook(self.user)

        self.assertTrue(created)
        self.assertEqual(post.source_type, LookbookSourceType.GOLDEN_LOOK.value)
        self.assertEqual(post.golden_id, look.result["golden_id"])
        self.assertEqual(post.status, "COMPLETED")
        # 사진 룩북과 달리 뽑을 옷도 만들 이미지도 없다 — 처리 상태를 거치지 않는다.
        self.assertIsNone(post.wardrobe_upload_job_id)

    def test_cover_points_at_golden_bucket(self):
        """표지는 골든 버킷을 **가리킨다**. 복사하면 사용자 수만큼 사진이 늘어난다."""
        _succeeded_look(self.user)

        post, _ = daily_look_save.save_to_lookbook(self.user)

        self.assertEqual(post.image_s3_bucket, GOLDEN_BUCKET)
        self.assertEqual(post.image_s3_key, RESULT["render_image"]["s3_key"])

    def test_items_are_snapshots_not_wardrobe_items(self):
        """골든셋 옷은 사용자가 가진 옷이 아니다 — 옷장에 넣지 않는다."""
        _succeeded_look(self.user)

        post, _ = daily_look_save.save_to_lookbook(self.user)

        links = list(post.wardrobe_links.all())
        self.assertEqual(len(links), 2)
        self.assertTrue(all(link.wardrobe_item_id is None for link in links))
        self.assertTrue(
            all(link.link_type == LookbookLinkType.GOLDEN.value for link in links)
        )
        self.assertEqual(links[0].snapshot["item_key"], "095#000")
        # 옷장 스냅샷과 같은 키 이름을 써야 룩북 상세가 출처별 분기를 안 만든다.
        self.assertEqual(links[0].snapshot["item_name"], "화이트 셔츠")
        self.assertEqual(links[0].snapshot["s3_bucket"], GOLDEN_BUCKET)
        # 순서가 뒤집히면 룩 상세의 구성이 상의-하의가 아니라 하의-상의로 선다.
        self.assertEqual([link.sort_order for link in links], [0, 1])

    def test_tags_come_from_the_look_not_rebuilt(self):
        """오늘의 룩이 이미 룩북 어휘로 만들어 둔 태그를 그대로 쓴다."""
        _succeeded_look(self.user)

        post, _ = daily_look_save.save_to_lookbook(self.user)

        self.assertEqual(post.hashtags, ["출근", "미니멀"])
        self.assertEqual(post.schedule, RESULT["headline"])

    def test_falls_back_to_outfit_image_then_first_item(self):
        """착용 이미지가 아직 없어도 담을 수 있어야 한다 — 홈 카드와 같은 우선순위."""
        _succeeded_look(self.user, result={"render_image": None})

        post, _ = daily_look_save.save_to_lookbook(self.user)

        self.assertEqual(post.image_s3_key, RESULT["outfit_image"]["s3_key"])

        post.delete()
        DailyLook.objects.all().delete()
        _succeeded_look(self.user, result={"render_image": None, "outfit_image": None})

        post, _ = daily_look_save.save_to_lookbook(self.user)

        self.assertEqual(post.image_s3_key, RESULT["items"][0]["s3_key"])

    def test_second_save_returns_the_same_lookbook(self):
        """한 번 더 눌러도 두 개가 담기지 않는다."""
        _succeeded_look(self.user)

        first, created_first = daily_look_save.save_to_lookbook(self.user)
        second, created_second = daily_look_save.save_to_lookbook(self.user)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(LookbookPost.objects.filter(user=self.user).count(), 1)

    def test_unique_is_per_user_not_global(self):
        """같은 코디를 추천받은 다른 사용자도 각자 담을 수 있어야 한다."""
        other = User.objects.create(username="save2")
        _succeeded_look(self.user)
        _succeeded_look(other)

        daily_look_save.save_to_lookbook(self.user)
        _, created = daily_look_save.save_to_lookbook(other)

        self.assertTrue(created)
        self.assertEqual(LookbookPost.objects.filter(golden_id="095").count(), 2)

    def test_pending_look_is_not_savable(self):
        DailyLook.objects.create(
            user=self.user,
            look_date=daily_look_service.today(),
            status=DailyLook.Status.QUEUED,
        )

        with self.assertRaises(daily_look_save.DailyLookNotSavableError) as ctx:
            daily_look_save.save_to_lookbook(self.user)

        self.assertEqual(ctx.exception.status, DailyLook.Status.QUEUED)

    def test_missing_look_is_not_savable(self):
        with self.assertRaises(daily_look_save.DailyLookNotSavableError) as ctx:
            daily_look_save.save_to_lookbook(self.user)

        self.assertEqual(ctx.exception.status, "MISSING")

    def test_succeeded_without_golden_id_is_refused(self):
        """결과 JSON 이 깨졌다면 담아 봐야 어느 코디인지 되짚을 수 없다."""
        _succeeded_look(self.user, result={"golden_id": ""})

        with self.assertRaises(daily_look_save.DailyLookNotSavableError):
            daily_look_save.save_to_lookbook(self.user)

        self.assertEqual(LookbookPost.objects.count(), 0)

    def test_render_refresh_failure_does_not_block_saving(self):
        """표지 보정은 부가 단계다 — 실패해도 저장은 성립한다."""
        _succeeded_look(self.user)

        with patch.object(
            daily_look_service, "refresh_render", side_effect=RuntimeError("s3 down")
        ):
            post, created = daily_look_save.save_to_lookbook(self.user)

        self.assertTrue(created)
        self.assertEqual(post.golden_id, "095")


class SaveApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="save-api")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse("recommend:daily-look-save")
        patcher = patch.object(daily_look_service, "refresh_render", return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_first_save_returns_201_with_lookbook(self):
        _succeeded_look(self.user)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["created"])
        # 저장 직후 룩북 화면으로 이동하는 흐름이라, 목록을 다시 부르지 않고
        # 이 응답만으로 카드를 그릴 수 있어야 한다.
        self.assertEqual(body["lookbook"]["golden_id"], "095")
        self.assertEqual(body["lookbook"]["source_type"], "GOLDEN_LOOK")
        self.assertEqual(body["lookbook"]["hashtags"], ["출근", "미니멀"])
        self.assertEqual(len(body["lookbook"]["wardrobe_items"]), 2)

    def test_second_save_returns_200_and_created_false(self):
        _succeeded_look(self.user)
        self.client.post(self.url)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])

    def test_pending_look_returns_409_with_status(self):
        """프론트가 '잠시 뒤 다시'와 '담을 추천이 없다'를 갈라야 한다."""
        DailyLook.objects.create(
            user=self.user,
            look_date=daily_look_service.today(),
            status=DailyLook.Status.PROCESSING,
        )

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "PROCESSING")

    def test_missing_look_returns_409(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "MISSING")

    def test_requires_authentication(self):
        self.assertEqual(APIClient().post(self.url).status_code, 401)

    def test_image_url_is_signed_in_the_golden_bucket(self):
        """룩북 버킷으로 서명하면 담아 둔 룩이 전부 깨진 이미지가 된다."""
        _succeeded_look(self.user)

        with patch(
            "apps.lookbook.serializers.storage.presigned_get_in",
            return_value="https://signed",
        ) as presign:
            body = self.client.post(self.url).json()

        self.assertEqual(body["lookbook"]["image_url"], "https://signed")
        presign.assert_any_call(GOLDEN_BUCKET, RESULT["render_image"]["s3_key"])

    def test_presign_failure_does_not_break_the_response(self):
        """이미지 하나가 저장 응답 전체를 500으로 만들면 안 된다."""
        _succeeded_look(self.user)

        with patch(
            "apps.lookbook.serializers.storage.presigned_get_in",
            side_effect=RuntimeError("no credentials"),
        ):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["lookbook"]["image_url"], "")
