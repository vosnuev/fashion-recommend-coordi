"""룩북 등록·조회 API.

플로우(사진 등록)는 캘린더와 같다.
  ① multipart 업로드 → ② 룩북 S3 선업로드 → ③ 옷장 job 생성(PENDING)
  → ④ 큐 enqueue(exclude_categories 포함) → ⑤ 202
  ... ⑨ 옷장 callback → ⑩ 룩북에 아이템 자동 연결(COMPLETED)

옷장 직접 선택 등록은 비동기 단계가 없어 곧바로 201 + COMPLETED다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import redis as redis_lib
from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404, HttpResponse
from django.conf import settings
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.lookbook.serializers import (
    DiscoveryLookQuerySerializer,
    LookbookListQuerySerializer,
    LookbookMetadataUpdateSerializer,
    LookbookPhotoCreateSerializer,
    LookbookPostSerializer,
    LookbookProcessingStatusSerializer,
    LookbookWardrobeCreateSerializer,
)
from apps.lookbook.services import cover_image, discovery, lookbook_service
from apps.wardrobe.services import jobs as wardrobe_jobs

logger = logging.getLogger(__name__)


class DiscoveryLookListView(APIView):
    """GET /api/v1/lookbooks/discover/ — 네이버 상품 기반 공개 룩 피드."""

    permission_classes = (AllowAny,)

    def get(self, request):
        serializer = DiscoveryLookQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        params = discovery.DiscoveryQuery(**serializer.validated_data)
        return Response(discovery.list_looks(params))


class DiscoveryLookDetailView(APIView):
    """GET /api/v1/lookbooks/discover/{id}/ — 구성 아이템과 가격 비교 후보."""

    permission_classes = (AllowAny,)

    def get(self, request, look_id: str):
        look = discovery.get_look(look_id)
        if look is None:
            return Response(
                {"detail": "룩을 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(look)


class DiscoveryLookCoverView(APIView):
    """운영 CSV가 가리키는 로컬 전신사진을 개발·배포 환경에 동일하게 제공한다.

    `?w=400|800`을 주면 그 폭의 JPEG 축소본을 준다. 원본은 1080x1350 PNG(장당 약 2MB)라
    목록에서 그대로 받으면 한 화면에 수십 MB가 된다. **파라미터가 없으면 원본 그대로**라
    기존 호출은 달라지지 않는다. 자세한 배경은 services/cover_image.py 참고.
    """

    permission_classes = (AllowAny,)

    def get(self, request, external_id: str):
        from apps.lookbook.models import CuratedLook

        look = get_object_or_404(CuratedLook, external_id=external_id, is_active=True)
        root = Path(settings.BASE_DIR).parent / "data" / "lookbook"
        path = (root / look.cover_image_url).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise Http404

        width = cover_image.requested_width(request.query_params.get("w"))
        if width is None:
            return FileResponse(path.open("rb"), content_type="image/png")

        data = cover_image.cached_thumbnail(
            path, width, root / cover_image.THUMB_DIR, external_id
        )
        response = HttpResponse(data, content_type="image/jpeg")
        # 축소본은 원본이 바뀌지 않는 한 같은 바이트다 — 브라우저가 다시 받지 않게 한다.
        response["Cache-Control"] = "public, max-age=86400"
        return response


def _creation_error_response(error: Exception) -> Response | None:
    """등록 계열 API가 공유하는 도메인 오류 → HTTP 응답 매핑."""

    if isinstance(error, lookbook_service.WardrobeItemsNotFoundError):
        return Response(
            {
                "wardrobe_item_ids": [
                    "존재하지 않거나 사용자 소유가 아닌 옷장 아이템이 포함되어 있습니다."
                ]
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(error, lookbook_service.CalendarDateConflictError):
        # 프론트는 여기서 '이 룩으로 그날 기록을 바꿀까요?'를 물은 뒤
        # overwrite_calendar=true로 같은 요청을 다시 보낸다.
        return Response(
            {
                "calendar_date": ["해당 날짜의 캘린더가 이미 존재합니다."],
                "code": "CALENDAR_DATE_CONFLICT",
                "date": error.entry_date.isoformat(),
            },
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(error, lookbook_service.CalendarBusyError):
        return Response(
            {
                "detail": "이미지 처리 중인 캘린더는 교체할 수 없습니다.",
                "code": "CALENDAR_BUSY",
                "status": error.current_status,
            },
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(error, lookbook_service.LookbookStorageError):
        return Response(
            {"detail": "룩북 이미지 저장에 실패했습니다. 잠시 후 다시 시도해주세요."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return None


class LookbookPhotoCreateView(APIView):
    """POST /api/v1/lookbooks/photo/ — 룩 사진 룩북 선등록."""

    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        serializer = LookbookPhotoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            post = lookbook_service.create_from_photo(
                user=request.user,
                image=data["image"],
                wardrobe_item_ids=data["wardrobe_item_ids"],
                schedule=data["schedule"],
                tpo=data["tpo"],
                hashtags=data["hashtags"],
                calendar_date=data["calendar_date"],
                overwrite_calendar=data["overwrite_calendar"],
                is_public=data["is_public"],
            )
        except Exception as error:
            response = _creation_error_response(error)
            if response is None:
                raise
            return response

        try:
            wardrobe_jobs.enqueue(
                post.wardrobe_upload_job,
                # 입은 옷으로 이미 지정한 부위는 사진에서 다시 뽑지 않는다.
                exclude_categories=post.skipped_categories,
            )
        except redis_lib.RedisError:
            logger.exception(
                "옷장 Queue 적재 실패: lookbook_id=%s job_id=%s",
                post.pk,
                post.wardrobe_upload_job_id,
            )
            lookbook_service.mark_queue_enqueue_failed(post)
            return Response(
                {
                    "detail": "처리 대기열 등록에 실패했습니다. 잠시 후 다시 시도해주세요.",
                    "id": str(post.pk),
                    "status": post.status,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            LookbookPostSerializer(post).data,
            status=status.HTTP_202_ACCEPTED,
        )


class LookbookWardrobeCreateView(APIView):
    """POST /api/v1/lookbooks/wardrobe/ — 옷장 아이템 직접 선택 등록."""

    def post(self, request):
        serializer = LookbookWardrobeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            post = lookbook_service.create_from_wardrobe(
                user=request.user,
                wardrobe_item_ids=data["wardrobe_item_ids"],
                schedule=data["schedule"],
                tpo=data["tpo"],
                hashtags=data["hashtags"],
                calendar_date=data["calendar_date"],
                overwrite_calendar=data["overwrite_calendar"],
                is_public=data["is_public"],
            )
        except Exception as error:
            response = _creation_error_response(error)
            if response is None:
                raise
            return response

        return Response(
            LookbookPostSerializer(post).data,
            status=status.HTTP_201_CREATED,
        )


class LookbookListView(APIView):
    """GET /api/v1/lookbooks/?hashtag=&status=&limit=&offset= — 내 룩북 목록."""

    def get(self, request):
        query = LookbookListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        queryset = lookbook_service.posts_filtered(
            user=request.user,
            hashtag=params["hashtag"],
            status=params["status"],
        )
        # 피드는 계속 자란다. 전체를 내려 주면 앱이 스크롤 한 번에 수백 건을
        # 받아 presigned URL도 그만큼 만들게 되므로 항상 잘라서 준다.
        total = queryset.count()
        offset = params["offset"]
        limit = params["limit"]
        page = list(queryset[offset : offset + limit])
        next_offset = offset + limit if offset + limit < total else None

        return Response(
            {
                "count": total,
                "next_offset": next_offset,
                "results": LookbookPostSerializer(page, many=True).data,
            }
        )


class LookbookPublicFeedView(APIView):
    """GET /api/v1/lookbooks/public/?hashtag=&limit=&offset= — 전체 공개 룩 피드.

    앱의 '둘러보기'가 읽는 목록이다. 남의 룩이라 로그인 없이도 볼 수 있게 열어 둔다
    (비회원도 둘러보기까지는 들어온다).
    """

    permission_classes = (AllowAny,)

    def get(self, request):
        query = LookbookListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data

        queryset = lookbook_service.public_posts(hashtag=params["hashtag"])
        total = queryset.count()
        offset = params["offset"]
        limit = params["limit"]
        page = list(queryset[offset : offset + limit])
        next_offset = offset + limit if offset + limit < total else None

        return Response(
            {
                "count": total,
                "next_offset": next_offset,
                "results": LookbookPostSerializer(page, many=True).data,
            }
        )


class LookbookDetailView(APIView):
    """내 룩북 상세 조회·메타데이터 수정·삭제."""

    @staticmethod
    def _get_post(*, user, lookbook_id):
        return get_object_or_404(
            lookbook_service.posts_for_user(user=user),
            pk=lookbook_id,
        )

    def get(self, request, lookbook_id):
        post = self._get_post(user=request.user, lookbook_id=lookbook_id)
        return Response(LookbookPostSerializer(post).data)

    def patch(self, request, lookbook_id):
        """PATCH /api/v1/lookbooks/{lookbook_id}/ — 일정·TPO·해시태그 수정."""

        post = self._get_post(user=request.user, lookbook_id=lookbook_id)
        serializer = LookbookMetadataUpdateSerializer(
            post,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        post = serializer.save()
        return Response(LookbookPostSerializer(post).data)

    def delete(self, request, lookbook_id):
        """DELETE /api/v1/lookbooks/{lookbook_id}/ — 종료된 룩북 삭제."""

        try:
            lookbook_service.delete_post(
                user=request.user,
                lookbook_id=lookbook_id,
            )
        except lookbook_service.LookbookNotFoundError:
            return Response(
                {"detail": "룩북을 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except lookbook_service.LookbookDeletionConflictError as exc:
            return Response(
                {"detail": str(exc), "status": exc.current_status},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class LookbookProcessingStatusView(APIView):
    """GET /api/v1/lookbooks/{lookbook_id}/processing-status/ — 프론트 폴링용."""

    def get(self, request, lookbook_id):
        post = get_object_or_404(
            lookbook_service.processing_statuses_for_user(user=request.user),
            pk=lookbook_id,
        )
        return Response(LookbookProcessingStatusSerializer(post).data)
