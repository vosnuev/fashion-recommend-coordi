"""스타일 캘린더 조회 API."""

from __future__ import annotations

import logging

import redis as redis_lib
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.style_calendar.serializers import (
    CalendarDateQuerySerializer,
    CalendarEntrySerializer,
    CalendarMetadataUpdateSerializer,
    CalendarPeriodQuerySerializer,
    CalendarPhotoCreateSerializer,
    CalendarProcessingStatusSerializer,
    CalendarWardrobeCreateSerializer,
    CalendarWardrobeItemLinkSerializer,
)
from apps.style_calendar.services import calendar_service
from apps.wardrobe.services import jobs as wardrobe_jobs

logger = logging.getLogger(__name__)


class CalendarPhotoCreateView(APIView):
    """POST /api/v1/calendars/photo/ — 사용자 사진 캘린더 선등록."""

    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        serializer = CalendarPhotoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            entry = calendar_service.create_from_photo(
                user=request.user,
                image=data["image"],
                entry_date=data["date"],
                wardrobe_item_ids=data["wardrobe_item_ids"],
                schedule=data["schedule"],
                tpo=data["tpo"],
                hashtags=data["hashtags"],
            )
        except calendar_service.WardrobeItemsNotFoundError:
            return Response(
                {
                    "wardrobe_item_ids": [
                        "존재하지 않거나 사용자 소유가 아닌 옷장 아이템이 포함되어 있습니다."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except calendar_service.DuplicateCategorySlotError as exc:
            return Response(
                {
                    "wardrobe_item_ids": [
                        f"'{exc.slot_key}' 카테고리 항목은 캘린더 착장당 1개만 선택할 수 있습니다."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except calendar_service.CalendarDateConflictError:
            return Response(
                {"date": ["해당 날짜의 캘린더가 이미 존재합니다."]},
                status=status.HTTP_409_CONFLICT,
            )
        except calendar_service.CalendarStorageError:
            return Response(
                {"detail": "캘린더 이미지 저장에 실패했습니다. 잠시 후 다시 시도해주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            wardrobe_jobs.enqueue(
                entry.wardrobe_upload_job,
                # 입은 옷으로 이미 지정한 부위는 사진에서 다시 뽑지 않는다 — 뽑으면
                # 같은 옷이 옷장에 한 벌 더 생긴다 (룩북 등록과 같은 규칙).
                exclude_categories=entry.skipped_categories,
            )
        except redis_lib.RedisError:
            logger.exception(
                "옷장 Queue 적재 실패: calendar_id=%s job_id=%s",
                entry.pk,
                entry.wardrobe_upload_job_id,
            )
            calendar_service.mark_queue_enqueue_failed(entry)
            return Response(
                {
                    "detail": "처리 대기열 등록에 실패했습니다. 잠시 후 다시 시도해주세요.",
                    "id": str(entry.pk),
                    "status": entry.status,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            CalendarEntrySerializer(entry).data,
            status=status.HTTP_202_ACCEPTED,
        )


class CalendarWardrobeCreateView(APIView):
    """POST /api/v1/calendars/wardrobe/ — 옷장 아이템 직접 선택 등록."""

    def post(self, request):
        serializer = CalendarWardrobeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            entry = calendar_service.create_from_wardrobe(
                user=request.user,
                entry_date=data["date"],
                wardrobe_item_ids=data["wardrobe_item_ids"],
                schedule=data["schedule"],
                tpo=data["tpo"],
                hashtags=data["hashtags"],
            )
        except calendar_service.WardrobeItemsNotFoundError:
            return Response(
                {
                    "wardrobe_item_ids": [
                        "존재하지 않거나 사용자 소유가 아닌 옷장 아이템이 포함되어 있습니다."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except calendar_service.DuplicateCategorySlotError as exc:
            return Response(
                {
                    "wardrobe_item_ids": [
                        f"'{exc.slot_key}' 카테고리 항목은 캘린더 착장당 1개만 선택할 수 있습니다."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except calendar_service.CalendarDateConflictError:
            return Response(
                {"date": ["해당 날짜의 캘린더가 이미 존재합니다."]},
                status=status.HTTP_409_CONFLICT,
            )
        except calendar_service.CalendarStorageError:
            return Response(
                {"detail": "캘린더 이미지 저장에 실패했습니다. 잠시 후 다시 시도해주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            CalendarEntrySerializer(entry).data,
            status=status.HTTP_201_CREATED,
        )


class CalendarEntryListView(APIView):
    """GET /api/v1/calendars/?start_date=&end_date= — 기간별 내 캘린더."""

    def get(self, request):
        query = CalendarPeriodQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        entries = calendar_service.entries_in_period(
            user=request.user,
            start_date=query.validated_data["start_date"],
            end_date=query.validated_data["end_date"],
        )
        return Response(CalendarEntrySerializer(entries, many=True).data)


class CalendarEntryByDateView(APIView):
    """GET /api/v1/calendars/by-date/?date= — 특정 날짜의 내 캘린더."""

    def get(self, request):
        query = CalendarDateQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        entry = get_object_or_404(
            calendar_service.entries_for_user(user=request.user),
            date=query.validated_data["date"],
        )
        return Response(CalendarEntrySerializer(entry).data)


class CalendarEntryDetailView(APIView):
    """내 캘린더 상세 조회·메타데이터 수정·삭제."""

    @staticmethod
    def _get_entry(*, user, calendar_id):
        return get_object_or_404(
            calendar_service.entries_for_user(user=user),
            pk=calendar_id,
        )

    def get(self, request, calendar_id):
        entry = self._get_entry(user=request.user, calendar_id=calendar_id)
        return Response(CalendarEntrySerializer(entry).data)

    def patch(self, request, calendar_id):
        """PATCH /api/v1/calendars/{calendar_id}/ — 일정·TPO·해시태그 수정."""

        entry = self._get_entry(user=request.user, calendar_id=calendar_id)
        serializer = CalendarMetadataUpdateSerializer(
            entry,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        entry = serializer.save()
        return Response(CalendarEntrySerializer(entry).data)

    def delete(self, request, calendar_id):
        """DELETE /api/v1/calendars/{calendar_id}/ — 종료된 캘린더 삭제."""

        try:
            calendar_service.delete_entry(
                user=request.user,
                calendar_id=calendar_id,
            )
        except calendar_service.CalendarDeletionNotFoundError:
            return Response(
                {"detail": "캘린더를 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except calendar_service.CalendarDeletionConflictError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "status": exc.current_status,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class CalendarWardrobeItemLinkView(APIView):
    """POST /api/v1/calendars/{calendar_id}/items/ — 입은 옷을 더한다.

    unlink 의 반대편이다. 이것이 없으면 옷 하나를 더하려고 기록을 지우고 다시
    만들어야 하는데, 사진 기록에서는 그것이 곧 같은 사진의 재분석이라 같은 옷이
    옷장에 한 벌 더 생긴다. 응답은 갱신된 캘린더 전체다(unlink 와 같다).
    """

    def post(self, request, calendar_id):
        serializer = CalendarWardrobeItemLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            entry = calendar_service.link_wardrobe_items(
                user=request.user,
                calendar_id=calendar_id,
                wardrobe_item_ids=serializer.validated_data["wardrobe_item_ids"],
            )
        except calendar_service.CalendarDeletionNotFoundError:
            return Response(
                {"detail": "캘린더를 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except calendar_service.WardrobeItemsNotFoundError:
            return Response(
                {
                    "wardrobe_item_ids": [
                        "존재하지 않거나 사용자 소유가 아닌 옷장 아이템이 포함되어 있습니다."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except calendar_service.DuplicateCategorySlotError as exc:
            return Response(
                {
                    "wardrobe_item_ids": [
                        f"'{exc.slot_key}' 카테고리 항목은 캘린더 착장당 1개만 선택할 수 있습니다."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except calendar_service.CalendarDeletionConflictError as exc:
            return Response(
                {
                    "detail": "이미지 처리 중인 캘린더는 수정할 수 없습니다.",
                    "status": exc.current_status,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except calendar_service.CalendarStorageError:
            return Response(
                {"detail": "캘린더 이미지 저장에 실패했습니다. 잠시 후 다시 시도해주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(CalendarEntrySerializer(entry).data)


class CalendarWardrobeItemUnlinkView(APIView):
    """DELETE /api/v1/calendars/{calendar_id}/items/{wardrobe_item_id}/.

    캘린더에서 입은 옷 하나를 뺀다 — 지워지는 것은 **연결(calendar_wardrobe_item)
    한 행**뿐이다. 옷장 아이템(wardrobe_item)과 캘린더 기록은 그대로 남는다.
    응답으로 갱신된 캘린더 전체를 돌려줘, 프론트가 다시 조회하지 않고 화면을
    맞출 수 있게 한다.
    """

    def delete(self, request, calendar_id, wardrobe_item_id):
        try:
            entry = calendar_service.unlink_wardrobe_item(
                user=request.user,
                calendar_id=calendar_id,
                wardrobe_item_id=wardrobe_item_id,
            )
        except calendar_service.CalendarDeletionNotFoundError:
            return Response(
                {"detail": "캘린더를 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except calendar_service.CalendarItemLinkNotFoundError:
            return Response(
                {"detail": "이 캘린더에 연결된 옷장 아이템이 아닙니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except calendar_service.CalendarDeletionConflictError as exc:
            return Response(
                {
                    "detail": "이미지 처리 중인 캘린더는 수정할 수 없습니다.",
                    "status": exc.current_status,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(CalendarEntrySerializer(entry).data)


class CalendarProcessingStatusView(APIView):
    """GET /api/v1/calendars/{calendar_id}/processing-status/."""

    def get(self, request, calendar_id):
        entry = get_object_or_404(
            calendar_service.processing_statuses_for_user(user=request.user),
            pk=calendar_id,
        )
        return Response(CalendarProcessingStatusSerializer(entry).data)
