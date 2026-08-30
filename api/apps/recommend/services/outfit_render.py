"""골든 코디의 '정면 착용 이미지'를 만들어 둔다.

골든 원본 사진은 대개 노출 불가(exposable=False)라 사용자 화면에 쓸 수 없다.
그래서 파이프라인이 만든 아이템 이미지(흰 배경 파생물)를 참조로 넘겨, 정면을
보는 사람이 그 옷을 입은 이미지를 새로 만든다.

**코디당 한 번만 만든다.** 같은 골든 코디는 여러 사용자에게, 여러 날에 걸쳐
추천되므로 사용자마다 다시 만들 이유가 없다. 결과를 골든셋 산출물과 같은
위치에 두고, 이미 있으면 생성 없이 그 키를 그대로 쓴다. 키가 코디마다
결정적이라 별도의 캐시 테이블이 필요 없다.

    {derived}/{golden_id}/item_000.png     ← 참조 (파이프라인 산출물)
    {derived}/{golden_id}/render_frontal.png  ← 여기에 저장

경로를 아이템 키에서 유도하는 이유가 있다. derived prefix와 dataset version은
golden_set 패키지의 설정이라 api 쪽에는 없다. 아이템 키에 이미 그 경로가
들어 있으므로, 같은 설정을 두 곳에 두는 대신 키에서 디렉터리만 떼어 쓴다.
"""

from __future__ import annotations

import base64
import binascii
import logging
import posixpath
import re
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

from apps.recommend.services import storage
from apps.recommend.services.gender import GENDER_TO_PRESENTATION, normalize_gender

logger = logging.getLogger(__name__)

RENDER_BASENAME = "render_frontal"

#: 착용 이미지의 형식은 백엔드가 정한다. OpenRouter(Qwen)는 PNG를 주고 Gemini는
#: JPEG만 준다 — response_format.mime_type에 image/png을 넣으면 400이다.
#: 그래서 확장자를 하나로 못 박지 않고, **저장할 때 실제 바이트를 보고** 정한다.
#: 재사용 검사는 두 확장자를 모두 본다. 한쪽만 보면 이미 만들어 둔 이미지를
#: 못 찾고 매번 다시 만들어 요금이 사용자 수만큼 붙는다.
RENDER_EXTENSIONS = (".png", ".jpg")
DEFAULT_RENDER_EXTENSION = ".png"

#: 확장자·Content-Type을 정하기 위한 매직 바이트. 응답이 알려준 mime을 믿지
#: 않는다 — 백엔드가 헤더와 다른 바이트를 준 적이 있고, 틀리면 브라우저가
#: 못 여는 파일이 S3에 영구히 남는다.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"RIFF", ".webp", "image/webp"),
)

RENDER_OBJECT_NAME = RENDER_BASENAME + DEFAULT_RENDER_EXTENSION  # 하위호환

#: 백엔드 이름. 참조 장수만 보고 고른다.
BACKEND_OPENROUTER = "openrouter"
BACKEND_GEMINI = "gemini"

#: OpenRouter(qwen/qwen-image-3-pro)의 참조 이미지 한도.
#:
#:     Provider rejections: Alibaba: input_references: must have between 0 and 4 items
#:
#: 이 모델을 제공하는 곳이 Alibaba 하나뿐이라, 5장을 보내면 다른 제공자로
#: 넘어가지 못하고 그대로 400이 된다. 설정값과 무관하게 코드에서 막는다 —
#: 기본값만 낮추면 .env에 옛 값이 남은 서버에서 똑같이 재현된다.
OPENROUTER_MAX_REFERENCES = 4

#: Gemini 3 계열 이미지 모델이 섞을 수 있는 참조 이미지 수.
#: 아이템이 다섯 이상인 코디는 이쪽으로 넘긴다.
GEMINI_MAX_REFERENCES = 14

#: 하위호환. 예전 이름을 쓰는 곳이 있으면 OpenRouter 한도를 가리킨다.
PROVIDER_MAX_REFERENCES = OPENROUTER_MAX_REFERENCES

#: 자리가 모자랄 때 무엇을 남길지. 실루엣을 만드는 옷이 먼저다 — 가방·액세서리를
#: 남기려고 바지를 버리면 생성된 사진이 그 코디가 아니게 된다.
_CATEGORY_PRIORITY = {
    "원피스/세트": 0,
    "상의": 1,
    "하의": 2,
    "아우터": 3,
    "신발": 4,
    "가방": 5,
    "액세서리": 6,
    "언더웨어/이너웨어": 7,
}
#: 분류가 비었거나 목록 밖인 경우. 신발과 가방 사이에 둔다 — 무엇인지 모르는
#: 아이템이 옷일 가능성은 남아 있으므로 가방·액세서리보다는 앞세운다.
_CATEGORY_FALLBACK = 4.5

