"""Qwen Image Edit Plus (Alibaba Cloud Model Studio / DashScope).

오픈 가중치도 있으나 이 테스트에서는 API 호출로 통일한다.
결과 이미지는 URL(24시간 유효)로 오므로 즉시 다운로드해 저장한다.

환경변수:
  DASHSCOPE_API_KEY  (필수)
  QWEN_IMAGE_MODEL   (기본 qwen-image-edit-plus)
  DASHSCOPE_BASE_URL (기본 국제 리전 https://dashscope-intl.aliyuncs.com/api/v1)
"""
from __future__ import annotations

import base64
import os

import requests

from .base import ImageEditProvider

TIMEOUT_SEC = 300


class QwenImageEditProvider(ImageEditProvider):
    key = "qwen-image-edit-plus"
    required_env = "DASHSCOPE_API_KEY"

    def __init__(self) -> None:
        import dashscope  # 지연 import

        dashscope.base_http_api_url = os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/api/v1"
        )
        self.api_key = os.environ["DASHSCOPE_API_KEY"]
        self.model = os.getenv("QWEN_IMAGE_MODEL", "qwen-image-edit-plus")

    def edit(self, image_bytes: bytes, mime: str, prompt: str,
             item: dict | None = None) -> bytes:
        from dashscope import MultiModalConversation

        b64 = base64.b64encode(image_bytes).decode()
        rsp = MultiModalConversation.call(
            api_key=self.api_key,
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"image": f"data:{mime};base64,{b64}"},
                    {"text": prompt},
                ],
            }],
            result_format="message",
            n=1,
        )
        status = getattr(rsp, "status_code", None)
        if status != 200:
            raise RuntimeError(
                f"Qwen API {status}: "
                f"{getattr(rsp, 'code', '')} {getattr(rsp, 'message', '')}"
            )
        try:
            content = rsp["output"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Qwen 응답 구조 파싱 실패: {e} / {rsp}") from e

        for part in content:
            url = part.get("image") if isinstance(part, dict) else None
            if url:
                img = requests.get(url, timeout=TIMEOUT_SEC)
                img.raise_for_status()
                return img.content
        raise RuntimeError(f"Qwen 응답에 이미지가 없습니다: {content}")
