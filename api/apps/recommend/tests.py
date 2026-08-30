import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import redis
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from pillow_heif import from_pillow
from rest_framework.test import APIClient

from apps.recommend.models import OutfitAnalysis
from apps.recommend.services import analysis as analysis_service
from apps.recommend.services import claim as claim_service
from apps.wardrobe.models import WardrobeItem, WardrobeUploadJob
from apps.recommend.services import gemini, imaging, qdrant
from apps.recommend.services.outfit_context import build_analysis_context

EVALUATION = {
    "overall_score": 88,
    "summary": "색상 조화가 안정적이고 세련된 코디입니다.",
    "strengths": ["색상 조화가 좋습니다.", "실루엣이 깔끔합니다."],
    "weather_comment": "현재 기온에 잘 어울립니다.",
    "personalization_comment": "개인 정보 없이도 조화로운 인상입니다.",
    "styling_tips": ["현재 장점을 살려 액세서리를 더해보세요."],
}
RAW_RESPONSE = {
    "candidates": [{"content": {"parts": [{"text": json.dumps(EVALUATION)}]}}],
    "usageMetadata": {"totalTokenCount": 1234},
}
GEMINI_RESULT = gemini.GeminiResult(
    evaluation=EVALUATION,
    response_payload=RAW_RESPONSE,
    model="gemini-3.5-flash",
    latency_ms=321,
)
OBSERVED_AT = datetime(2026, 7, 15, 14, 0, tzinfo=dt_timezone.utc)
# weather-collector가 도는 서버에서는 observed_at이 datetime으로 채워진다.
# 실황이 없는 로컬(None)만 검증하면 JSON 직렬화 회귀를 놓친다.
RAW_WEATHER = {
    "region": "서울특별시 종로구",
    "temperature": 24.0,
    "sky_state": "맑음",
    "is_stale": False,
    "observed_at": OBSERVED_AT,
}
WEATHER = {**RAW_WEATHER, "observed_at": OBSERVED_AT.isoformat()}
CONTEXT = {
    "weather": WEATHER,
    "pursuit": None,
    "body": None,
    "personalized": False,
}


# 단색 이미지는 JPEG가 극단적으로 잘 압축해 축소 효과를 관찰할 수 없다.
# Image.effect_noise는 호출마다 결과가 달라져 크기 비교가 불안정하므로,
# 결정적인 노이즈 타일을 만들어 채운다.
_NOISE_TILE_PX = 64
_NOISE_TILE = Image.frombytes(
    "RGB",
    (_NOISE_TILE_PX, _NOISE_TILE_PX),
    bytes(
        (x * 37 + y * 97 + channel * 53) % 256
        for y in range(_NOISE_TILE_PX)
        for x in range(_NOISE_TILE_PX)
        for channel in range(3)
    ),
)


def make_image(size: tuple[int, int] = (2, 2)) -> Image.Image:
    image = Image.new("RGB", size)
    for top in range(0, size[1], _NOISE_TILE_PX):
        for left in range(0, size[0], _NOISE_TILE_PX):
            image.paste(_NOISE_TILE, (left, top))
    return image


def make_image_bytes(size: tuple[int, int] = (2, 2)) -> bytes:
    buffer = BytesIO()
    make_image(size).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def make_image_file(
    name: str = "outfit.jpg", size: tuple[int, int] = (2, 2)
) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, make_image_bytes(size), content_type="image/jpeg")


def make_heic_file(name: str = "portrait.heic") -> SimpleUploadedFile:
    buffer = BytesIO()
    from_pillow(make_image((32, 48))).save(buffer, quality=90)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/heic")



def image_part_size(analysis: OutfitAnalysis) -> int:
    """자리표시자에 박아 둔 바이트 수를 되읽어 전송 크기를 확인한다."""
    data = analysis.request_payload["contents"][0]["parts"][1]["inlineData"]["data"]
    return int(data.split(":")[1].strip().split(" ")[0])