#: 응답에서 이미지 데이터를 찾을 때 쓰는 data URL 형식
_DATA_URL = re.compile(r"^data:image/(?P<ext>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$", re.S)

#: 모델의 성별 표현. 유니섹스 코디는 남녀 모두에게 추천되므로, 요청한
#: 사용자의 성별에 맞는 사람으로 그려야 한다. 같은 코디라도 성별마다 다른
#: 이미지가 되므로 저장 키도 성별로 갈린다 (render_key_for 참고).
_MODEL_SENTENCE = {
    "male": "성인 남성 모델이 착용한 모습으로 그립니다.\n",
    "female": "성인 여성 모델이 착용한 모습으로 그립니다.\n",
}

PROMPT = (
    "첨부한 이미지들은 한 벌의 코디를 구성하는 개별 의상 아이템입니다.\n"
    "이 옷들을 모두 착용하고 정면을 바라보는 사람의 전신 사진을 만들어 주세요.\n"
    "요구사항:\n"
    "- 각 아이템의 색상·핏·기장·소재감·디테일을 원본 그대로 유지합니다.\n"
    "- 배경은 단색 흰색, 조명은 균일한 스튜디오 촬영처럼.\n"
    "- 전신이 잘리지 않게 머리끝부터 발끝까지 담습니다.\n"
    "- 특정 실존 인물을 닮게 만들지 않습니다.\n"
    "- 사진에 텍스트나 워터마크를 넣지 않습니다."
)


class RenderError(RuntimeError):
    """착용 이미지 생성 실패. 추천 자체를 되돌리지는 않는다."""


@dataclass(frozen=True)
class RenderRef:
    s3_bucket: str
    s3_key: str

    def as_dict(self) -> dict[str, str]:
        return {"s3_bucket": self.s3_bucket, "s3_key": self.s3_key}


def prompt_for(gender: str = "") -> str:
    """성별에 맞는 생성 지시문. 성별을 모르면 사람을 특정하지 않는다."""
    if sentence := _MODEL_SENTENCE.get(normalize_gender(gender)):
        return PROMPT + "- " + sentence.rstrip("\n") + "\n"
    return PROMPT


def render_key_for(
    item_s3_key: str,
    extension: str = DEFAULT_RENDER_EXTENSION,
    gender: str = "",
) -> str:
    """아이템 이미지 키에서 착용 이미지 키를 유도한다.

    **성별마다 다른 키를 쓴다.** 유니섹스 코디는 남녀 모두에게 추천되는데,
    키가 하나면 먼저 만든 쪽의 이미지가 반대 성별에게 그대로 나간다 — 남성
    사용자가 여성 모델 사진을 보는 일이 벌어진다.

        .../render_frontal_men.jpg
        .../render_frontal_women.jpg
        .../render_frontal.png        ← 성별 없이 만들던 시절의 키

    성별을 모르면 옛 키를 쓴다. 옛 키의 이미지는 모델 성별이 무엇인지 알 수
    없으므로 **성별을 아는 요청에서는 재사용하지 않는다.**
    """
    suffix = ""
    if presentation := GENDER_TO_PRESENTATION.get(normalize_gender(gender)):
        suffix = f"_{presentation}"
    return posixpath.join(
        posixpath.dirname(item_s3_key), RENDER_BASENAME + suffix + extension
    )


def _sniff(image: bytes) -> tuple[str, str]:
    """이미지 바이트에서 (확장자, Content-Type)을 알아낸다."""
    for magic, extension, content_type in _MAGIC:
        if image.startswith(magic):
            return extension, content_type
    logger.warning(
        "착용 이미지 형식을 알 수 없어 PNG로 저장합니다 (앞 8바이트: %r)", image[:8]
    )
    return DEFAULT_RENDER_EXTENSION, "image/png"


def _find_existing(bucket: str, item_s3_key: str, gender: str = "") -> str | None:
    """이미 만들어 둔 착용 이미지가 있으면 그 키. 확장자 후보를 모두 본다.

    성별을 아는 요청은 **그 성별의 키만** 본다. 옛 키(render_frontal.png)는
    모델 성별을 알 수 없어서, 재사용하면 남성에게 여성 모델이 나갈 수 있다.
    """
    for extension in RENDER_EXTENSIONS:
        key = render_key_for(item_s3_key, extension, gender)
        if storage.exists_for(bucket, key):
            return key
    return None


