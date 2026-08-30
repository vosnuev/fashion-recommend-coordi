import random
import string
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction
from apps.wardrobe.models import SharedWardrobeRoom, SharedWardrobeMember, SharedWardrobeItem, WardrobeItem

# 한 공유방의 최대 인원. preview 응답의 capacity도 이 값을 참조한다 —
# 리터럴 6을 여러 곳에 두면 정원 정책이 바뀔 때 화면과 서버가 어긋난다.
MAX_MEMBERS = 6


def generate_unique_invite_code() -> str:
    """영문 대문자와 숫자가 혼합된 고유한 6자리 핀코드를 생성합니다."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(random.choices(chars, k=6))
        if not SharedWardrobeRoom.objects.filter(invite_code=code).exists():
            return code
    return "".join(random.choices(chars, k=6))


@transaction.atomic
def create_shared_room(user, title: str) -> SharedWardrobeRoom:
    """새로운 공유 옷장 방을 개설하고 생성자를 방장(owner)으로 자동 배정합니다."""
    code = generate_unique_invite_code()
    expires_at = timezone.now() + timedelta(hours=24)
    
    room = SharedWardrobeRoom.objects.create(
        title=title,
        invite_code=code,
        code_expires_at=expires_at
    )
    
    SharedWardrobeMember.objects.create(
        room=room,
        user=user,
        role=SharedWardrobeMember.Role.OWNER
    )
    
    return room


@transaction.atomic
def refresh_invite_code(user, room_id: str) -> SharedWardrobeRoom:
    """방의 기존 초대코드를 만료시키고, 24시간 동안 유효한 새 초대코드를 발급합니다.
    방장(owner)만 이 코드를 재발급할 수 있습니다.
    """
    room = SharedWardrobeRoom.objects.select_for_update().get(pk=room_id)
    
    # 방장 권한 체크
    member = SharedWardrobeMember.objects.filter(room=room, user=user).first()
    if not member or member.role != SharedWardrobeMember.Role.OWNER:
        raise PermissionError("초대코드 재발급 권한이 없습니다. 방장만 재발급할 수 있습니다.")
        
    code = generate_unique_invite_code()
    expires_at = timezone.now() + timedelta(hours=24)
    
    room.invite_code = code
    room.code_expires_at = expires_at
    room.save(update_fields=["invite_code", "code_expires_at"])
    return room


@transaction.atomic
def join_shared_room(user, invite_code: str) -> tuple[SharedWardrobeRoom, bool]:
    """6자리 초대코드를 입력하여 공유 옷장 방에 신규 참여(가입)합니다.

    초대코드의 24시간 만료 시간 체크가 적용됩니다.

    Returns: (방, 이번에 새로 가입했는지). 이미 멤버면 `(방, False)` —
        가입 실패가 아니라 "그냥 입장"이다.
    Raises: ValueError — 코드가 없거나 만료됐거나 정원이 찬 경우.
        이때는 **멤버로 추가되지 않는다.**
    """
    code = invite_code.strip().upper()
    room = SharedWardrobeRoom.objects.select_for_update().filter(invite_code=code).first()
    
    if not room:
        raise ValueError("유효하지 않은 초대코드입니다.")
        
    # 만료 여부 체크
    if room.code_expires_at and room.code_expires_at < timezone.now():
        raise ValueError("초대코드가 24시간 만료 시간을 초과하여 사용할 수 없습니다. 방장에게 재발급을 요청하세요.")
        
    # 이미 참여 중인지 체크. 에러는 아니지만 "새로 들어왔다"와는 다른 사건이라
    # 호출부가 구분할 수 있게 플래그로 알린다 — 화면이 같은 문구를 띄우면
    # 사용자는 방금 가입에 성공했다고 오해한다.
    if SharedWardrobeMember.objects.filter(room=room, user=user).exists():
        return room, False

    # 인원 제한 체크
    if room.members.count() >= MAX_MEMBERS:
        raise ValueError(f"공유 옷장 정원(최대 {MAX_MEMBERS}명)이 꽉 차서 참여할 수 없습니다.")

    # 멤버십 참여 등록
    SharedWardrobeMember.objects.create(
        room=room,
        user=user,
        role=SharedWardrobeMember.Role.MEMBER
    )
    return room, True


@transaction.atomic
def leave_shared_room(user, room_id: str, delete_my_items: bool = True) -> None:
    """공유 옷장 방을 자발적으로 탈퇴(퇴장)합니다.
    
    - 방장이 나갈 시:
      - 남은 멤버 중 가입일시가 가장 빠른 다른 유저에게 방장(owner) 권한을 자동으로 위임합니다.
      - 방 안에 더이상 남은 유저가 0명이면 방을 폐쇄(Delete) 처리합니다.
    - 아이템 처리:
      - delete_my_items 가 True이면 사용자가 해당 공유 옷장에 기여한 옷들을 일괄 삭제합니다.
      - False이면 등록자 연결만 NULL 로 바꿔 방에 남깁니다("기부").
        ⚠️ 기부는 원본이 살아 있는 동안만 유지된다 — 공유 행은 원본 WardrobeItem 을
        CASCADE 로 따라가므로, 탈퇴자가 나중에 개인 옷장에서 그 옷을 지우면 방에서도
        사라진다. 원본과 무관한 영속 기부가 필요해지면 이미지·메타 복제가 선행돼야 한다.
    """
    try:
        # select_for_update: 마지막 두 멤버가 동시에 탈퇴하면 둘 다 "남은 인원 1명"을
        # 보고 아무도 방을 지우지 않아, 조회는 안 되는데 초대코드로는 들어와지는
        # 유령 방이 남는다. join/refresh 와 같은 방 행 잠금으로 직렬화한다.
        room = SharedWardrobeRoom.objects.select_for_update().get(pk=room_id)
        membership = SharedWardrobeMember.objects.get(room=room, user=user)
    except (SharedWardrobeRoom.DoesNotExist, SharedWardrobeMember.DoesNotExist, ValidationError):
        # ValidationError: room_id 가 UUID 형식이 아닐 때 — 없는 방과 같게 취급한다(500 방지)
        raise ValueError("참여하고 있지 않은 공유 옷장 방입니다.")

    # 1. 탈퇴자 등록 옷 처리 분기
    if delete_my_items:
        # 내가 등록한 옷 완전 삭제
        SharedWardrobeItem.objects.filter(room=room, registered_by=user).delete()
    else:
        # 옷은 그대로 두고 등록자 연관관계만 NULL 처리하여 기부 유지
        SharedWardrobeItem.objects.filter(room=room, registered_by=user).update(registered_by=None)

    # 2. 멤버십 탈퇴
    is_owner = (membership.role == SharedWardrobeMember.Role.OWNER)
    membership.delete()

    # 3. 방장 퇴장 처리 및 방 유지/위임/폭파 연산
    remaining_members = SharedWardrobeMember.objects.filter(room=room).order_by("joined_at")
    remaining_count = remaining_members.count()

    if remaining_count == 0:
        # 남은 인원이 없으면 방 완전 폭파
        room.delete()
    else:
        # 방장이 나갔고 남은 인원이 있으면 가입 순서가 가장 빠른 사람에게 위임
        if is_owner:
            next_owner = remaining_members.first()
            if next_owner:
                next_owner.role = SharedWardrobeMember.Role.OWNER
                next_owner.save(update_fields=["role"])


@transaction.atomic
def register_item_to_shared_room(user, room_id: str, wardrobe_item_id: str) -> SharedWardrobeItem:
    """개인 옷장에서 보유하고 있는 내 옷(confirmed=True인 옷만 가능)을 공유 옷장에 정식으로 등록(공유)합니다."""
    try:
        room = SharedWardrobeRoom.objects.get(pk=room_id)
        # 방 참여자인지 확인
        SharedWardrobeMember.objects.get(room=room, user=user)
    except (SharedWardrobeRoom.DoesNotExist, SharedWardrobeMember.DoesNotExist, ValidationError):
        raise ValueError("공유 옷장 참여 멤버만 옷을 공유할 수 있습니다.")

    try:
        wardrobe_item = WardrobeItem.objects.get(
            pk=wardrobe_item_id,
            user=user,
            confirmed=True,
        )
    except (WardrobeItem.DoesNotExist, ValidationError):
        raise ValueError("내 개인 옷장에서 사용자가 확정한 옷만 공유할 수 있습니다.")

    shared_item, _ = SharedWardrobeItem.objects.get_or_create(
        room=room,
        wardrobe_item=wardrobe_item,
        defaults={"registered_by": user},
    )
    return shared_item


def is_room_member(user, room_id: str) -> bool:
    """이 사용자가 지금 그 방의 멤버인가. 공유 예약을 받아 줄지 판단하는 데 쓴다."""
    if not room_id:
        return False
    try:
        return SharedWardrobeMember.objects.filter(room_id=room_id, user=user).exists()
    except ValidationError:
        return False  # UUID 형식이 아닌 값 — 멤버 아님과 같게 취급한다(500 방지)


@transaction.atomic
def redeem_pending_share(item: WardrobeItem) -> SharedWardrobeItem | None:
    """확정된 옷의 공유 예약을 소진한다. 확정 처리(PATCH confirmed=true) 직후에 부른다.

    원자성 규칙 — "비우기"와 "등록"은 한 트랜잭션이다:
      - 등록 성공          → 예약 비움 + 공유 행 생성이 같이 커밋
      - ValueError(방 나감/방 삭제) → 되살릴 수 없는 예약이므로 비움만 커밋
      - 그 밖의 예외(DB 오류 등)   → 전체 롤백. **예약이 살아남아 재시도가 가능하다.**
    데코레이터 없이 비우기를 먼저 저장하면 등록이 죽는 순간 예약도 같이 증발해,
    일시적 DB 오류 한 번에 사용자가 켠 토글이 영영 사라진다.

    호출부(확정 API)는 이 함수가 None 을 주면 '공유는 못 했지만 확정은 성공'으로 응답한다.
    """
    room_id = item.pending_share_room_id
    if not room_id:
        return None

    item.pending_share_room = None
    # 공유 상태 기능은 없어졌지만, 레거시 행에 남은 예약 상태는 여기서 비운다.
    item.pending_share_status = ""
    item.save(update_fields=["pending_share_room", "pending_share_status"])

    try:
        # register_* 도 atomic 이라 여기서는 savepoint 로 중첩된다 —
        # ValueError 로 빠져도 바깥(예약 비우기)은 살아서 커밋된다.
        return register_item_to_shared_room(item.user, str(room_id), str(item.pk))
    except ValueError:
        # 방을 나갔거나 방이 지워진 뒤 확정한 경우. 확정 자체를 실패시키지 않는다.
        return None
