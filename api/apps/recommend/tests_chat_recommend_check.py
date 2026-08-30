"""check_chat_recommend 커맨드 회귀 테스트.

운영에서 실제로 났던 실패(적재 PILOT ↔ 설정 ACTIVE)를 그대로 재현해, 커맨드가
'어느 조건에서 0건이 되는지'를 짚어내는지 못 박는다. 진단 도구가 조용히
"점검 완료"를 찍으면 없느니만 못하다.
"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


def _point(**payload) -> SimpleNamespace:
    return SimpleNamespace(id="p", payload=payload)


def _client(*, scroll_points, count_side_effect) -> Mock:
    client = Mock()
    client.get_collections.return_value = SimpleNamespace(collections=[])
    client.collection_exists.return_value = True
    client.get_collection.return_value = SimpleNamespace(points_count=len(scroll_points))
    client.scroll.return_value = (scroll_points, None)
    client.count.side_effect = [
        SimpleNamespace(count=value) for value in count_side_effect
    ]
    return client


@override_settings(
    CHAT_GOLDENSET_DATASET_VERSION="v1",
    CHAT_GOLDENSET_DATASET_STATUSES=("ACTIVE",),
    TEXT_EMBEDDING_API_URL="",
)
class CheckChatRecommendCommandTests(SimpleTestCase):
    """적재값과 설정값이 어긋난 상태를 커맨드가 이름 붙여 지목하는지."""

    @patch("apps.recommend.services.qdrant.get_client")
    def test_reports_status_mismatch_as_the_culprit(self, get_client) -> None:
        # 운영 재현: dataset_version은 맞는데 status만 PILOT으로 남아 있다.
        get_client.return_value = _client(
            scroll_points=[
                _point(dataset_version="v1", status="PILOT", presentation_group="men"),
                _point(dataset_version="v1", status="PILOT", presentation_group="women"),
            ],
            # 필터 없음 → +version → +status → +성별
            count_side_effect=[2, 2, 0, 0],
        )
        stdout = StringIO()

        call_command("check_chat_recommend", stdout=stdout, stderr=StringIO())

        output = stdout.getvalue()
        self.assertIn("설정 ['ACTIVE'] 과 적재값 ['PILOT'] 이", output)
        self.assertIn("set_goldenset_qdrant_status", output)
        self.assertIn("조건에서 후보가 전부 사라집니다", output)
        self.assertIn("dataset_statuses", output)
        # 원인 지목 뒤에는 멈춰야 한다. 뒤 단계를 계속 돌면 진짜 원인이 묻힌다.
        self.assertNotIn("점검 완료", output)

    @patch("apps.recommend.services.qdrant.get_client")
    def test_reports_version_mismatch(self, get_client) -> None:
        get_client.return_value = _client(
            scroll_points=[
                _point(dataset_version="v0", status="ACTIVE", presentation_group="men"),
            ],
            count_side_effect=[1, 0, 0, 0],
        )
        stdout = StringIO()

        call_command("check_chat_recommend", stdout=stdout, stderr=StringIO())

        output = stdout.getvalue()
        self.assertIn("설정한 'v1' 버전으로 적재된 코디가 없습니다", output)

    @patch("apps.recommend.services.qdrant.get_client")
    def test_warns_when_outfit_collection_is_empty(self, get_client) -> None:
        get_client.return_value = _client(scroll_points=[], count_side_effect=[0])
        stdout = StringIO()

        call_command("check_chat_recommend", stdout=stdout, stderr=StringIO())

        output = stdout.getvalue()
        self.assertIn("골든 코디가 한 건도 없습니다", output)

    @patch("apps.recommend.services.retriever.retrieve_outfits")
    @patch("apps.recommend.services.qdrant.get_client")
    def test_passes_when_loaded_status_matches(self, get_client, retrieve) -> None:
        get_client.return_value = _client(
            scroll_points=[
                _point(dataset_version="v1", status="ACTIVE", presentation_group="men"),
            ],
            count_side_effect=[1, 1, 1, 1],
        )
        retrieve.return_value = [
            SimpleNamespace(
                golden_id="g1",
                score=12.5,
                payload={"presentation_group": "men", "items": [{}, {}]},
            )
        ]
        stdout = StringIO()

        call_command("check_chat_recommend", stdout=stdout, stderr=StringIO())

        output = stdout.getvalue()
        self.assertIn("후보 1건", output)
        self.assertIn("점검 완료", output)

    @override_settings(CHAT_GOLDENSET_DATASET_STATUSES=("PUBLISHED",))
    @patch("apps.recommend.services.qdrant.get_client")
    def test_rejects_status_outside_model_contract(self, get_client) -> None:
        get_client.return_value = _client(scroll_points=[], count_side_effect=[0])
        stdout = StringIO()

        call_command("check_chat_recommend", stdout=stdout, stderr=StringIO())

        output = stdout.getvalue()
        # PUBLISHED는 GoldenDataset에 없는 값이라 Qdrant를 보기도 전에 걸러야 한다.
        self.assertIn("지원하지 않는 상태", output)
        self.assertNotIn("Qdrant 연결", output)
