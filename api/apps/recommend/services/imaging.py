"""LLM 전송용 이미지 축소.

왜 필요한가: 휴대폰 원본 사진(수 MB, 4000px)을 그대로 base64로 실어 보내면
업로드만으로 수십 초가 걸려 Gemini 호출이 타임아웃한다. 코디 평가는 옷의 종류·
색·핏·실루엣을 보는 작업이라 1024px면 충분하다 (Gemini도 내부적으로 타일 단위로
축소해 처리한다).

원본은 S3에 그대로 보관하므로 여기서 줄이는 것은 **전송본**뿐이다.
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# 긴 변 기준 최대 픽셀. 코디 평가에 필요한 디테일(패턴·소재감)은 이 정도면 남는다.
MAX_EDGE_PX = 1024
JPEG_QUALITY = 85
JPEG_MIME = "image/jpeg"


def shrink_for_llm(
    image_data: bytes,
    *,
    mime_type: str,
    max_edge: int = MAX_EDGE_PX,
) -> tuple[bytes, str]:
    """전송용으로 축소한 (바이트, MIME) 반환.

    축소가 불가능하거나(디코딩 실패) 오히려 커지면 원본을 그대로 돌려준다.
    즉 이 함수는 실패하지 않는다 — 평가를 막을 이유가 없는 최적화이기 때문이다.
    """
    try:
        with Image.open(BytesIO(image_data)) as image:
            # 휴대폰 사진은 EXIF Orientation으로 회전 정보를 들고 있다.
            # 리사이즈하며 EXIF가 날아가므로 픽셀에 먼저 반영해야 눕지 않는다.
            image = ImageOps.exif_transpose(image)
            resized = max(image.size) > max_edge
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.LANCZOS)

            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception:  # noqa: BLE001 — 축소 실패는 평가를 막지 않는다
        logger.warning("전송용 이미지 축소 실패, 원본을 그대로 보낸다", exc_info=True)
        return image_data, mime_type

    shrunk = buffer.getvalue()
    # 해상도를 실제로 줄였다면 바이트가 늘어도 축소본을 쓴다. 전송량뿐 아니라
    # 모델이 처리할 픽셀 수도 줄이는 것이 목적이기 때문이다 (잘 압축되는 PNG가
    # 4000px 그대로 넘어가는 것을 막는다).
    if resized:
        return shrunk, JPEG_MIME
    if len(shrunk) >= len(image_data):
        # 이미 충분히 작은 사진 — 재압축이 손해다
        return image_data, mime_type
    return shrunk, JPEG_MIME
