from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from apps.wardrobe.models import (
    SharedWardrobeCategory,
    SharedWardrobeItem,
    SharedWardrobeMember,
    SharedWardrobeRoom,
    WardrobeItem,
)
from apps.wardrobe.services import shared_wardrobe as shared_service

User = get_user_model()

class SharedWardrobeTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="hayoung", email="hayoung@test.com", password="password")
        self.user2 = User.objects.create_user(username="hyeji", email="hyeji@test.com", password="password")
        self.user3 = User.objects.create_user(username="lkw", email="lkw@test.com", password="password")
        
        # 개인 옷장 아이템 생성
        self.item1 = WardrobeItem.objects.create(
            user=self.user1,
            s3_key="wardrobe/hayoung/shirt.png",
            category_large="상의",
            category_small="티셔츠",
            item_name="hayoung_shirt",
            confirmed=True
        )
        self.unconfirmed_item = WardrobeItem.objects.create(
            user=self.user1,
            s3_key="wardrobe/hayoung/pending-shirt.png",
            category_large="상의",
            category_small="티셔츠",
            item_name="pending_shirt",
            confirmed=False,
        )
        self.client = APIClient()

    @override_settings(DEBUG=True)
    def test_create_room(self):
        """DEBUG 환경에서도 생성자만 방장으로 등록되는지 확인합니다."""
        room = shared_service.create_shared_room(self.user1, "하영이네 옷장")
        self.assertEqual(room.title, "하영이네 옷장")
        self.assertEqual(len(room.invite_code), 6)

        member = SharedWardrobeMember.objects.get(room=room, user=self.user1)
        self.assertEqual(member.role, SharedWardrobeMember.Role.OWNER)
        self.assertEqual(SharedWardrobeMember.objects.filter(room=room).count(), 1)

    def test_shared_room_full_api_lifecycle(self):
        """Swagger에 노출된 방 관리 API를 실제 요청 순서대로 전부 실행합니다."""
        self.client.force_authenticate(self.user1)
        created = self.client.post(
            "/api/v1/shared-wardrobes/",
            {"title": "API 검증방"},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        room_id = created.data["id"]

        listed = self.client.get("/api/v1/shared-wardrobes/")
        detailed = self.client.get(f"/api/v1/shared-wardrobes/{room_id}/")
        renamed = self.client.patch(
            f"/api/v1/shared-wardrobes/{room_id}/",
            {"title": "수정된 검증방"},
            format="json",
        )
        refreshed = self.client.post(
            f"/api/v1/shared-wardrobes/{room_id}/refresh-code/"
        )
        self.assertEqual((listed.status_code, detailed.status_code), (200, 200))
        self.assertEqual((renamed.status_code, renamed.data["title"]), (200, "수정된 검증방"))
        self.assertEqual(refreshed.status_code, 200)
        invite_code = refreshed.data["invite_code"]

        preview = APIClient().get(
            f"/api/v1/shared-wardrobes/preview/?code={invite_code}"
        )
        self.assertEqual(preview.status_code, 200)

        member_client = APIClient()
        member_client.force_authenticate(self.user2)
        joined = member_client.post(
            "/api/v1/shared-wardrobes/join/",
            {"invite_code": invite_code},
            format="json",
        )
        self.assertEqual((joined.status_code, joined.data["status"]), (200, "joined"))

        members = self.client.get(
            f"/api/v1/shared-wardrobes/{room_id}/members/"
        )
        self.assertEqual((members.status_code, len(members.data)), (200, 2))

        left = member_client.post(
            f"/api/v1/shared-wardrobes/{room_id}/leave/",
            {"delete_my_items": False},
            format="json",
        )
        self.assertEqual(left.status_code, 204)
        self.assertFalse(
            SharedWardrobeMember.objects.filter(room_id=room_id, user=self.user2).exists()
        )

    def test_invite_code_expiry(self):
        """초대코드가 24시간을 경과하면 만료 처리되어 가입할 수 없는지 확인합니다."""
        room = shared_service.create_shared_room(self.user1, "하영이네 옷장")
        
        # 만료 시각을 과거로 강제 조작
        room.code_expires_at = timezone.now() - timedelta(seconds=1)
        room.save()
        
        # user2 가 가입 시도할 때 ValueError 발생해야 함
        with self.assertRaises(ValueError) as ctx:
            shared_service.join_shared_room(self.user2, room.invite_code)
        self.assertIn("초대코드가 24시간 만료 시간을 초과", str(ctx.exception))

    def test_refresh_invite_code(self):
        """방장만 초대코드를 24시간짜리 새 코드로 재발급할 수 있는지 확인합니다."""
        room = shared_service.create_shared_room(self.user1, "하영이네 옷장")
        old_code = room.invite_code
        
        # 방장이 아닌 user2 가 재발급 시도 시 PermissionError 발생
        with self.assertRaises(PermissionError):
            shared_service.refresh_invite_code(self.user2, str(room.pk))
            
        # 방장인 user1 이 재발급
        updated_room = shared_service.refresh_invite_code(self.user1, str(room.pk))
        self.assertNotEqual(updated_room.invite_code, old_code)
        self.assertGreater(updated_room.code_expires_at, timezone.now() + timedelta(hours=23))

    def test_leave_room_owner_delegation(self):
        """방장이 나갈 때 가입일시 순서대로 방장 권한이 위임되는지 확인합니다."""
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        
        # user2 가입
        shared_service.join_shared_room(self.user2, room.invite_code)
        # user3 가입
        shared_service.join_shared_room(self.user3, room.invite_code)
        
        # 방장인 user1 이 퇴장
        shared_service.leave_shared_room(self.user1, str(room.pk))
        
        # user2 가 새로운 방장(owner)으로 자동 승격되었는지 확인
        user2_member = SharedWardrobeMember.objects.get(room=room, user=self.user2)
        self.assertEqual(user2_member.role, SharedWardrobeMember.Role.OWNER)

    def test_leave_room_item_option(self):
        """탈퇴 시 아이템 삭제 옵션(A/B)이 올바르게 분기 처리되는지 확인합니다."""
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        
        # user1 이 방에 옷 등록
        shared_item = shared_service.register_item_to_shared_room(self.user1, str(room.pk), str(self.item1.pk))
        self.assertEqual(SharedWardrobeItem.objects.filter(room=room).count(), 1)
        
        # 옵션 B: 아이템 유지하고 탈퇴 (delete_my_items = False)
        shared_service.leave_shared_room(self.user1, str(room.pk), delete_my_items=False)
        
        # 옷은 남아있고 등록자는 None 처리되어야 함
        shared_item.refresh_from_db()
        self.assertIsNone(shared_item.registered_by)
        
        # user2 도 방에서 나가며 옵션 A: 아이템 삭제 탈퇴 (delete_my_items = True)
        shared_service.leave_shared_room(self.user2, str(room.pk), delete_my_items=True)
        # 방에 남은 인원이 없으므로 방 자체가 삭제되어야 함
        self.assertFalse(SharedWardrobeRoom.objects.filter(pk=room.pk).exists())

    def test_unconfirmed_item_cannot_be_registered(self):
        """사용자 확정 전 아이템은 공유 옷장에 등록할 수 없습니다."""
        room = shared_service.create_shared_room(self.user1, "공유 옷방")

        with self.assertRaisesMessage(ValueError, "사용자가 확정한 옷만"):
            shared_service.register_item_to_shared_room(
                self.user1,
                str(room.pk),
                str(self.unconfirmed_item.pk),
            )

        self.assertFalse(
            SharedWardrobeItem.objects.filter(
                room=room,
                wardrobe_item=self.unconfirmed_item,
            ).exists()
        )

    def test_member_cannot_delete_shared_room_via_api(self):
        """일반 멤버의 공유 옷장 DELETE는 403이고 방은 유지됩니다."""
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        self.client.force_authenticate(self.user2)

        response = self.client.delete(f"/api/v1/shared-wardrobes/{room.pk}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(SharedWardrobeRoom.objects.filter(pk=room.pk).exists())

    def test_owner_can_delete_shared_room_via_api(self):
        """방장의 공유 옷장 DELETE는 204이고 방을 삭제합니다."""
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        self.client.force_authenticate(self.user1)

        response = self.client.delete(f"/api/v1/shared-wardrobes/{room.pk}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(SharedWardrobeRoom.objects.filter(pk=room.pk).exists())

    def test_member_can_create_list_and_delete_custom_category_via_api(self):
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        self.client.force_authenticate(self.user2)
        url = f"/api/v1/shared-wardrobes/{room.pk}/categories/"

        created = self.client.post(url, {"name": " 운동복 "}, format="json")
        self.assertEqual(created.status_code, 201)
        category = SharedWardrobeCategory.objects.get(pk=created.data["id"])
        self.assertEqual(category.name, "운동복")
        self.assertEqual(category.created_by, self.user2)

        listed = self.client.get(url)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["name"] for row in listed.data], ["운동복"])

        deleted = self.client.delete(f"{url}?category_id={category.pk}")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(SharedWardrobeCategory.objects.filter(pk=category.pk).exists())

    def test_custom_category_rejects_default_and_duplicate_names(self):
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        self.client.force_authenticate(self.user1)
        url = f"/api/v1/shared-wardrobes/{room.pk}/categories/"

        default_name = self.client.post(url, {"name": "상의"}, format="json")
        first = self.client.post(url, {"name": "여행룩"}, format="json")
        duplicate = self.client.post(url, {"name": "여행룩"}, format="json")

        self.assertEqual(default_name.status_code, 400)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)

    def test_room_member_can_read_shared_item_detail_but_outsider_cannot(self):
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        shared_service.register_item_to_shared_room(
            self.user1, str(room.pk), str(self.item1.pk)
        )
        url = f"/api/v1/wardrobe/items/{self.item1.pk}/"

        self.client.force_authenticate(self.user2)
        member_response = self.client.get(url)
        self.assertEqual(member_response.status_code, 200)
        self.assertEqual(str(member_response.data["id"]), str(self.item1.pk))

        self.client.force_authenticate(self.user3)
        outsider_response = self.client.get(url)
        self.assertEqual(outsider_response.status_code, 404)

    def test_share_button_api_persists_item_and_exposes_it_to_room_members(self):
        """개인 옷 공유 요청이 DB에 저장되고 다른 방 멤버의 목록에도 노출됩니다."""
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        url = f"/api/v1/shared-wardrobes/{room.pk}/items/"

        self.client.force_authenticate(self.user1)
        created = self.client.post(
            url,
            {"wardrobe_item_id": str(self.item1.pk), "status": "available"},
            format="json",
        )

        self.assertEqual(created.status_code, 201)
        shared_item = SharedWardrobeItem.objects.get(
            room=room,
            wardrobe_item=self.item1,
        )
        self.assertEqual(shared_item.registered_by, self.user1)
        self.assertEqual(shared_item.status, SharedWardrobeItem.Status.AVAILABLE)

        self.client.force_authenticate(self.user2)
        listed = self.client.get(url)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data), 1)
        self.assertEqual(
            str(listed.data[0]["wardrobe_item"]["id"]),
            str(self.item1.pk),
        )

        self.client.force_authenticate(self.user1)
        repeated = self.client.post(
            url,
            {"wardrobe_item_id": str(self.item1.pk), "status": "available"},
            format="json",
        )
        self.assertEqual(repeated.status_code, 201)
        self.assertEqual(
            SharedWardrobeItem.objects.filter(
                room=room, wardrobe_item=self.item1
            ).count(),
            1,
        )

    def test_unconfirmed_share_api_returns_actionable_error(self):
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        self.client.force_authenticate(self.user1)

        response = self.client.post(
            f"/api/v1/shared-wardrobes/{room.pk}/items/",
            {"wardrobe_item_id": str(self.unconfirmed_item.pk)},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("사용자가 확정한 옷만", response.data["detail"])
        self.assertFalse(
            SharedWardrobeItem.objects.filter(wardrobe_item=self.unconfirmed_item).exists()
        )

    def test_only_item_owner_or_room_owner_can_change_shared_item_status(self):
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        shared_service.join_shared_room(self.user3, room.invite_code)
        user2_item = WardrobeItem.objects.create(
            user=self.user2,
            s3_key="wardrobe/hyeji/jacket.png",
            item_name="hyeji_jacket",
            confirmed=True,
        )
        shared_item = shared_service.register_item_to_shared_room(
            self.user2, str(room.pk), str(user2_item.pk)
        )
        url = f"/api/v1/shared-wardrobes/{room.pk}/items/"

        self.client.force_authenticate(self.user3)
        denied = self.client.patch(
            url,
            {"item_id": str(shared_item.pk), "status": "borrowed"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.user1)
        changed = self.client.patch(
            url,
            {"item_id": str(shared_item.pk), "status": "borrowed"},
            format="json",
        )
        self.assertEqual(changed.status_code, 200)
        shared_item.refresh_from_db()
        self.assertEqual(shared_item.status, SharedWardrobeItem.Status.BORROWED)

    def test_private_item_is_visible_only_to_registrant(self):
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.join_shared_room(self.user2, room.invite_code)
        shared_service.register_item_to_shared_room(
            self.user1,
            str(room.pk),
            str(self.item1.pk),
            status=SharedWardrobeItem.Status.PRIVATE,
        )
        room_items_url = f"/api/v1/shared-wardrobes/{room.pk}/items/"
        detail_url = f"/api/v1/wardrobe/items/{self.item1.pk}/"

        self.client.force_authenticate(self.user1)
        owner_list = self.client.get(room_items_url)
        self.assertEqual(len(owner_list.data), 1)

        self.client.force_authenticate(self.user2)
        member_list = self.client.get(room_items_url)
        member_detail = self.client.get(detail_url)
        self.assertEqual(member_list.status_code, 200)
        self.assertEqual(member_list.data, [])
        self.assertEqual(member_detail.status_code, 404)

        self.client.force_authenticate(user=None)
        preview = self.client.get(
            "/api/v1/shared-wardrobes/preview/",
            {"code": room.invite_code},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data["items"], [])

    def test_unshare_deletes_link_but_preserves_personal_wardrobe_item(self):
        room = shared_service.create_shared_room(self.user1, "공유 옷방")
        shared_service.register_item_to_shared_room(
            self.user1, str(room.pk), str(self.item1.pk)
        )
        self.client.force_authenticate(self.user1)

        response = self.client.delete(
            f"/api/v1/shared-wardrobes/{room.pk}/items/",
            {"wardrobe_item_id": str(self.item1.pk)},
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            SharedWardrobeItem.objects.filter(room=room, wardrobe_item=self.item1).exists()
        )
        self.assertTrue(WardrobeItem.objects.filter(pk=self.item1.pk).exists())


class PendingShareReservationTests(TestCase):
    """등록 화면에서 켠 '공유 옷장' 토글이 확정 시점에 소진되는지 검증한다.

    예약을 기기(secureStore)가 아니라 DB(wardrobe_item.pending_share_room)에 두는 이유는
    PC 에서 올리고 폰에서 확정해도 공유가 살아야 하기 때문이다. 그래서 검증도
    '클라이언트가 무엇을 기억하는가'가 아니라 '서버가 확정 시 무엇을 하는가'로 한다.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", email="owner@test.com", password="password"
        )
        self.room = shared_service.create_shared_room(self.owner, "예약 테스트방")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _make_item(self, **kwargs):
        defaults = dict(
            user=self.owner,
            s3_key="wardrobe/owner/item.png",
            category_large="상의",
            category_small="티셔츠",
            item_name="예약옷",
            confirmed=False,
            pending_share_room=self.room,
        )
        defaults.update(kwargs)
        return WardrobeItem.objects.create(**defaults)

    def test_confirm_redeems_reservation(self):
        """확정하면 예약이 공유로 바뀌고 예약 컬럼은 비워진다."""
        item = self._make_item()

        res = self.client.patch(
            f"/api/v1/wardrobe/items/{item.pk}/", {"confirmed": True}, format="json"
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["shared_room_id"], str(self.room.pk))
        self.assertTrue(
            SharedWardrobeItem.objects.filter(room=self.room, wardrobe_item=item).exists()
        )
        item.refresh_from_db()
        self.assertIsNone(item.pending_share_room_id)
        self.assertEqual(item.pending_share_status, "")

    def test_reserved_status_is_applied(self):
        """등록할 때 고른 상태(private 등)가 공유 행에 그대로 실린다."""
        item = self._make_item(pending_share_status=SharedWardrobeItem.Status.PRIVATE)

        self.client.patch(
            f"/api/v1/wardrobe/items/{item.pk}/", {"confirmed": True}, format="json"
        )

        shared = SharedWardrobeItem.objects.get(room=self.room, wardrobe_item=item)
        self.assertEqual(shared.status, SharedWardrobeItem.Status.PRIVATE)

    def test_confirm_succeeds_after_leaving_room(self):
        """예약해 둔 방을 나간 뒤 확정해도 확정 자체는 성공하고 예약만 사라진다.

        여기서 500 이 나거나 예약이 남으면, 사용자는 확정할 때마다 같은 실패를 반복한다.
        """
        item = self._make_item()
        shared_service.leave_shared_room(self.owner, str(self.room.pk))

        res = self.client.patch(
            f"/api/v1/wardrobe/items/{item.pk}/", {"confirmed": True}, format="json"
        )

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data["shared_room_id"])
        item.refresh_from_db()
        self.assertTrue(item.confirmed)
        self.assertIsNone(item.pending_share_room_id)

    def test_patch_without_confirm_keeps_reservation(self):
        """이름만 고치는 PATCH 는 예약을 건드리지 않는다 (확정 전에 소진되면 400 이 난다)."""
        item = self._make_item()

        res = self.client.patch(
            f"/api/v1/wardrobe/items/{item.pk}/", {"item_name": "이름만 수정"}, format="json"
        )

        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data["shared_room_id"])
        item.refresh_from_db()
        self.assertEqual(item.pending_share_room_id, self.room.pk)
        self.assertFalse(
            SharedWardrobeItem.objects.filter(room=self.room, wardrobe_item=item).exists()
        )

    def test_reservation_survives_room_deletion_as_null(self):
        """방이 지워져도 옷은 남고 예약만 NULL 이 된다 (SET_NULL)."""
        item = self._make_item()
        self.room.delete()

        item.refresh_from_db()
        self.assertIsNone(item.pending_share_room_id)


class SharedRoomGuardrailTests(TestCase):
    """2026-08-16 리뷰에서 나온 구멍들의 회귀 테스트.

    - 정원 6명: 명세에 있는 정책인데 테스트가 한 건도 없었다
    - 방 이름 수정: destroy/refresh 는 owner 전용인데 rename 만 뚫려 있었다
    - 잘못된 UUID: 400/404 여야 할 요청이 ValidationError 로 500 이 났다
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="cap_owner", email="cap_owner@test.com", password="password"
        )
        self.member = User.objects.create_user(
            username="cap_member", email="cap_member@test.com", password="password"
        )
        self.room = shared_service.create_shared_room(self.owner, "정원 테스트방")
        self.client = APIClient()

    def _fill_room_to_capacity(self):
        """owner 포함 6명을 채운다. 서비스 경유가 아니라 직접 넣는다 —
        여기서 검증할 건 '7번째 가입 거절'이지 가입 경로 자체가 아니다."""
        for i in range(shared_service.MAX_MEMBERS - 1):
            filler = User.objects.create_user(
                username=f"filler{i}", email=f"filler{i}@test.com", password="password"
            )
            SharedWardrobeMember.objects.create(
                room=self.room, user=filler, role=SharedWardrobeMember.Role.MEMBER
            )

    def test_seventh_member_is_rejected(self):
        """정원(6명)이 찬 방은 유효한 초대코드로도 못 들어온다."""
        self._fill_room_to_capacity()
        self.assertEqual(self.room.members.count(), shared_service.MAX_MEMBERS)

        with self.assertRaises(ValueError) as ctx:
            shared_service.join_shared_room(self.member, self.room.invite_code)
        self.assertIn("정원", str(ctx.exception))
        # 거절이면 멤버로 추가되지 않아야 한다
        self.assertEqual(self.room.members.count(), shared_service.MAX_MEMBERS)

    def test_seventh_member_rejected_via_api(self):
        """API 경로로도 400 + 서버 문구가 그대로 내려간다 (프론트가 이 문구를 토스트로 띄운다)."""
        self._fill_room_to_capacity()
        self.client.force_authenticate(self.member)

        res = self.client.post(
            "/api/v1/shared-wardrobes/join/",
            {"invite_code": self.room.invite_code},
            format="json",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("정원", str(res.data))

    def test_rename_by_any_member(self):
        """방 이름은 멤버 누구나 바꿀 수 있다 (2026-08-16 팀 결정 — 변경 주체는 id 로 남는다).
        단, 멤버가 아니면 get_queryset 에서 걸러져 404 다."""
        shared_service.join_shared_room(self.member, self.room.invite_code)
        self.client.force_authenticate(self.member)

        res = self.client.patch(
            f"/api/v1/shared-wardrobes/{self.room.pk}/", {"title": "멤버가 수정"}, format="json"
        )

        self.assertEqual(res.status_code, 200)
        self.room.refresh_from_db()
        self.assertEqual(self.room.title, "멤버가 수정")

        # 비멤버는 접근 자체가 안 된다
        outsider = User.objects.create_user(
            username="outsider", email="outsider@test.com", password="password"
        )
        self.client.force_authenticate(outsider)
        res = self.client.patch(
            f"/api/v1/shared-wardrobes/{self.room.pk}/", {"title": "외부인"}, format="json"
        )
        self.assertEqual(res.status_code, 404)

    def test_title_over_10_chars_is_400(self):
        """방 이름은 10글자 이내 — 생성·수정 모두 같은 문구로 거른다."""
        self.client.force_authenticate(self.owner)

        res = self.client.post(
            "/api/v1/shared-wardrobes/", {"title": "가" * 11}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("10글자", str(res.data))

        res = self.client.patch(
            f"/api/v1/shared-wardrobes/{self.room.pk}/", {"title": "가" * 11}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("10글자", str(res.data))
        # 10글자는 통과한다 (경계값)
        res = self.client.patch(
            f"/api/v1/shared-wardrobes/{self.room.pk}/", {"title": "가" * 10}, format="json"
        )
        self.assertEqual(res.status_code, 200)

    def test_malformed_uuid_is_not_500(self):
        """UUID 자리에 아무 문자열 — 전 엔드포인트가 4xx 로 떨어져야 한다."""
        self.client.force_authenticate(self.owner)
        cases = [
            ("get", "/api/v1/shared-wardrobes/not-a-uuid/items/", None),
            ("post", "/api/v1/shared-wardrobes/not-a-uuid/leave/", {}),
            ("post", "/api/v1/shared-wardrobes/not-a-uuid/refresh-code/", {}),
            (
                "patch",
                f"/api/v1/shared-wardrobes/{self.room.pk}/items/",
                {"item_id": "not-a-uuid", "status": "available"},
            ),
        ]
        for method, url, body in cases:
            res = getattr(self.client, method)(url, body, format="json")
            self.assertLess(
                res.status_code, 500,
                f"{method.upper()} {url} → {res.status_code} (500 이면 UUID 검증 누락)",
            )

    def test_concurrent_last_two_leave_no_ghost_room(self):
        """마지막 두 멤버가 차례로 나가면 방이 반드시 사라진다.

        (진짜 동시성은 단일 커넥션 테스트로 재현이 어려워, 잠금이 걸린 상태에서
        순차 실행되는 결과가 올바른지를 본다 — select_for_update 가 직렬화를 보장한다.)
        """
        shared_service.join_shared_room(self.member, self.room.invite_code)
        shared_service.leave_shared_room(self.owner, str(self.room.pk))
        shared_service.leave_shared_room(self.member, str(self.room.pk))
        self.assertFalse(SharedWardrobeRoom.objects.filter(pk=self.room.pk).exists())

    def test_room_delete_never_touches_personal_items(self):
        """방 삭제는 delete_personal_items=true 로도 개인 옷장 원본을 못 지운다 (2026-08-16 정책)."""
        item = WardrobeItem.objects.create(
            user=self.owner, s3_key="k", category_large="상의", item_name="원본",
            confirmed=True,
        )
        shared_service.register_item_to_shared_room(self.owner, str(self.room.pk), str(item.pk))
        self.client.force_authenticate(self.owner)

        res = self.client.delete(
            f"/api/v1/shared-wardrobes/{self.room.pk}/?delete_personal_items=true"
        )

        self.assertEqual(res.status_code, 204)
        self.assertFalse(SharedWardrobeRoom.objects.filter(pk=self.room.pk).exists())
        # 원본은 반드시 살아 있어야 한다
        self.assertTrue(WardrobeItem.objects.filter(pk=item.pk).exists())
