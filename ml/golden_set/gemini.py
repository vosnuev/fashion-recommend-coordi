"""Gemini REST structured output 클라이언트."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import requests

from .config import GoldenSettings


class GeminiError(RuntimeError):
    pass


class GeminiStructuredClient:
    def __init__(self, settings: GoldenSettings) -> None:
        #: PrincipleClient 프로토콜이 요구하는 모델 이름.
        self.model = settings.gemini_model
        if not settings.gemini_api_key:
            raise GeminiError("GEMINI_API_KEY가 없습니다.")
        self.settings = settings

    def analyze_image(
        self,
        *,
        image_path: Path,
        prompt: str,
        system_instruction: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        mime = _mime_type(image_path)
        parts = [
            {"text": prompt},
            {
                "inlineData": {
                    "mimeType": mime,
                    "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                }
            },
        ]
        return self._generate(
            parts=parts,
            system_instruction=system_instruction,
            schema=schema,
            temperature=0.2,
        )

    def generate_text_json(
        self,
        *,
        prompt: str,
        system_instruction: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        return self._generate(
            parts=[{"text": prompt}],
            system_instruction=system_instruction,
            schema=schema,
            temperature=0.1,
        )

    def _generate(
        self,
        *,
        parts: list[dict[str, Any]],
        system_instruction: str,
        schema: dict[str, Any],
        temperature: float,
    ) -> dict[str, Any]:
        url = (
            f"{self.settings.gemini_api_base_url}/v1beta/models/"
            f"{self.settings.gemini_model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        try:
            response = requests.post(
                url,
                headers={
                    "x-goog-api-key": self.settings.gemini_api_key,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.settings.gemini_timeout_seconds,
            )
            if response.status_code >= 400:
                raise GeminiError(
                    f"Gemini 호출 실패 {response.status_code}: {response.text[:1000]}"
                )
            payload = response.json()
            text = _extract_text(payload)
            return json.loads(text)
        except requests.Timeout as exc:
            raise GeminiError("Gemini 응답 시간이 초과되었습니다.") from exc
        except requests.RequestException as exc:
            raise GeminiError(f"Gemini 네트워크 호출 실패: {exc}") from exc
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise GeminiError("Gemini 구조화 응답을 해석할 수 없습니다.") from exc


def _extract_text(payload: dict[str, Any]) -> str:
    parts = payload["candidates"][0]["content"]["parts"]
    return "".join(str(part.get("text", "")) for part in parts if "text" in part)


def _mime_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
