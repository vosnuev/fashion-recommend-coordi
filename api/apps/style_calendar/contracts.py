"""캘린더 API와 이미지 프로세서가 공유하는 계약 상수."""

from enum import StrEnum


class CalendarStatus(StrEnum):
    """캘린더 이미지 처리 상태."""

    REGISTERED = "REGISTERED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CalendarSourceType(StrEnum):
    """캘린더 등록 경로."""

    PHOTO_UPLOAD = "PHOTO_UPLOAD"
    WARDROBE_SELECTED = "WARDROBE_SELECTED"


class CalendarProcessingErrorCode(StrEnum):
    """캘린더 이미지 처리의 표준 전체 실패 코드."""

    QUEUE_ENQUEUE_FAILED = "QUEUE_ENQUEUE_FAILED"
    NO_ITEM_EXTRACTED = "NO_ITEM_EXTRACTED"
    IMAGE_PROCESSING_FAILED = "IMAGE_PROCESSING_FAILED"
