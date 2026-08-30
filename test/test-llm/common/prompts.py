"""프론티어 이미지 편집 모델용 공통 프롬프트 빌더.

모든 프로바이더가 동일한 프롬프트를 사용해야 모델 간 공정 비교가 된다.
Seedream 권장 한도(600단어 미만)에 맞춰 간결하게 유지한다.
"""
from __future__ import annotations

# 정면화 지시가 각도별로 다를 필요는 없지만, enumerator가 파악한
# 촬영 각도를 명시하면 모델이 "재구성"임을 더 잘 인지한다.
_VIEW_NOTE = {
    "front": "The photo already shows the front of the item.",
    "side": "The photo shows the item from the side. Reconstruct the front view.",
    "back": "The photo shows the item from the back. Reconstruct the front view.",
    "three-quarter": (
        "The photo shows the item at a three-quarter angle. "
        "Reconstruct the straight-on front view."
    ),
}


def build_edit_prompt(item: dict) -> str:
    """전체 사진에서 아이템 1개를 분리·복구·정면화하는 편집 프롬프트.

    item: enumerator가 만든 dict
      (descriptor_en, category_large, occluded_by, view_angle)
    """
    descriptor = item["descriptor_en"]
    occluded = item.get("occluded_by") or []
    view = item.get("view_angle", "front")

    # 주의: "remove the person", "body parts" 같은 표현은 이미지 API 안전
    # 필터(moderation) 오탐을 유발한다. 사람 언급 없이 "상품만 보여달라"로 쓴다.
    occlusion_note = (
        "In the source photo, some areas of the item are covered by: "
        + ", ".join(occluded)
        + ". Reconstruct those covered areas conservatively, continuing the "
        "visible color, pattern, material and construction. "
        if occluded
        else "If any area of the item is covered in the source photo, "
        "reconstruct it conservatively from the visible evidence. "
    )

    pair_note = (
        "Footwear must be shown as one matching pair, both shoes fully visible."
        if item.get("category_large") == "신발"
        else "Show exactly one item. Do not duplicate sleeves, legs, straps or parts."
    )

    return (
        "From the provided photo, extract exactly one fashion item: "
        f"{descriptor}.\n"
        "Create a clean e-commerce catalog product photo of that single item.\n"
        "Rules:\n"
        "- Pure white background. Item centered, entirely inside the frame, "
        "with generous margin on all sides. Never crop any edge of the item.\n"
        f"- Front-facing standard retail presentation. {_VIEW_NOTE.get(view, _VIEW_NOTE['front'])}\n"
        f"- {occlusion_note}\n"
        "- The output must contain only the product itself on the white "
        "background: no other garments, no accessories that are not the "
        "target item, and nothing else from the source photo.\n"
        "- Do not preserve the on-body drape or posing distortion of the "
        "source photo. Natural unworn retail product shape "
        "(ghost-mannequin style volume is acceptable for clothing).\n"
        "- Preserve the true colors, fabric texture, seams, closures, pockets, "
        "and any real logos or printed graphics exactly as they appear. "
        "Do not invent logos, text, patterns or design details that are not "
        "visible in the photo.\n"
        f"- {pair_note}\n"
        "Output only the edited product image."
    )
