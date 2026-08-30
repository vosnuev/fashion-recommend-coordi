"""둘러보기 룩 커버 — 목록용 축소본 생성·캐시.

왜 필요한가: 원본은 1080x1350 PNG 로 장당 평균 2MB 다. 룩북 목록의 카드 한 칸은
170pt 남짓인데 거기에 원본을 그대로 내려주면 한 화면에 수십 MB 를 받게 되고,
폰에서는 수십 초 동안 빈 칸으로 남아 '사진이 안 뜬다'로 보인다.

같은 사진을 JPEG 로 줄이면 장당 100KB 안팎이 된다 — 사진이라 무손실이 필요 없고
투명도도 쓰지 않는다. **원본 응답은 그대로 두고 `?w=` 가 올 때만** 축소본을 쓴다.
그래서 이 변경으로 기존 호출(상세 화면 등)의 동작은 달라지지 않는다.

폭을 화이트리스트로 제한하는 이유: 임의의 값을 받으면 요청마다 새 파일이 쌓여
디스크를 채우는 통로가 된다(캐시 폭탄).
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

#: 축소본 캐시가 쌓이는 폴더 (원본 루트 아래). 점으로 시작해 원본 목록과 섞이지 않는다.
THUMB_DIR = ".thumb"

#: 허용 폭. 목록 카드(2x 기준 약 390px)는 400, 넓은 화면·2단 배치는 800.
ALLOWED_WIDTHS = (400, 800)

#: 사진이라 82 면 눈에 띄는 손실 없이 크게 줄어든다.
JPEG_QUALITY = 82


def requested_width(raw: str | None) -> int | None:
    """쿼리스트링의 w 를 허용 폭으로 해석한다. 해석할 수 없으면 None(=원본)."""

    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value in ALLOWED_WIDTHS else None


def build_thumbnail(source: Path, width: int) -> bytes:
    """원본을 width 폭 JPEG 로 줄여 바이트로 돌려준다."""

    with Image.open(source) as im:
        # 휴대폰으로 찍은 원본은 회전 정보가 EXIF 에만 있을 수 있다 — 먼저 세워 둔다.
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            # JPEG 는 알파를 담지 못한다. 투명한 자리는 흰색으로 깐다(앱 면 색이 밝다).
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.split()[-1])
            im = flat
        else:
            im = im.convert("RGB")
        if im.width > width:
            height = max(1, round(im.height * width / im.width))
            im = im.resize((width, height), Image.LANCZOS)
        buffer = BytesIO()
        im.save(
            buffer,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
        return buffer.getvalue()


def cached_thumbnail(source: Path, width: int, cache_dir: Path, key: str) -> bytes:
    """축소본을 돌려준다. 디스크 캐시를 쓰되, 못 쓰면 매번 만들어서라도 응답한다.

    key 는 파일 이름이 겹치지 않도록 호출부가 주는 식별자(external_id)다 —
    cover_image_url 은 하위 폴더를 포함할 수 있어 파일명만으로는 유일하지 않다.
    """

    destination = cache_dir / f"{key}-{width}.jpg"
    try:
        # 원본이 더 새로우면 캐시는 낡은 것이다(운영 CSV 재적재 등).
        if destination.is_file() and destination.stat().st_mtime >= source.stat().st_mtime:
            return destination.read_bytes()
    except OSError:  # 캐시를 읽지 못하는 것은 실패가 아니다 — 아래에서 다시 만든다.
        pass

    data = build_thumbnail(source, width)

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 같은 축소본을 두 요청이 동시에 만들어도 반쯤 쓰인 파일이 보이지 않도록
        # 임시 파일에 쓴 뒤 원자적으로 바꿔 끼운다.
        temporary = destination.with_name(f"{destination.name}.{id(data):x}.tmp")
        temporary.write_bytes(data)
        temporary.replace(destination)
    except OSError:
        # 읽기 전용 배포처럼 쓸 수 없는 환경도 있다. 캐시는 못 남겨도 응답은 나가야 한다.
        logger.warning("커버 축소본을 캐시하지 못했습니다: %s", destination, exc_info=True)

    return data