def existing_render(
    bucket: str, item_s3_key: str, gender: str = ""
) -> RenderRef | None:
    """**생성하지 않고** 이미 있는 착용 이미지만 찾는다.

    조회 경로에서 쓴다. 생성은 수십 초가 걸려 요청을 잡아둘 수 없지만, 이미
    만들어져 있는지 보는 건 HEAD 한두 번이라 조회에서 해도 된다.

    같은 골든 코디를 받은 다른 사용자의 워커가 그 사이에 만들어 뒀을 수도 있고,
    한 번 실패한 생성이 다음 시행에서 성공했을 수도 있다. 그때 결과 JSON만
    비어 있는 채로 남으면 사용자는 영원히 대표 이미지를 못 본다.
    """
    if not bucket or not item_s3_key:
        return None
    key = _find_existing(bucket, item_s3_key, gender)
    return RenderRef(bucket, key) if key else None


def _ordered_items(items: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """이미지가 있는 아이템을 **중요도 순**으로 정렬한다 (원래 인덱스를 함께 들고).

    예전에는 payload 순서대로 앞에서 잘랐다. 그 순서에는 의미가 없어서, 아이템이
    다섯인 코디에서 신발 대신 가방이 남는 식으로 결과가 흔들렸다.
    """
    usable = [(i, item) for i, item in enumerate(items) if item.get("s3_key")]
    return sorted(
        usable,
        key=lambda pair: (
            _CATEGORY_PRIORITY.get(
                str(pair[1].get("category_large") or ""), _CATEGORY_FALLBACK
            ),
            pair[0],  # 같은 우선순위면 원래 순서 — 같은 코디가 매번 같은 조합이 되도록
        ),
    )


def _take(ordered: list[tuple[int, dict[str, Any]]], limit: int) -> list[str]:
    """중요도 상위 limit개를 고르되, **전달 순서는 원래 순서**로 되돌린다.

    모델에 주는 순서가 결과에 영향을 주므로, 무엇을 버릴지 고르는 기준과 남은
    것을 어떤 순서로 넘길지를 분리한다.
    """
    chosen = ordered[:limit]
    if len(ordered) > limit:
        dropped = [
            str(item.get("item_name") or item.get("category_large") or "?")
            for _, item in ordered[limit:]
        ]
        logger.info(
            "착용 이미지 참조 %d장 중 %d장만 사용합니다 (제외: %s)",
            len(ordered), limit, ", ".join(dropped),
        )
    return [str(item.get("s3_key")) for _, item in sorted(chosen, key=lambda p: p[0])]


@dataclass(frozen=True)
class RenderPlan:
    """이 코디를 어느 백엔드로, 어떤 참조로 만들지."""

    backend: str
    keys: list[str]


def plan_references(items: list[dict[str, Any]]) -> RenderPlan:
    """참조 장수만 보고 백엔드를 고른다.

    OpenRouter(Qwen)가 더 싸므로 기본이고, 참조가 그 한도(4장)를 넘는 코디만
    Gemini로 보낸다. 아이템이 다섯 이상인 코디에서 넷만 남기면 무엇을 버려도
    그 코디가 아니게 되기 때문이다 — 가방을 버려도 아우터가 빠지면 마찬가지다.
    """
    ordered = _ordered_items(items)
    budget = max(0, settings.DAILY_LOOK_RENDER_MAX_REFERENCES)
    ordered = ordered[:budget]

    if len(ordered) >= settings.DAILY_LOOK_RENDER_GEMINI_THRESHOLD:
        return RenderPlan(BACKEND_GEMINI, _take(ordered, GEMINI_MAX_REFERENCES))
    return RenderPlan(BACKEND_OPENROUTER, _take(ordered, OPENROUTER_MAX_REFERENCES))


def _reference_keys(items: list[dict[str, Any]]) -> list[str]:
    """하위호환 래퍼. 새 코드는 plan_references()를 쓴다."""
    return plan_references(items).keys


def ensure_render(
    *, bucket: str, items: list[dict[str, Any]], gender: str = ""
) -> RenderRef | None:
    """착용 이미지를 보장한다. 이미 있으면 만들지 않고 그 참조만 돌려준다.

    `gender`는 **요청한 사용자의 성별**이다. 유니섹스 코디는 남녀 모두에게
    추천되므로 그 사용자에 맞는 모델로 그리고, 성별별로 따로 저장·재사용한다.

    Returns: 참조. 만들 수 없으면(참조 이미지 없음·기능 끔) None.
    Raises: RenderError — 생성을 시도했는데 실패한 경우.
    """
    plan = plan_references(items)
    reference_keys = plan.keys
    if not bucket or not reference_keys:
        logger.info("착용 이미지 생략: 버킷 또는 참조 아이템 이미지가 없습니다")
        return None

    # ── 재사용 ──
    if existing := _find_existing(bucket, reference_keys[0], gender):
        logger.info("착용 이미지 재사용: s3://%s/%s", bucket, existing)
        return RenderRef(bucket, existing)

    if not settings.DAILY_LOOK_RENDER_ENABLED:
        logger.info("착용 이미지 생성이 꺼져 있습니다 (DAILY_LOOK_RENDER_ENABLED=0)")
        return None

    image = _generate(
        bucket=bucket,
        reference_keys=reference_keys,
        backend=plan.backend,
        gender=gender,
    )
    # 확장자와 Content-Type은 받은 바이트가 정한다. .png 키에 JPEG를 넣어 두면
    # S3가 Content-Type: image/png으로 내려보내고, 일부 클라이언트는 못 연다.
    extension, content_type = _sniff(image)
    key = render_key_for(reference_keys[0], extension, gender)
    storage.put_bytes_for(bucket, key, image, content_type)
    logger.info(
        "착용 이미지 생성: s3://%s/%s (%s, %s, 성별=%s, 참조 %d장, %d bytes)",
        bucket, key, plan.backend, content_type,
        normalize_gender(gender) or "(미지정)", len(reference_keys), len(image),
    )
    return RenderRef(bucket, key)


def _generate(
    *, bucket: str, reference_keys: list[str], backend: str, gender: str = ""
) -> bytes:
    if backend == BACKEND_GEMINI:
        return _generate_gemini(
            bucket=bucket, reference_keys=reference_keys, gender=gender
        )
    return _generate_openrouter(
        bucket=bucket, reference_keys=reference_keys, gender=gender
    )


def _load_references(bucket: str, reference_keys: list[str]) -> list[bytes]:
    return [storage.download_for(bucket, key) for key in reference_keys]


def _generate_gemini(
    *, bucket: str, reference_keys: list[str], gender: str = ""
) -> bytes:
    """Gemini 3.1 Flash Image로 만든다. 참조를 14장까지 받는다.

    OpenRouter가 아니라 Google API를 직접 부른다. 이미지 모델은 generateContent가
    아니라 Interactions API를 쓰는데, 화면비·해상도를 response_format으로 지정할
    수 있어야 전신 9:16을 강제할 수 있기 때문이다.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise RenderError(
            "GEMINI_API_KEY가 설정되지 않았습니다 "
            "(참조 5장 이상인 코디는 Gemini로 만듭니다)."
        )

    payload_input: list[dict[str, Any]] = [
        {"type": "text", "text": prompt_for(gender)}
    ]
    for blob in _load_references(bucket, reference_keys):
        payload_input.append(
            {
                "type": "image",
                # 입력 형식도 바이트로 판단한다. 파이프라인이 PNG를 쓰지만,
                # 헤더와 실제가 어긋나면 여기서도 400이 난다.
                "mime_type": _sniff(blob)[1],
                "data": base64.b64encode(blob).decode(),
            }
        )

    try:
        response = requests.post(
            settings.DAILY_LOOK_RENDER_GEMINI_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "model": settings.DAILY_LOOK_RENDER_GEMINI_MODEL,
                "input": payload_input,
                "response_format": {
                    "type": "image",
                    # 출력 형식. Gemini는 JPEG만 받는다 (image/png은 400).
                    "mime_type": settings.DAILY_LOOK_RENDER_GEMINI_MIME_TYPE,
                    "aspect_ratio": settings.DAILY_LOOK_RENDER_ASPECT_RATIO,
                    "image_size": settings.DAILY_LOOK_RENDER_RESOLUTION,
                },
            },
            timeout=settings.DAILY_LOOK_RENDER_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RenderError(f"Gemini 이미지 생성 요청 실패: {exc}") from exc

    if response.status_code >= 400:
        raise RenderError(
            f"Gemini 이미지 생성 실패 {response.status_code}: {response.text[:500]}"
        )

    payload = response.json()
    if usage := (payload.get("usage") or payload.get("usageMetadata")):
        logger.info("착용 이미지 usage(gemini): %s", usage)
    image = _extract_image(payload)
    if image is None:
        raise RenderError(
            "Gemini 응답에서 이미지를 찾지 못했습니다 "
            f"(model={settings.DAILY_LOOK_RENDER_GEMINI_MODEL})"
        )
    return image


def _generate_openrouter(
    *, bucket: str, reference_keys: list[str], gender: str = ""
) -> bytes:
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise RenderError("OPENROUTER_API_KEY가 설정되지 않았습니다.")

    # OpenRouter는 이미지 생성에 전용 엔드포인트(POST /api/v1/images)를 쓴다.
    # 채팅 API에 modalities=["image","text"]를 붙이는 방식은 지원되지 않아
    # 404 "No endpoints found that support the requested output modalities"를 받는다.
    references = [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{_sniff(blob)[1]};base64,"
                + base64.b64encode(blob).decode()
            },
        }
        for blob in _load_references(bucket, reference_keys)
    ]

    try:
        response = requests.post(
            settings.DAILY_LOOK_RENDER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.DAILY_LOOK_RENDER_MODEL,
                "prompt": prompt_for(gender),
                "input_references": references,
                # 전신이 담겨야 하므로 세로로 긴 비율을 지정한다.
                "aspect_ratio": settings.DAILY_LOOK_RENDER_ASPECT_RATIO,
                "resolution": settings.DAILY_LOOK_RENDER_RESOLUTION,
                "n": 1,
            },
            timeout=settings.DAILY_LOOK_RENDER_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RenderError(f"이미지 생성 요청 실패: {exc}") from exc

    if response.status_code >= 400:
        # 모델명 오류·잔액 부족 등 실제 사유는 본문에만 담긴다.
        raise RenderError(
            f"이미지 생성 실패 {response.status_code}: {response.text[:500]}"
        )

    payload = response.json()
    if usage := payload.get("usage"):
        # 첫 실행에서 실제 요금을 눈으로 확인할 수 있게 남긴다.
        logger.info("착용 이미지 usage: %s", usage)
    image = _extract_image(payload)
    if image is None:
        raise RenderError(
            "응답에서 이미지를 찾지 못했습니다. 모델이 이미지 출력을 지원하는지 "
            f"확인하세요 (model={settings.DAILY_LOOK_RENDER_MODEL})"
        )
    return image


def _extract_image(payload: dict[str, Any]) -> bytes | None:
    """OpenRouter 이미지 API 응답에서 첫 이미지를 꺼낸다.

        {"data": [{"b64_json": "...", "media_type": "image/png"}], "usage": {...}}

    채팅 API 형태(messages.images / content의 data URL)와 Gemini 형태도 함께 본다.
    모델·백엔드를 바꾸면 응답 모양이 통째로 달라지는데, 한쪽만 보면 "이미지를
    찾지 못했습니다"로 조용히 실패한다.
    """
    if decoded := _extract_gemini_image(payload):
        return decoded

    for row in payload.get("data") or []:
        if encoded := row.get("b64_json"):
            if decoded := _decode_base64(str(encoded)):
                return decoded
        if url := row.get("url"):
            if decoded := _decode_data_url(str(url)):
                return decoded

    for choice in payload.get("choices") or []:
        message = choice.get("message") or {}
        for image in message.get("images") or []:
            url = (image.get("image_url") or {}).get("url") or image.get("url")
            if decoded := _decode_data_url(str(url or "")):
                return decoded
        content = message.get("content")
        if isinstance(content, str):
            if decoded := _decode_data_url(content.strip()):
                return decoded
        elif isinstance(content, list):
            for part in content:
                url = (part.get("image_url") or {}).get("url", "")
                if decoded := _decode_data_url(str(url)):
                    return decoded
    return None


def _extract_gemini_image(payload: dict[str, Any]) -> bytes | None:
    """Gemini 응답에서 이미지를 꺼낸다. 두 API 모양을 모두 본다.

    Interactions API:
        {"steps": [{"content": [{"type": "image", "data": "<b64>"}]}]}
    generateContent:
        {"candidates": [{"content": {"parts": [{"inlineData": {"data": "<b64>"}}]}}]}

    문서가 Interactions 쪽을 표준으로 안내하지만 두 경로가 함께 살아 있어서,
    엔드포인트 설정(DAILY_LOOK_RENDER_GEMINI_URL)만 바꿔도 동작하게 둔다.
    """
    for step in payload.get("steps") or []:
        for part in step.get("content") or []:
            if not isinstance(part, dict):
                continue
            if decoded := _decode_base64(str(part.get("data") or "")):
                return decoded

    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if decoded := _decode_base64(str(inline.get("data") or "")):
                return decoded
    return None


def _decode_base64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("착용 이미지 base64 디코딩 실패")
        return None


def _decode_data_url(value: str) -> bytes | None:
    matched = _DATA_URL.match(value)
    if not matched:
        return None
    try:
        return base64.b64decode(matched.group("data"), validate=True)
    except (binascii.Error, ValueError):
        logger.warning("착용 이미지 base64 디코딩 실패")
        return None