@patch("apps.recommend.services.analysis.storage.is_configured", return_value=True)
@patch("apps.recommend.services.analysis.storage.upload_fileobj")
@patch("apps.recommend.services.analysis.queue.enqueue")
class OutfitAnalysisAcceptTests(TestCase):
    """접수 API — 202만 돌려주고 Gemini는 부르지 않는다.

    S3와 큐는 클래스 단위로 mock한다 (전부 필수 경로라 매번 정상 동작이 기본값).
    """

    def setUp(self) -> None:
        self.client = APIClient()
        self.url = reverse("recommend:outfit-analysis")

    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_accepts_without_authentication(
        self,
        mock_context: Mock,
        mock_enqueue: Mock,
        mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        response = self.client.post(
            self.url,
            {"image": make_image_file(), "lat": 37.5, "lon": 127.0},
            format="multipart",
        )

        self.assertEqual(response.status_code, 202)
        analysis = OutfitAnalysis.objects.get()
        self.assertEqual(response.data["analysis_id"], analysis.pk)
        self.assertEqual(response.data["status"], OutfitAnalysis.Status.QUEUED)
        self.assertEqual(
            response.data["poll_url"], f"/api/v1/outfits/analyses/{analysis.pk}/"
        )
        self.assertEqual(response.data["poll_after_ms"], 2000)
        self.assertEqual(response.data["estimated_seconds"], 30)
        self.assertIsNone(response.data["wardrobe_job_id"])  # 기본은 옷장 미연계
        mock_context.assert_called_once()
        mock_upload.assert_called_once()
        mock_enqueue.assert_called_once()

    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_normalizes_iphone_heic_before_storage(
        self,
        _mock_context: Mock,
        _mock_enqueue: Mock,
        mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        response = self.client.post(
            self.url,
            {"image": make_heic_file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 202)
        analysis = OutfitAnalysis.objects.get()
        self.assertEqual(analysis.image_content_type, "image/jpeg")
        self.assertTrue(analysis.image_s3_key.endswith("/original.jpg"))
        uploaded, key, content_type = mock_upload.call_args.args
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(key, analysis.image_s3_key)
        self.assertEqual(uploaded.read(2), b"\xff\xd8")

    @patch("apps.recommend.services.analysis.gemini.evaluate_outfit")
    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_does_not_call_llm_during_accept(
        self,
        _mock_context: Mock,
        mock_evaluate: Mock,
        _mock_enqueue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        """비동기화의 핵심 — 요청 스레드가 Gemini를 기다리지 않는다."""
        self.client.post(self.url, {"image": make_image_file()}, format="multipart")

        mock_evaluate.assert_not_called()

    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_freezes_query_context_at_accept_time(
        self,
        _mock_context: Mock,
        _mock_enqueue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        """큐에서 대기하는 사이 날씨가 바뀌어도 되도록 스냅샷을 굳혀 둔다."""
        self.client.post(
            self.url,
            {"image": make_image_file(), "lat": 37.5, "lon": 127.0},
            format="multipart",
        )

        analysis = OutfitAnalysis.objects.get()
        self.assertEqual(analysis.weather, WEATHER)
        self.assertIsNone(analysis.body)
        self.assertIsNone(analysis.pursuit)
        self.assertFalse(analysis.personalized)
        self.assertEqual(analysis.requested_lat, 37.5)
        self.assertEqual(analysis.resolved_lat, 37.5)
        self.assertEqual(analysis.image_bytes, len(make_image_bytes()))
        self.assertEqual(analysis.attempts, 0)
        self.assertIsNone(analysis.started_at)

    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_uploads_original_and_enqueues_its_key(
        self,
        _mock_context: Mock,
        mock_enqueue: Mock,
        mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        self.client.post(self.url, {"image": make_image_file()}, format="multipart")

        analysis = OutfitAnalysis.objects.get()
        self.assertEqual(
            analysis.image_s3_key, f"outfits/anonymous/{analysis.pk}/original.jpg"
        )
        self.assertEqual(mock_upload.call_args.args[1], analysis.image_s3_key)
        self.assertEqual(mock_enqueue.call_args.args[0].pk, analysis.pk)

    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_records_logged_in_user(
        self,
        _mock_context: Mock,
        _mock_enqueue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        user = get_user_model().objects.create(username="naver_1")
        self.client.force_authenticate(user=user)

        self.client.post(self.url, {"image": make_image_file()}, format="multipart")

        analysis = OutfitAnalysis.objects.get()
        self.assertEqual(analysis.user, user)
        self.assertEqual(
            analysis.image_s3_key, f"outfits/{user.pk}/{analysis.pk}/original.jpg"
        )

    def test_rejects_request_without_image(self, *_mocks: Mock) -> None:
        response = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.data)
        self.assertEqual(OutfitAnalysis.objects.count(), 0)

    def test_rejects_only_one_coordinate(self, *_mocks: Mock) -> None:
        response = self.client.post(
            self.url,
            {"image": make_image_file(), "lat": 37.5},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("non_field_errors", response.data)

    def test_rejects_non_image_file(self, *_mocks: Mock) -> None:
        response = self.client.post(
            self.url,
            {
                "image": SimpleUploadedFile(
                    "outfit.txt", b"not an image", content_type="text/plain"
                )
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.data)

    def test_rejects_json_request(self, *_mocks: Mock) -> None:
        response = self.client.post(self.url, {"image": "value"}, format="json")
        self.assertEqual(response.status_code, 415)


@patch("apps.recommend.services.analysis.storage.is_configured", return_value=True)
@patch("apps.recommend.services.analysis.storage.upload_fileobj")
@patch("apps.recommend.services.analysis.queue.enqueue")
@patch("apps.recommend.services.wardrobe_link.wardrobe_jobs.enqueue")
@patch(
    "apps.recommend.services.analysis.build_analysis_context",
    return_value=CONTEXT,
)
class SaveToWardrobeTests(TestCase):
    """save_to_wardrobe — 로그인 사용자만, 사진 재업로드 없이 옷장 파이프라인에 연결."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.url = reverse("recommend:outfit-analysis")
        self.user = get_user_model().objects.create(username="naver_1")

    def _post(self, **extra) -> object:
        return self.client.post(
            self.url, {"image": make_image_file(), **extra}, format="multipart"
        )

    def test_creates_wardrobe_job_for_logged_in_user(
        self,
        _mock_context: Mock,
        mock_wardrobe_enqueue: Mock,
        _mock_queue: Mock,
        mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        self.client.force_authenticate(user=self.user)

        response = self._post(save_to_wardrobe=True)

        self.assertEqual(response.status_code, 202)
        analysis = OutfitAnalysis.objects.get()
        job = WardrobeUploadJob.objects.get()
        self.assertTrue(analysis.save_to_wardrobe)
        self.assertEqual(analysis.wardrobe_job, job)
        self.assertEqual(response.data["wardrobe_job_id"], job.pk)
        self.assertEqual(job.user, self.user)
        self.assertEqual(job.status, WardrobeUploadJob.Status.PENDING)
        mock_wardrobe_enqueue.assert_called_once()
        # 사진은 한 번만 올린다 — 같은 사진을 두 번 올리면 S3 비용만 두 배다
        mock_upload.assert_called_once()
        self.assertEqual(job.source_s3_key, analysis.image_s3_key)

    def test_wardrobe_job_reuses_outfit_photo_with_explicit_bucket(
        self,
        _mock_context: Mock,
        mock_wardrobe_enqueue: Mock,
        _mock_queue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        """원본이 코디 평가 버킷에 있으므로 큐 페이로드에 버킷을 명시해야 한다."""
        self.client.force_authenticate(user=self.user)

        with patch(
            "apps.recommend.services.wardrobe_link.storage.bucket",
            return_value="outfit-bucket",
        ):
            self._post(save_to_wardrobe=True)

        self.assertEqual(
            mock_wardrobe_enqueue.call_args.kwargs["source_bucket"], "outfit-bucket"
        )

    def test_ignored_for_anonymous_request(
        self,
        _mock_context: Mock,
        mock_wardrobe_enqueue: Mock,
        _mock_queue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        """옷장은 사용자 소유 데이터라 비로그인 요청의 플래그는 조용히 버린다."""
        response = self._post(save_to_wardrobe=True)

        self.assertEqual(response.status_code, 202)
        self.assertIsNone(response.data["wardrobe_job_id"])
        self.assertFalse(OutfitAnalysis.objects.get().save_to_wardrobe)
        self.assertEqual(WardrobeUploadJob.objects.count(), 0)
        mock_wardrobe_enqueue.assert_not_called()

    def test_not_requested_creates_no_job(
        self,
        _mock_context: Mock,
        mock_wardrobe_enqueue: Mock,
        _mock_queue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        self.client.force_authenticate(user=self.user)

        self._post()

        self.assertEqual(WardrobeUploadJob.objects.count(), 0)
        mock_wardrobe_enqueue.assert_not_called()

    def test_wardrobe_queue_failure_does_not_block_evaluation(
        self,
        _mock_context: Mock,
        mock_wardrobe_enqueue: Mock,
        _mock_queue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        """곁가지(옷장)가 본류(평가)를 막지 않는다. 다만 흔적은 남긴다."""
        mock_wardrobe_enqueue.side_effect = redis.ConnectionError("redis down")
        self.client.force_authenticate(user=self.user)

        response = self._post(save_to_wardrobe=True)

        self.assertEqual(response.status_code, 202)
        analysis = OutfitAnalysis.objects.get()
        self.assertEqual(analysis.status, OutfitAnalysis.Status.QUEUED)  # 평가는 정상
        job = WardrobeUploadJob.objects.get()
        self.assertEqual(job.status, WardrobeUploadJob.Status.FAILED)
        self.assertEqual(response.data["wardrobe_job_id"], job.pk)


class OutfitAnalysisAcceptFailureTests(TestCase):
    """S3와 큐는 이제 필수 경로다 — 실패하면 접수 자체를 거절한다."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.url = reverse("recommend:outfit-analysis")

    @patch("apps.recommend.services.analysis.storage.is_configured", return_value=False)
    def test_rejects_when_bucket_not_configured(self, _mock_configured: Mock) -> None:
        """버킷이 없으면 워커가 사진을 못 읽는다 — 조용히 받아두면 영원히 분석 중이 된다."""
        response = self.client.post(
            self.url, {"image": make_image_file()}, format="multipart"
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(OutfitAnalysis.objects.count(), 0)

    @patch("apps.recommend.services.analysis.storage.is_configured", return_value=True)
    @patch(
        "apps.recommend.services.analysis.storage.upload_fileobj",
        side_effect=RuntimeError("S3 down"),
    )
    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_s3_failure_rejects_and_leaves_no_row(
        self,
        _mock_context: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        response = self.client.post(
            self.url, {"image": make_image_file()}, format="multipart"
        )

        self.assertEqual(response.status_code, 503)
        # 분석이 불가능한 기록을 남기지 않는다
        self.assertEqual(OutfitAnalysis.objects.count(), 0)

    @patch("apps.recommend.services.analysis.storage.is_configured", return_value=True)
    @patch("apps.recommend.services.analysis.storage.upload_fileobj")
    @patch(
        "apps.recommend.services.analysis.queue.enqueue",
        side_effect=redis.ConnectionError("redis down"),
    )
    @patch(
        "apps.recommend.services.analysis.build_analysis_context",
        return_value=CONTEXT,
    )
    def test_queue_failure_marks_row_failed(
        self,
        _mock_context: Mock,
        _mock_enqueue: Mock,
        _mock_upload: Mock,
        _mock_configured: Mock,
    ) -> None:
        """사진은 이미 S3에 올라갔으므로 흔적을 남긴다 (wardrobe와 같은 처리)."""
        response = self.client.post(
            self.url, {"image": make_image_file()}, format="multipart"
        )

        self.assertEqual(response.status_code, 503)
        analysis = OutfitAnalysis.objects.get()
        self.assertEqual(analysis.status, OutfitAnalysis.Status.FAILED)
        self.assertIn("큐", analysis.error_message)
        self.assertIsNotNone(analysis.finished_at)


class OutfitAnalysisPollTests(TestCase):
    """조회 API — 폴링과 결과 수신을 겸한다."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.user = get_user_model().objects.create(username="naver_1")
        self.other = get_user_model().objects.create(username="kakao_2")

    def _url(self, analysis) -> str:
        return reverse("recommend:outfit-analysis-detail", args=[analysis.pk])

    def _anonymous(self, **kwargs) -> OutfitAnalysis:
        return OutfitAnalysis.objects.create(user=None, weather=WEATHER, **kwargs)

    def test_anonymous_can_poll_own_ticket(self) -> None:
        analysis = self._anonymous(status=OutfitAnalysis.Status.PROCESSING)

        response = self.client.get(self._url(analysis))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "PROCESSING")
        self.assertIsNone(response.data["evaluation"])
        self.assertEqual(response.data["poll_after_ms"], 2000)

    def test_anonymous_receives_result_when_done(self) -> None:
        analysis = self._anonymous(
            status=OutfitAnalysis.Status.SUCCEEDED, evaluation=EVALUATION
        )

        response = self.client.get(self._url(analysis))

        self.assertEqual(response.data["evaluation"], EVALUATION)
        self.assertEqual(response.data["context"]["weather"], WEATHER)
        self.assertIsNone(response.data["poll_after_ms"])  # 폴링 중단 신호
        self.assertIsNone(response.data["detail"])

    def test_anonymous_response_hides_private_fields(self) -> None:
        """UUID는 URL·로그로 샐 수 있다 — 사진과 체형은 익명 응답에 싣지 않는다."""
        analysis = self._anonymous(
            status=OutfitAnalysis.Status.SUCCEEDED,
            evaluation=EVALUATION,
            body={"gender": "female", "height": 165},
            pursuit={"preferred": {"styles": ["minimal"]}},
            request_payload={"systemInstruction": {}},
            response_payload=RAW_RESPONSE,
            image_s3_key="outfits/anonymous/x/original.jpg",
        )

        response = self.client.get(self._url(analysis))

        for hidden in (
            "body",
            "pursuit",
            "request_payload",
            "response_payload",
            "image_url",
            "error_message",
        ):
            self.assertNotIn(hidden, response.data)
        # 개인화가 걸렸는지 여부는 안내 문구용으로 남긴다
        self.assertTrue(response.data["context"]["used_body"])

    def test_failed_analysis_returns_user_facing_message(self) -> None:
        analysis = self._anonymous(
            status=OutfitAnalysis.Status.FAILED,
            error_message="GeminiServiceError: quota exceeded",
        )

        response = self.client.get(self._url(analysis))

        self.assertEqual(response.data["status"], "FAILED")
        self.assertIn("다시 시도", response.data["detail"])
        # 내부 사유는 노출하지 않는다
        self.assertNotIn("quota", json.dumps(response.data, default=str))

    @override_settings(OUTFIT_ANON_TTL_HOURS=24)
    def test_anonymous_ticket_expires(self) -> None:
        analysis = self._anonymous(status=OutfitAnalysis.Status.SUCCEEDED)
        OutfitAnalysis.objects.filter(pk=analysis.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        response = self.client.get(self._url(analysis))

        self.assertEqual(response.status_code, 404)

    def test_logged_in_record_needs_token(self) -> None:
        analysis = OutfitAnalysis.objects.create(user=self.user)

        # UUID를 알아도 익명으로는 못 본다
        self.assertEqual(self.client.get(self._url(analysis)).status_code, 404)

        # 남의 토큰으로도 못 본다 (403이면 그 UUID의 존재를 알려주는 셈이라 404)
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(self._url(analysis)).status_code, 404)

        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.client.get(self._url(analysis)).status_code, 200)

    def test_owner_response_includes_internals(self) -> None:
        analysis = OutfitAnalysis.objects.create(
            user=self.user,
            status=OutfitAnalysis.Status.SUCCEEDED,
            evaluation=EVALUATION,
            response_payload=RAW_RESPONSE,
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self._url(analysis))

        self.assertEqual(response.data["response_payload"], RAW_RESPONSE)
        self.assertIn("attempts", response.data)
        self.assertIn("llm_image_bytes", response.data)

    def test_unknown_id_is_404(self) -> None:
        response = self.client.get(
            reverse(
                "recommend:outfit-analysis-detail",
                args=["6b2c1f3a-9d4e-4c8b-8a71-2f0e5d9c3b17"],
            )
        )
        self.assertEqual(response.status_code, 404)


@patch(
    "apps.recommend.services.wardrobe_link.wardrobe_storage.BUCKET",
    "test-wardrobe-bucket",
)
@patch(
    "apps.recommend.services.wardrobe_link.wardrobe_storage.presigned_get",
    return_value="https://s3.example/item_01.png",
)
class OutfitAnalysisWardrobeLinkTests(TestCase):
    """상세 응답의 `wardrobe` 필드 — 옷장 등록이 끝나면 아이템 요약까지 같이 내려준다.

    옷장 파이프라인은 GPU 서버 → 콜백이라 평가가 끝나도 job은 아직 진행 중일 수 있다.
    그래서 "상태는 항상 / 아이템은 DONE일 때만"이 이 필드의 계약이다.
    """

    def setUp(self) -> None:
        self.client = APIClient()
        self.user = get_user_model().objects.create(username="naver_1")
        self.client.force_authenticate(user=self.user)

    def _analysis(self, job=None) -> OutfitAnalysis:
        return OutfitAnalysis.objects.create(
            user=self.user,
            status=OutfitAnalysis.Status.SUCCEEDED,
            evaluation=EVALUATION,
            weather=WEATHER,
            save_to_wardrobe=job is not None,
            wardrobe_job=job,
        )

    def _job(self, status=WardrobeUploadJob.Status.DONE, **kwargs) -> WardrobeUploadJob:
        return WardrobeUploadJob.objects.create(
            user=self.user,
            source_s3_key="outfits/1/abc/original.jpg",
            status=status,
            **kwargs,
        )

    def _item(self, job, **kwargs) -> WardrobeItem:
        fields = {
            "s3_key": "wardrobe/1/abc/item_01.png",
            "item_name": "화이트 옥스포드 셔츠",
            "category_large": "상의",
            "category_small": "셔츠",
            "color": "화이트",
            "season": ["봄", "가을"],
            "style": ["캐주얼"],
            "seg_meta": {"raw_label": "shirt", "score": 0.94},
            **kwargs,
        }
        return WardrobeItem.objects.create(user=self.user, job=job, **fields)

    def _get(self, analysis):
        return self.client.get(
            reverse("recommend:outfit-analysis-detail", args=[analysis.pk])
        )

    def test_done_job_includes_item_summaries(self, _presigned: Mock) -> None:
        job = self._job(finished_at=timezone.now())
        self._item(job)
        self._item(job, item_name="연청 슬림 진", category_large="하의", color="블루")

        response = self._get(self._analysis(job))

        self.assertEqual(response.status_code, 200)
        wardrobe = response.data["wardrobe"]
        self.assertEqual(wardrobe["job_id"], job.pk)
        self.assertEqual(wardrobe["status"], "DONE")
        self.assertEqual(len(wardrobe["items"]), 2)

        names = {item["item_name"] for item in wardrobe["items"]}
        self.assertEqual(names, {"화이트 옥스포드 셔츠", "연청 슬림 진"})
        self.assertEqual(
            wardrobe["items"][0]["image_url"], "https://s3.example/item_01.png"
        )

    def test_item_payload_is_summary_only(self, _presigned: Mock) -> None:
        """전체 태그는 옷장 API의 일이다 — 여기서 늘어나면 계약이 조용히 번진다."""
        job = self._job()
        self._item(job)

        item = self._get(self._analysis(job)).data["wardrobe"]["items"][0]

        self.assertEqual(
            set(item),
            {
                "id",
                "item_name",
                "category_large",
                "category_small",
                "color",
                "image_url",
                "confirmed",
            },
        )

    def test_pending_job_reports_status_without_items(self, _presigned: Mock) -> None:
        job = self._job(status=WardrobeUploadJob.Status.PROCESSING)
        # 콜백 전이라도 행이 먼저 생길 수 있다 — 상태가 DONE이 아니면 내보내지 않는다
        self._item(job)

        wardrobe = self._get(self._analysis(job)).data["wardrobe"]

        self.assertEqual(wardrobe["status"], "PROCESSING")
        self.assertEqual(wardrobe["items"], [])
        self.assertIsNone(wardrobe["finished_at"])

    def test_failed_job_exposes_error_message(self, _presigned: Mock) -> None:
        job = self._job(
            status=WardrobeUploadJob.Status.FAILED,
            error_message="처리 큐 적재 실패",
            finished_at=timezone.now(),
        )

        wardrobe = self._get(self._analysis(job)).data["wardrobe"]

        self.assertEqual(wardrobe["status"], "FAILED")
        self.assertEqual(wardrobe["error_message"], "처리 큐 적재 실패")
        self.assertEqual(wardrobe["items"], [])

    def test_unlinked_analysis_returns_null(self, _presigned: Mock) -> None:
        response = self._get(self._analysis())

        self.assertIsNone(response.data["wardrobe"])

    def test_presigned_failure_degrades_to_null_url(self, presigned: Mock) -> None:
        """URL 발급 장애가 평가 조회 자체를 막으면 안 된다."""
        presigned.side_effect = RuntimeError("s3 down")
        job = self._job()
        self._item(job)

        response = self._get(self._analysis(job))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["wardrobe"]["items"][0]["image_url"])

    def test_anonymous_response_omits_wardrobe(self, _presigned: Mock) -> None:
        """옷장은 사용자 소유 데이터라 익명 축소 응답에는 아예 들어가지 않는다."""
        analysis = OutfitAnalysis.objects.create(
            user=None, status=OutfitAnalysis.Status.SUCCEEDED, weather=WEATHER
        )
        client = APIClient()  # 인증 없이

        response = client.get(
            reverse("recommend:outfit-analysis-detail", args=[analysis.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("wardrobe", response.data)


class OutfitAnalysisHistoryTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.user = get_user_model().objects.create(username="naver_1")
        self.other = get_user_model().objects.create(username="kakao_2")
        self.mine = OutfitAnalysis.objects.create(
            user=self.user,
            status=OutfitAnalysis.Status.SUCCEEDED,
            weather=WEATHER,
            evaluation=EVALUATION,
            personalized=True,
        )
        OutfitAnalysis.objects.create(
            user=self.other, status=OutfitAnalysis.Status.SUCCEEDED
        )
        OutfitAnalysis.objects.create(
            user=None, status=OutfitAnalysis.Status.SUCCEEDED
        )

    def test_list_requires_authentication(self) -> None:
        response = self.client.get(reverse("recommend:outfit-analysis-list"))
        self.assertEqual(response.status_code, 401)

    def test_list_returns_only_my_analyses(self) -> None:
        self.client.force_authenticate(user=self.user)

        response = self.client.get(reverse("recommend:outfit-analysis-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        item = response.data["results"][0]
        self.assertEqual(str(item["id"]), str(self.mine.pk))
        self.assertEqual(item["overall_score"], 88)
        self.assertEqual(item["summary"], EVALUATION["summary"])

    def test_list_filters_by_status(self) -> None:
        OutfitAnalysis.objects.create(
            user=self.user, status=OutfitAnalysis.Status.FAILED
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            reverse("recommend:outfit-analysis-list"), {"status": "failed"}
        )

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["status"], "FAILED")


@patch("apps.recommend.services.analysis.storage.download",
        return_value=make_image_bytes((1600, 1200)),)
class WorkerTests(TestCase):
    """워커 서비스 계층 — claim 멱등성과 결과 기록."""

    def setUp(self) -> None:
        self.analysis = OutfitAnalysis.objects.create(
            user=None,
            status=OutfitAnalysis.Status.QUEUED,
            weather=WEATHER,
            image_s3_key="outfits/anonymous/x/original.jpg",
            image_content_type="image/jpeg",
        )

    def test_claim_marks_processing(self, _mock_download: Mock) -> None:
        claimed = analysis_service.claim(str(self.analysis.pk))

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, OutfitAnalysis.Status.PROCESSING)
        self.assertEqual(claimed.attempts, 1)
        self.assertIsNotNone(claimed.started_at)

    def test_claim_skips_already_succeeded(self, _mock_download: Mock) -> None:
        """중복 배달·재시도가 완료된 평가를 다시 돌리면 Gemini 비용이 두 번 나간다."""
        self.analysis.status = OutfitAnalysis.Status.SUCCEEDED
        self.analysis.save(update_fields=["status"])

        self.assertIsNone(analysis_service.claim(str(self.analysis.pk)))

    def test_claim_returns_none_for_missing_row(self, _mock_download: Mock) -> None:
        self.assertIsNone(
            analysis_service.claim("6b2c1f3a-9d4e-4c8b-8a71-2f0e5d9c3b17")
        )

    def test_claim_retries_processing_row(self, _mock_download: Mock) -> None:
        """워커가 죽어 PROCESSING에 남은 행은 다시 집을 수 있어야 한다."""
        analysis_service.claim(str(self.analysis.pk))

        claimed = analysis_service.claim(str(self.analysis.pk))

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.attempts, 2)

    @patch(
        "apps.recommend.services.analysis.gemini.evaluate_outfit",
        return_value=GEMINI_RESULT,
    )
    def test_run_records_result_and_uses_frozen_context(
        self, mock_evaluate: Mock, _mock_download: Mock
    ) -> None:
        self.analysis.pursuit = {"preferred": {"styles": ["minimal"]}}
        self.analysis.save(update_fields=["pursuit"])
        claimed = analysis_service.claim(str(self.analysis.pk))

        analysis_service.run_analysis(claimed)

        # 컨텍스트를 새로 만들지 않고 접수 시점 스냅샷을 쓴다
        sent_context = mock_evaluate.call_args.kwargs["context"]
        self.assertEqual(sent_context["weather"], WEATHER)
        self.assertEqual(sent_context["pursuit"], {"preferred": {"styles": ["minimal"]}})

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, OutfitAnalysis.Status.SUCCEEDED)
        self.assertEqual(claimed.evaluation, EVALUATION)
        self.assertEqual(claimed.response_payload, RAW_RESPONSE)
        self.assertEqual(claimed.latency_ms, 321)
        self.assertIsNotNone(claimed.llm_image_bytes)
        self.assertIsNotNone(claimed.finished_at)

    @patch(
        "apps.recommend.services.analysis.gemini.evaluate_outfit",
        side_effect=gemini.GeminiServiceError(
            "타임아웃", response_payload={"error": {"code": 504}}
        ),
    )
    def test_run_records_query_even_on_failure(
        self, _mock_evaluate: Mock, _mock_download: Mock
    ) -> None:
        """재시도 판단과 사후 분석을 위해 무엇을 보냈는지는 남긴다."""
        claimed = analysis_service.claim(str(self.analysis.pk))

        with self.assertRaises(gemini.GeminiServiceError):
            analysis_service.run_analysis(claimed)

        claimed.refresh_from_db()
        # 아직 FAILED가 아니다 — 재시도 여지가 있으므로 워커 커맨드가 결정한다
        self.assertEqual(claimed.status, OutfitAnalysis.Status.PROCESSING)
        self.assertIn("systemInstruction", claimed.request_payload)
        self.assertEqual(claimed.response_payload, {"error": {"code": 504}})

    def test_mark_failed(self, _mock_download: Mock) -> None:
        analysis_service.mark_failed(self.analysis, "GeminiServiceError: 타임아웃")

        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.status, OutfitAnalysis.Status.FAILED)
        self.assertIn("타임아웃", self.analysis.error_message)
        self.assertIsNotNone(self.analysis.finished_at)


class SweepStaleTests(TestCase):
    """워커가 죽으면 프론트가 영원히 폴링한다 — 방치된 행을 실패로 정리한다."""

    def _aged(self, status: str, *, minutes: int, started: bool) -> OutfitAnalysis:
        analysis = OutfitAnalysis.objects.create(user=None, status=status)
        moment = timezone.now() - timedelta(minutes=minutes)
        OutfitAnalysis.objects.filter(pk=analysis.pk).update(
            created_at=moment, started_at=moment if started else None
        )
        return analysis

    def test_sweeps_stuck_processing_and_queued(self) -> None:
        stuck_processing = self._aged(
            OutfitAnalysis.Status.PROCESSING, minutes=10, started=True
        )
        forgotten_queued = self._aged(
            OutfitAnalysis.Status.QUEUED, minutes=10, started=False
        )
        recent = self._aged(OutfitAnalysis.Status.QUEUED, minutes=1, started=False)
        done = OutfitAnalysis.objects.create(
            user=None, status=OutfitAnalysis.Status.SUCCEEDED
        )

        swept = analysis_service.sweep_stale(minutes=5)

        self.assertEqual(swept, 2)
        for analysis in (stuck_processing, forgotten_queued):
            analysis.refresh_from_db()
            self.assertEqual(analysis.status, OutfitAnalysis.Status.FAILED)
            self.assertIsNotNone(analysis.finished_at)
        recent.refresh_from_db()
        self.assertEqual(recent.status, OutfitAnalysis.Status.QUEUED)
        done.refresh_from_db()
        self.assertEqual(done.status, OutfitAnalysis.Status.SUCCEEDED)

    def test_command_dry_run_changes_nothing(self) -> None:
        self._aged(OutfitAnalysis.Status.QUEUED, minutes=10, started=False)

        call_command("sweep_stale_analyses", "--minutes", "5", "--dry-run", stdout=StringIO())

        self.assertEqual(
            OutfitAnalysis.objects.filter(
                status=OutfitAnalysis.Status.QUEUED
            ).count(),
            1,
        )

    def test_command_sweeps(self) -> None:
        self._aged(OutfitAnalysis.Status.QUEUED, minutes=10, started=False)

        call_command("sweep_stale_analyses", "--minutes", "5", stdout=StringIO())

        self.assertEqual(
            OutfitAnalysis.objects.get().status, OutfitAnalysis.Status.FAILED
        )


class WorkerCommandTests(TestCase):
    """run_outfit_worker — 큐 상호작용(ack / 재시도 / dead)."""

    def setUp(self) -> None:
        self.analysis = OutfitAnalysis.objects.create(
            user=None,
            status=OutfitAnalysis.Status.QUEUED,
            weather=WEATHER,
            image_s3_key="outfits/anonymous/x/original.jpg",
            image_content_type="image/jpeg",
        )
        self.raw = json.dumps({"analysis_id": str(self.analysis.pk)})

    def _run_once(self) -> None:
        call_command("run_outfit_worker", "--once", stdout=StringIO())

    @patch("apps.recommend.management.commands.run_outfit_worker.queue")
    @patch("apps.recommend.services.analysis.storage.download",
        return_value=make_image_bytes((1600, 1200)),)
    @patch(
        "apps.recommend.services.analysis.gemini.evaluate_outfit",
        return_value=GEMINI_RESULT,
    )
    def test_processes_and_acks(
        self, _mock_evaluate: Mock, _mock_download: Mock, mock_queue: Mock
    ) -> None:
        mock_queue.fetch.return_value = self.raw

        self._run_once()

        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.status, OutfitAnalysis.Status.SUCCEEDED)
        mock_queue.ack.assert_called_once()
        mock_queue.retry_or_dead.assert_not_called()

    @patch("apps.recommend.management.commands.run_outfit_worker.queue")
    @patch("apps.recommend.services.analysis.storage.download",
        return_value=make_image_bytes((1600, 1200)),)
    @patch(
        "apps.recommend.services.analysis.gemini.evaluate_outfit",
        side_effect=gemini.GeminiServiceError("타임아웃"),
    )
    def test_retry_keeps_row_pending(
        self, _mock_evaluate: Mock, _mock_download: Mock, mock_queue: Mock
    ) -> None:
        mock_queue.fetch.return_value = self.raw
        mock_queue.retry_or_dead.return_value = False  # 아직 재시도 여지가 있다

        self._run_once()

        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.status, OutfitAnalysis.Status.PROCESSING)
        mock_queue.ack.assert_not_called()

    @patch("apps.recommend.management.commands.run_outfit_worker.queue")
    @patch("apps.recommend.services.analysis.storage.download",
        return_value=make_image_bytes((1600, 1200)),)
    @patch(
        "apps.recommend.services.analysis.gemini.evaluate_outfit",
        side_effect=gemini.GeminiServiceError("타임아웃"),
    )
    def test_dead_queue_marks_row_failed(
        self, _mock_evaluate: Mock, _mock_download: Mock, mock_queue: Mock
    ) -> None:
        mock_queue.fetch.return_value = self.raw
        mock_queue.retry_or_dead.return_value = True  # 재시도 한도 초과

        self._run_once()

        self.analysis.refresh_from_db()
        self.assertEqual(self.analysis.status, OutfitAnalysis.Status.FAILED)
        self.assertIn("GeminiServiceError", self.analysis.error_message)

    @patch("apps.recommend.management.commands.run_outfit_worker.queue")
    @patch(
        "apps.recommend.services.analysis.gemini.evaluate_outfit",
        return_value=GEMINI_RESULT,
    )
    def test_duplicate_delivery_is_acked_without_llm_call(
        self, mock_evaluate: Mock, mock_queue: Mock
    ) -> None:
        self.analysis.status = OutfitAnalysis.Status.SUCCEEDED
        self.analysis.save(update_fields=["status"])
        mock_queue.fetch.return_value = self.raw

        self._run_once()

        mock_evaluate.assert_not_called()
        mock_queue.ack.assert_called_once()

    @patch("apps.recommend.management.commands.run_outfit_worker.queue")
    def test_unparsable_payload_is_discarded(self, mock_queue: Mock) -> None:
        """재시도해도 결과가 같은 페이로드로 큐를 막지 않는다."""
        mock_queue.fetch.return_value = "{not json"

        self._run_once()

        mock_queue.ack.assert_called_once()
        mock_queue.retry_or_dead.assert_not_called()


@override_settings(
    GEMINI_API_KEY="test-api-key",
    GEMINI_MODEL="gemini-3.5-flash",
    GEMINI_API_BASE_URL="https://example.test",
    GEMINI_TIMEOUT_SECONDS=10,
)
class GeminiServiceTests(SimpleTestCase):
    @patch("apps.recommend.services.gemini.requests.post")
    def test_sends_image_context_and_structured_schema(self, mock_post: Mock) -> None:
        api_response = Mock()
        api_response.status_code = 200   # Mock 기본값은 int 비교가 안 된다
        api_response.raise_for_status.return_value = None
        api_response.json.return_value = RAW_RESPONSE
        mock_post.return_value = api_response

        result = gemini.evaluate_outfit(
            make_image_bytes(), mime_type="image/jpeg", context=CONTEXT
        )

        self.assertEqual(result.evaluation, EVALUATION)
        self.assertEqual(result.response_payload, RAW_RESPONSE)
        self.assertEqual(result.model, "gemini-3.5-flash")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-api-key")
        self.assertEqual(kwargs["timeout"], 10)
        parts = kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/jpeg")
        self.assertTrue(parts[1]["inlineData"]["data"])
        self.assertIn("weather", parts[0]["text"])
        generation_config = kwargs["json"]["generationConfig"]
        self.assertEqual(generation_config["responseMimeType"], "application/json")
        self.assertEqual(
            generation_config["responseSchema"], gemini.EVALUATION_SCHEMA
        )
        # Gemini Schema는 OpenAPI 서브셋이라 additionalProperties를 모른다 (400 유발)
        self.assertNotIn("additionalProperties", gemini.EVALUATION_SCHEMA)

    @patch("apps.recommend.services.gemini.requests.post")
    def test_http_error_carries_response_body(self, mock_post: Mock) -> None:
        import requests

        api_response = Mock()
        api_response.status_code = 400
        api_response.text = '{"error": "bad"}'
        api_response.json.return_value = {"error": "bad"}
        api_response.raise_for_status.side_effect = requests.HTTPError("400")
        mock_post.return_value = api_response

        with self.assertRaises(gemini.GeminiServiceError) as ctx:
            gemini.evaluate_outfit(
            make_image_bytes(), mime_type="image/jpeg", context=CONTEXT
        )

        self.assertEqual(ctx.exception.response_payload, {"error": "bad"})

    def test_build_request_payload_matches_real_body_without_base64(self) -> None:
        payload = gemini.build_request_payload(
            CONTEXT, mime_type="image/jpeg", image_bytes=1234
        )

        parts = payload["contents"][0]["parts"]
        self.assertIn("weather", parts[0]["text"])
        self.assertIn("1234", parts[1]["inlineData"]["data"])
        self.assertEqual(
            payload["generationConfig"]["responseSchema"], gemini.EVALUATION_SCHEMA
        )

    @override_settings(GEMINI_API_KEY="")
    def test_requires_api_key(self) -> None:
        with self.assertRaises(gemini.GeminiConfigurationError):
            gemini.evaluate_outfit(
            make_image_bytes(), mime_type="image/jpeg", context=CONTEXT
        )


class OutfitContextTests(SimpleTestCase):
    @patch(
        "apps.recommend.services.outfit_context.get_current_weather",
        return_value=WEATHER,
    )
    def test_anonymous_context_omits_personal_data(self, mock_weather: Mock) -> None:
        context = build_analysis_context(AnonymousUser(), lat=None, lon=None)

        self.assertEqual(context["weather"], WEATHER)
        self.assertIsNone(context["pursuit"])
        self.assertIsNone(context["body"])
        self.assertFalse(context["personalized"])
        mock_weather.assert_called_once()

    @patch(
        "apps.recommend.services.outfit_context.get_current_weather",
        return_value=dict(RAW_WEATHER),
    )
    def test_weather_datetime_is_json_serializable(self, _mock_weather: Mock) -> None:
        context = build_analysis_context(AnonymousUser(), lat=None, lon=None)

        self.assertEqual(
            context["weather"]["observed_at"], OBSERVED_AT.isoformat()
        )
        json.dumps(context)  # 응답 직렬화(JSONField)와 같은 조건

    @patch("apps.recommend.services.outfit_context.get_pursuit")
    @patch("apps.recommend.services.outfit_context.BodyMeasurement.objects.filter")
    @patch(
        "apps.recommend.services.outfit_context.get_current_weather",
        return_value=WEATHER,
    )
    def test_authenticated_context_includes_pursuit_and_body(
        self,
        _mock_weather: Mock,
        mock_filter: Mock,
        mock_pursuit: Mock,
    ) -> None:
        user = SimpleNamespace(is_authenticated=True)
        mock_pursuit.return_value = {"preferred": {"styles": ["minimal"]}}
        mock_filter.return_value.first.return_value = SimpleNamespace(
            gender="female",
            height=None,
            weight=None,
            chest=None,
            waist=None,
            hip=None,
            thigh_length=None,
            calf_length=None,
            shoulder=None,
            neck_length=Decimal("9.6"),
            thigh_calf_ratio=Decimal("1.112"),
            torso_leg_ratio=Decimal("0.786"),
        )

        context = build_analysis_context(user, lat=37.5, lon=127.0)

        self.assertTrue(context["personalized"])
        self.assertEqual(context["body"]["gender"], "female")
        self.assertEqual(context["body"]["neck_length"], 9.6)
        self.assertEqual(context["body"]["thigh_calf_ratio"], 1.112)
        self.assertEqual(context["body"]["torso_leg_ratio"], 0.786)
        self.assertEqual(context["pursuit"]["preferred"]["styles"], ["minimal"])


@override_settings(
    QDRANT_IMAGE_VECTOR_DIM=768,
    QDRANT_TEXT_VECTOR_DIM=1024,
)
class QdrantSchemaTests(SimpleTestCase):
    def test_golden_collections_expose_role_and_scope_filters(self) -> None:
        specs = {spec.name: spec for spec in qdrant.collection_specs()}

        self.assertEqual(specs["knowledge"].vectors, {"text": 1024})
        self.assertEqual(
            specs["knowledge"].payload_indexes["knowledge_role"], "keyword"
        )
        self.assertEqual(
            specs["knowledge"].payload_indexes["eligible_for_scoring"], "bool"
        )
        self.assertEqual(
            specs["outfit_goldenset"].vectors,
            {"image": 768, "text": 1024},
        )
        self.assertEqual(
            specs["outfit_goldenset"].payload_indexes["anchor_scope"],
            "keyword",
        )

    def test_existing_collection_receives_new_payload_indexes(self) -> None:
        client = Mock()
        client.collection_exists.return_value = True
        client.get_collection.return_value = SimpleNamespace(payload_schema={})

        created = qdrant.ensure_collections(client)

        self.assertEqual(created, [])
        client.create_collection.assert_not_called()
        client.create_payload_index.assert_any_call(
            collection_name="knowledge",
            field_name="knowledge_role",
            field_schema="keyword",
        )
        client.create_payload_index.assert_any_call(
            collection_name="outfit_goldenset",
            field_name="anchor_scope",
            field_schema="keyword",
        )
class ImagingTests(SimpleTestCase):
    def test_shrinks_large_image_to_max_edge(self) -> None:
        original = make_image_bytes((2400, 1200))

        shrunk, mime = imaging.shrink_for_llm(original, mime_type="image/jpeg")

        self.assertEqual(mime, "image/jpeg")
        self.assertLess(len(shrunk), len(original))
        with Image.open(BytesIO(shrunk)) as image:
            self.assertEqual(max(image.size), imaging.MAX_EDGE_PX)
            self.assertEqual(image.size[0] / image.size[1], 2)  # 비율 유지

    def test_uses_resized_version_even_if_bytes_grow(self) -> None:
        """잘 압축되는 큰 PNG도 해상도를 줄여 보낸다 (모델 처리 픽셀 수가 목적)."""
        buffer = BytesIO()
        make_image((1600, 1600)).save(buffer, format="PNG")

        shrunk, mime = imaging.shrink_for_llm(buffer.getvalue(), mime_type="image/png")

        self.assertEqual(mime, "image/jpeg")
        with Image.open(BytesIO(shrunk)) as image:
            self.assertEqual(max(image.size), imaging.MAX_EDGE_PX)

    def test_keeps_already_small_image(self) -> None:
        original = make_image_bytes((8, 8))

        shrunk, mime = imaging.shrink_for_llm(original, mime_type="image/jpeg")

        # 재압축이 손해면 원본을 그대로 쓴다
        self.assertLessEqual(len(shrunk), len(original))
        self.assertEqual(mime, "image/jpeg")

    def test_broken_image_falls_back_to_original(self) -> None:
        """축소는 최적화일 뿐이라 실패해도 평가를 막지 않는다."""
        shrunk, mime = imaging.shrink_for_llm(b"not an image", mime_type="image/jpeg")

        self.assertEqual(shrunk, b"not an image")
        self.assertEqual(mime, "image/jpeg")


class ClaimTokenTests(SimpleTestCase):
    """토큰 자체의 성질 — 서명·만료·격리."""

    def test_token_carries_analysis_id(self) -> None:
        analysis = OutfitAnalysis(user=None)

        token = claim_service.issue_token(analysis)

        self.assertEqual(claim_service.verify_token(token), (str(analysis.pk), None))

    def test_no_token_for_logged_in_accept(self) -> None:
        """이미 주인이 있으면 넘겨받을 것이 없다."""
        self.assertIsNone(claim_service.issue_token(OutfitAnalysis(user_id=1)))

    def test_tampered_token_is_rejected(self) -> None:
        analysis = OutfitAnalysis(user=None)
        token = claim_service.issue_token(analysis)

        other_id = "6b2c1f3a-9d4e-4c8b-8a71-2f0e5d9c3b17"
        forged = other_id + token[token.index(":") :]

        self.assertEqual(
            claim_service.verify_token(forged),
            (None, claim_service.SkipReason.INVALID_TOKEN),
        )

    @override_settings(OUTFIT_CLAIM_TTL_MINUTES=0)
    def test_expired_token_is_rejected(self) -> None:
        token = claim_service.issue_token(OutfitAnalysis(user=None))

        _, reason = claim_service.verify_token(token)

        self.assertEqual(reason, claim_service.SkipReason.EXPIRED)

    def test_signature_is_namespaced_by_salt(self) -> None:
        """같은 SECRET_KEY로 만든 다른 용도의 서명을 claim 토큰으로 쓸 수 없다."""
        from django.core import signing

        foreign = signing.TimestampSigner(salt="something-else").sign("x")

        self.assertEqual(
            claim_service.verify_token(foreign),
            (None, claim_service.SkipReason.INVALID_TOKEN),
        )


@patch("apps.recommend.services.claim.storage.is_configured", return_value=True)
@patch("apps.recommend.services.claim.storage.move")
class ClaimApiTests(TestCase):
    """POST /outfits/analyses/claim/ — 소유권 이전."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.url = reverse("recommend:outfit-analysis-claim")
        self.user = get_user_model().objects.create(username="naver_1")
        self.other = get_user_model().objects.create(username="kakao_2")

    def _anonymous_analysis(self) -> OutfitAnalysis:
        analysis = OutfitAnalysis.objects.create(
            user=None,
            accepted_anonymously=True,
            status=OutfitAnalysis.Status.SUCCEEDED,
            evaluation=EVALUATION,
            weather=WEATHER,
        )
        analysis.image_s3_key = f"outfits/anonymous/{analysis.pk}/original.jpg"
        analysis.save(update_fields=["image_s3_key"])
        return analysis

    def _claim(self, *tokens) -> object:
        return self.client.post(self.url, {"claim_tokens": list(tokens)}, format="json")

    def test_requires_authentication(self, _mock_move: Mock, _mock_cfg: Mock) -> None:
        analysis = self._anonymous_analysis()

        response = self._claim(claim_service.issue_token(analysis))

        self.assertEqual(response.status_code, 401)

    def test_transfers_ownership_without_reevaluating(
        self, _mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        analysis = self._anonymous_analysis()
        token = claim_service.issue_token(analysis)
        self.client.force_authenticate(user=self.user)

        response = self._claim(token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([str(i) for i in response.data["claimed"]], [str(analysis.pk)])
        analysis.refresh_from_db()
        self.assertEqual(analysis.user, self.user)
        self.assertIsNotNone(analysis.claimed_at)
        # 평가는 다시 하지 않는다 — 결과와 상태가 그대로여야 한다
        self.assertEqual(analysis.status, OutfitAnalysis.Status.SUCCEEDED)
        self.assertEqual(analysis.evaluation, EVALUATION)
        self.assertEqual(analysis.attempts, 0)
        # 개인화 없이 나온 결과라는 사실은 남는다
        self.assertTrue(analysis.accepted_anonymously)
        self.assertFalse(analysis.personalized)

    def test_moves_photo_into_owner_folder(
        self, mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        """익명 프리픽스에 두면 익명 사진 정리에 함께 쓸려나간다."""
        analysis = self._anonymous_analysis()
        old_key = analysis.image_s3_key
        token = claim_service.issue_token(analysis)
        self.client.force_authenticate(user=self.user)

        self._claim(token)

        analysis.refresh_from_db()
        new_key = f"outfits/{self.user.pk}/{analysis.pk}/original.jpg"
        self.assertEqual(analysis.image_s3_key, new_key)
        mock_move.assert_called_once_with(old_key, new_key)

    def test_photo_move_failure_keeps_ownership(
        self, mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        """이동은 best-effort — 실패해도 기존 키로 계속 읽을 수 있다."""
        mock_move.side_effect = RuntimeError("S3 down")
        analysis = self._anonymous_analysis()
        old_key = analysis.image_s3_key
        token = claim_service.issue_token(analysis)
        self.client.force_authenticate(user=self.user)

        response = self._claim(token)

        self.assertEqual(response.status_code, 200)
        analysis.refresh_from_db()
        self.assertEqual(analysis.user, self.user)
        self.assertEqual(analysis.image_s3_key, old_key)

    def test_uuid_alone_is_not_enough(self, _mock_move: Mock, _mock_cfg: Mock) -> None:
        """조회는 UUID로 되지만 claim은 안 된다 (권한 상승 경로라 토큰을 요구)."""
        analysis = self._anonymous_analysis()
        self.client.force_authenticate(user=self.user)

        response = self._claim(str(analysis.pk))

        self.assertEqual(response.data["claimed"], [])
        self.assertEqual(
            response.data["skipped"][0]["reason"],
            claim_service.SkipReason.INVALID_TOKEN,
        )
        analysis.refresh_from_db()
        self.assertIsNone(analysis.user)

    def test_cannot_steal_someone_elses_record(
        self, _mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        analysis = self._anonymous_analysis()
        token = claim_service.issue_token(analysis)
        OutfitAnalysis.objects.filter(pk=analysis.pk).update(user=self.other)
        self.client.force_authenticate(user=self.user)

        response = self._claim(token)

        self.assertEqual(response.data["claimed"], [])
        self.assertEqual(
            response.data["skipped"][0]["reason"],
            claim_service.SkipReason.ALREADY_OWNED,
        )
        analysis.refresh_from_db()
        self.assertEqual(analysis.user, self.other)

    def test_repeat_claim_is_idempotent(self, _mock_move: Mock, _mock_cfg: Mock) -> None:
        analysis = self._anonymous_analysis()
        token = claim_service.issue_token(analysis)
        self.client.force_authenticate(user=self.user)

        self._claim(token)
        response = self._claim(token)

        self.assertEqual([str(i) for i in response.data["claimed"]], [str(analysis.pk)])

    def test_expired_row_is_rejected_even_with_valid_token(
        self, _mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        """토큰이 살아 있어도 행이 오래됐으면 막는다 (DB 쪽 2차 방어)."""
        analysis = self._anonymous_analysis()
        token = claim_service.issue_token(analysis)
        OutfitAnalysis.objects.filter(pk=analysis.pk).update(
            created_at=timezone.now() - timedelta(hours=3)
        )
        self.client.force_authenticate(user=self.user)

        response = self._claim(token)

        self.assertEqual(
            response.data["skipped"][0]["reason"], claim_service.SkipReason.EXPIRED
        )
        analysis.refresh_from_db()
        self.assertIsNone(analysis.user)

    def test_missing_row_reports_not_found(
        self, _mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        analysis = self._anonymous_analysis()
        token = claim_service.issue_token(analysis)
        analysis.delete()
        self.client.force_authenticate(user=self.user)

        response = self._claim(token)

        self.assertEqual(
            response.data["skipped"][0]["reason"], claim_service.SkipReason.NOT_FOUND
        )

    def test_batch_processes_each_token_independently(
        self, _mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        good = self._anonymous_analysis()
        self.client.force_authenticate(user=self.user)

        response = self._claim(claim_service.issue_token(good), "garbage")

        self.assertEqual([str(i) for i in response.data["claimed"]], [str(good.pk)])
        self.assertEqual(len(response.data["skipped"]), 1)

    def test_rejects_too_many_tokens(self, _mock_move: Mock, _mock_cfg: Mock) -> None:
        self.client.force_authenticate(user=self.user)

        response = self._claim(*["t"] * 21)

        self.assertEqual(response.status_code, 400)

    def test_rejects_empty_list(self, _mock_move: Mock, _mock_cfg: Mock) -> None:
        self.client.force_authenticate(user=self.user)

        self.assertEqual(self._claim().status_code, 400)

    def test_claimed_record_needs_token_for_polling(
        self, _mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        """이전 후에는 익명 조회가 닫힌다 — 프론트가 폴링 헤더를 갈아야 한다."""
        analysis = self._anonymous_analysis()
        token = claim_service.issue_token(analysis)
        detail_url = reverse("recommend:outfit-analysis-detail", args=[analysis.pk])
        self.assertEqual(self.client.get(detail_url).status_code, 200)  # 이전 전

        self.client.force_authenticate(user=self.user)
        self._claim(token)

        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.client.get(detail_url).status_code, 200)

    def test_internal_flag_is_not_exposed_to_clients(
        self, _mock_move: Mock, _mock_cfg: Mock
    ) -> None:
        """accepted_anonymously는 내부 기록이라 응답에 싣지 않는다."""
        analysis = self._anonymous_analysis()
        token = claim_service.issue_token(analysis)
        detail_url = reverse("recommend:outfit-analysis-detail", args=[analysis.pk])
        self.assertNotIn("accepted_anonymously", self.client.get(detail_url).data)

        self.client.force_authenticate(user=self.user)
        self._claim(token)

        for data in (
            self.client.get(detail_url).data,
            self.client.get(reverse("recommend:outfit-analysis-list")).data["results"][0],
        ):
            self.assertNotIn("accepted_anonymously", data)
            self.assertNotIn("claimed_at", data)
