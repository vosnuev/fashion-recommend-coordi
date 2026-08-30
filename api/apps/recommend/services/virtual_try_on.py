"""사용자 전신 사진의 체형을 유지하는 Qwen 가상 착장."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.recommend.services.mixed_outfit_render import (
    LoadedReferenceImage,
    OpenRouterQwenImageProvider,
    RenderItemReference,
    RenderSource,
    _detect_media_type,
)

DIRECT_PROMPT_VERSION = "virtual-try-on-direct-v1"
MANNEQUIN_PROMPT_VERSION = "virtual-try-on-mannequin-v5"

#: 체형 판정 → 프롬프트에 쓸 영어 표기. 프롬프트가 영어라 한국어 라벨
#: (body_profile.SILHOUETTE_LABELS)을 그대로 넣을 수 없다.
_SILHOUETTE_EN = {
    "hourglass": "hourglass",
    "inverted_triangle": "inverted-triangle (shoulders wider than hips)",
    "triangle": "triangle (hips wider than shoulders)",
    "rectangle": "rectangle (shoulders and hips balanced)",
    "round": "round",
}
_BMI_EN = {
    "underweight": "slim",
    "normal": "average",
    "overweight": "fuller",
    "obese": "plus-size",
}


def body_note(*, silhouette: str = "", bmi_band: str = "") -> str:
    """사용자가 입력한 체형 정보를 **옷이 앉는 방식**으로만 쓰게 하는 한 문장.

    사람 사진에 이미 몸이 찍혀 있는데 치수를 왜 주는가 — 옷을 어디에 어떻게
    걸칠지(어깨선·허리선·기장)를 정확히 잡으라는 뜻이다. 그래서 문장을 "이 체형에
    맞게 **옷을** 맞춰라"로 쓰고, 몸을 그 수치에 맞추라는 말은 하지 않는다.
    반대로 쓰면 모델이 사진 속 사람을 수치대로 고쳐 그린다 — 그건 가상 착장이
    아니라 체형 보정이고, 사용자가 요청한 적 없는 일이다.

    판정하지 못한 축은 아예 넣지 않는다. 모르는 값을 기본값으로 메우면 잘못된
    체형으로 옷을 맞추게 된다 (리트리버가 UNKNOWN 축을 건너뛰는 것과 같은 이유).
    """
    parts = [
        _SILHOUETTE_EN.get(silhouette, ""),
        _BMI_EN.get(bmi_band, ""),
    ]
    described = ", ".join(p for p in parts if p)
    if not described:
        return ""
    return (
        f"The person's own recorded body type is {described}. "
        "Use this only to place and drape the garments correctly for that build "
        "(shoulder seams, waistline, and hem lengths). "
        "Do not resize, reshape, or idealize the person to match it."
    )

DIRECT_PROMPT = """Image 1 is the target person. Image 2 is the outfit reference.
Preserve the exact face, identity, hairstyle, visible body shape, body proportions,
pose, hands, legs, camera angle, framing, and background from Image 1.
Replace only the clothing on the person with the complete outfit from Image 2.
Preserve the outfit's garment types, colors, patterns, materials, layering, sleeve
lengths, neckline, waistline, and hem lengths. Fit the clothes naturally to the
existing body and pose. Do not slim, enlarge, reshape, beautify, or otherwise alter
the person. Do not add text, logos, or watermarks that are not in the outfit."""

MANNEQUIN_PROMPT = """Image 1 is the target person. Image 2 is the outfit reference.
In one edit, replace the person with a modern clothing-store display mannequin and
dress it only in the complete outfit from Image 2. Preserve Image 1's exact body
silhouette, shoulder width, torso length, waist width, hip width, arm and leg
proportions, apparent height, pose, hand and foot positions, camera angle, framing,
lighting, and background. The mannequin must be smooth glossy white fiberglass.
Its head must be a plain faceless bald seamless oval shell, with no facial features,
hair, likeness, identity, skin texture, or human expression. Visible neck, arms,
hands, ankles, and feet not covered by the reference outfit must be smooth solid
white mannequin material. Completely discard every garment originally worn in
Image 1. Preserve only the outfit from Image 2, including its intended garment
types, colors, patterns, materials, layering, sleeve lengths, neckline, waistline,
and hem lengths. Do not add a base outfit, undershirt, turtleneck, extra sleeves,
extra trousers, socks, or any layer absent from Image 2. Do not create a stone or
plaster statue, realistic skin, or sculpted hairstyle. Do not slim, enlarge,
reshape, idealize, or beautify the source body."""


@dataclass(frozen=True)
class GeneratedTryOnImage:
    content: bytes
    media_type: str
    usage: dict[str, Any] = field(default_factory=dict)


def _reference(label: str, content: bytes) -> LoadedReferenceImage:
    return LoadedReferenceImage(
        item=RenderItemReference(
            item_id=label,
            position=1,
            slot=label,
            source_type=RenderSource.PRODUCT,
            image_ref=label,
        ),
        content=content,
        media_type=_detect_media_type(content),
    )


class VirtualTryOnService:
    def __init__(self, *, provider: OpenRouterQwenImageProvider | None = None) -> None:
        self.provider = provider or OpenRouterQwenImageProvider()

    def _generate(
        self, prompt: str, references: tuple[LoadedReferenceImage, ...]
    ) -> GeneratedTryOnImage:
        content, media_type, usage = self.provider.generate(
            prompt=prompt,
            references=references,
        )
        return GeneratedTryOnImage(content, media_type, usage)

    def fit_person(
        self, person: bytes, outfit: bytes, body_note_text: str = ""
    ) -> GeneratedTryOnImage:
        """사진 속 **그 사람**에게 옷을 입힌다 (마네킹으로 바꾸지 않는다).

        body_note_text 가 있으면 프롬프트 끝에 덧붙인다. 비어 있으면 프롬프트는
        예전과 한 글자도 다르지 않다 — 그래서 DIRECT_PROMPT_VERSION 을 올리지 않고,
        대신 이 문장을 결과 캐시 키(contract)에 넣어 구분한다.
        """
        prompt = f"{DIRECT_PROMPT}\n{body_note_text}" if body_note_text else DIRECT_PROMPT
        return self._generate(
            prompt,
            (_reference("target_person", person), _reference("outfit", outfit)),
        )

    def fit_mannequin(self, person: bytes, outfit: bytes) -> GeneratedTryOnImage:
        return self._generate(
            MANNEQUIN_PROMPT,
            (_reference("target_person", person), _reference("outfit", outfit)),
        )
