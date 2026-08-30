"""GPT Image 2 (OpenAI images.edit).

기존 common/product_image_generator.py(gpt-image-1)와 같은 API를 쓰되
모델만 gpt-image-2로 교체한 형태.

moderation 대응 (사람이 찍힌 실사 사진 편집은 출력 단계 오탐이 잦다):
  1차: 전체 사진 + moderation=low (extra_body — 미지원 계정이면 자동 제외)
  2차: 동일 재시도 (출력 단계 차단은 확률적이라 재생성으로 통과되기도 함)
  3차: 아이템 bbox 주변만 잘라 재시도 (얼굴·배경 문맥 제거 → 통과율 상승.
       enumerator 캐시에 bbox가 없으면 생략)
그래도 차단되면 그대로 실패 보고 — 프린트 그래픽 자체(캐릭터·저작권 이미지)가
원인인 경우는 우회 불가이며, 이는 모델 비교 데이터로 기록할 가치가 있다.

환경변수:
  OPENAI_API_KEY          (필수)
  OPENAI_IMAGE_MODEL      (기본 gpt-image-2)
  OPENAI_IMAGE_SIZE       (기본 auto)
  OPENAI_IMAGE_MODERATION (기본 low, 빈 문자열이면 파라미터 미전송)
"""
from __future__ import annotations

import base64
import io
import os
import sys

from .base import ImageEditProvider

CROP_PAD = 0.15  # bbox 폴백 크롭 여백 비율


class GptImageProvider(ImageEditProvider):
    key = "gpt-image-2"
    required_env = "OPENAI_API_KEY"

    def __init__(self) -> None:
        from openai import OpenAI  # 지연 import

        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
        self.size = os.getenv("OPENAI_IMAGE_SIZE", "auto")
        self.moderation = os.getenv("OPENAI_IMAGE_MODERATION", "low")
        self._moderation_param_ok = True  # unknown parameter로 거부되면 False

    def edit(self, image_bytes: bytes, mime: str, prompt: str,
             item: dict | None = None) -> bytes:
        last_err: Exception | None = None

        for attempt in ("full", "full-retry", "bbox-crop"):
            payload, payload_mime = image_bytes, mime
            if attempt == "bbox-crop":
                cropped = self._crop_to_bbox(image_bytes, item)
                if cropped is None:
                    break  # bbox 정보 없음 → 폴백 불가
                payload, payload_mime = cropped, "image/png"
                print(f"  [gpt-image-2] moderation 차단 → bbox 크롭 폴백 시도",
                      file=sys.stderr)
            try:
                return self._call(payload, payload_mime, prompt)
            except Exception as e:
                if "moderation_blocked" not in str(e):
                    raise
                last_err = e
        raise last_err  # 3단계 모두 차단

    # ── 호출/폴백 유틸 ────────────────────────────────────────
    def _call(self, image_bytes: bytes, mime: str, prompt: str) -> bytes:
        ext = "png" if "png" in mime else "jpg"
        kwargs = dict(model=self.model, prompt=prompt, size=self.size)

        if self.moderation and self._moderation_param_ok:
            try:
                result = self.client.images.edit(
                    image=(f"input.{ext}", io.BytesIO(image_bytes), mime),
                    extra_body={"moderation": self.moderation},
                    **kwargs,
                )
                return self._decode(result)
            except Exception as e:
                msg = str(e).lower()
                if "unknown parameter" in msg and "moderation" in msg:
                    self._moderation_param_ok = False  # 이후 호출부터 미전송
                    print("  [gpt-image-2] moderation 파라미터 미지원 → 제외하고 진행",
                          file=sys.stderr)
                else:
                    raise
        result = self.client.images.edit(
            image=(f"input.{ext}", io.BytesIO(image_bytes), mime),
            **kwargs,
        )
        return self._decode(result)

    @staticmethod
    def _crop_to_bbox(image_bytes: bytes, item: dict | None) -> bytes | None:
        """enumerator가 준 bbox([ymin,xmin,ymax,xmax], 0~1000 정규화) 주변 크롭."""
        bbox = (item or {}).get("bbox")
        if not bbox or len(bbox) != 4:
            return None
        from PIL import Image  # 지연 import

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        ymin, xmin, ymax, xmax = [max(0, min(1000, v)) for v in bbox]
        x1, x2 = xmin / 1000 * w, xmax / 1000 * w
        y1, y2 = ymin / 1000 * h, ymax / 1000 * h
        if x2 <= x1 or y2 <= y1:
            return None
        pad_x, pad_y = (x2 - x1) * CROP_PAD, (y2 - y1) * CROP_PAD
        box = (int(max(0, x1 - pad_x)), int(max(0, y1 - pad_y)),
               int(min(w, x2 + pad_x)), int(min(h, y2 + pad_y)))
        buf = io.BytesIO()
        img.crop(box).save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _decode(result) -> bytes:
        if not getattr(result, "data", None):
            raise RuntimeError("OpenAI 이미지 응답에 data가 없습니다.")
        b64 = getattr(result.data[0], "b64_json", None)
        if not b64:
            raise RuntimeError("OpenAI 응답에서 b64_json을 찾지 못했습니다.")
        return base64.b64decode(b64)
