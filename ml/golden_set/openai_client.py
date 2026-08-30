"""원칙 합성용 OpenAI 클라이언트 (Gemini 대체).

`synthesize_principles`는 `PrincipleClient` 프로토콜만 요구한다. 그래서 공급자를
바꿔도 합성 로직·캐시·검증은 그대로다. Gemini 할당량이 막혔을 때 이 단계만
옮겨 돌리기 위한 것이다.

## 캐시 키 주의

캐시 키에 `settings.gemini_model`이 들어간다. 모델 이름이 달라지면 **같은 증거라도
캐시가 안 맞아 전부 다시 호출한다.** 그래서 이 클라이언트를 쓸 때는 모델 이름을
설정에 반영하지 않고 `synthesize-principles --provider openai`가 그대로 넘긴다 —
Gemini로 22건을 이미 채웠다면 그 캐시를 살리려는 선택이다. 공급자가 섞인 결과가
싫으면 캐시를 지우고 처음부터 돌린다.

## 구조화 출력

Gemini는 응답 스키마를 API 인자로 받지만 OpenAI의 strict json_schema는 모든 속성이
`required`이고 `additionalProperties: false`여야 한다. 골든셋 스키마는 그 조건을
만족하지 않으므로 `json_object` 모드를 쓰고 스키마를 지시문에 실어 보낸다. 결과는
`principles._validate_principles()`가 어차피 다시 검사하므로, 형식이 어긋나면
저장 전에 걸린다.
"""

from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_MODEL = "gpt-4o-mini"

_FORMAT_RULE = """
출력은 아래 JSON 스키마를 따르는 JSON 객체 하나여야 합니다. 설명이나 코드펜스 없이
JSON만 출력하세요.

{schema}
"""


class OpenAIPrincipleClient:
    """`PrincipleClient` 프로토콜의 OpenAI 구현."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise ValueError(
                "OPENAI_API_KEY가 없습니다. 셸 환경변수로 넣으세요 "
                "(.env는 Infisical이 덮어씁니다)."
            )
        self.model = model or os.getenv("GOLDEN_OPENAI_MODEL") or os.getenv(
            "OPENAI_MODEL", DEFAULT_MODEL
        )
        self._client = OpenAI(api_key=key, timeout=timeout_seconds)

    def generate_text_json(
        self,
        *,
        prompt: str,
        system_instruction: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        system = system_instruction + _FORMAT_RULE.format(
            schema=json.dumps(schema, ensure_ascii=False)
        )
        response = self._client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("OpenAI가 빈 응답을 돌려주었습니다.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"OpenAI 응답을 JSON으로 읽지 못했습니다: {text[:200]}"
            ) from error
