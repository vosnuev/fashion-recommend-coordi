"""채팅 응답에 남은 제한적인 마크다운 표식을 안전하게 정리한다."""

from __future__ import annotations

import re

_HEADING_PREFIX = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+")
_LIST_PREFIX = re.compile(r"^[ \t]*[-*+][ \t]+")
_HORIZONTAL_RULE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$")
_BOLD_ASTERISK = re.compile(r"(?<![\w*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)")
_ITALIC_ASTERISK = re.compile(
    r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)"
)


def normalize_assistant_text(value: str) -> str:
    """줄 시작 문법과 쌍 강조만 제거하고 상품명 내부 기호는 보존한다."""

    normalized_lines: list[str] = []
    for raw_line in value.splitlines():
        if _HORIZONTAL_RULE.fullmatch(raw_line):
            normalized_lines.append("")
            continue
        line = _HEADING_PREFIX.sub("", raw_line)
        line = _LIST_PREFIX.sub("", line)
        line = _BOLD_ASTERISK.sub(r"\1", line)
        line = _ITALIC_ASTERISK.sub(r"\1", line)
        normalized_lines.append(line.rstrip())
    return "\n".join(normalized_lines).strip()
