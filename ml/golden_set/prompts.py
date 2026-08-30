"""골든 이미지 관찰과 조건부 원칙 합성을 위한 버전된 프롬프트."""

from __future__ import annotations

from . import PRINCIPLE_VERSION, PROMPT_VERSION, SCHEMA_VERSION

AXES = [
    "A1_COLOR_HARMONY",
    "A2_SILHOUETTE_PROPORTION",
    "A3_TPO",
    "A4_SEASON",
    "A5_MATERIAL_PATTERN",
    "A6_STYLE_COHESION",
    "A7_COMPLETENESS_DETAIL",
    "A8_WEARER_FIT",
]

VISUAL_AXES = [
    "A1_COLOR_HARMONY",
    "A2_SILHOUETTE_PROPORTION",
    "A5_MATERIAL_PATTERN",
    "A6_STYLE_COHESION",
    "A7_COMPLETENESS_DETAIL",
]

KNOWLEDGE_ROLES = [
    "SCORE_AND_EXPLANATION",
    "EXPLANATION_ONLY",
    "NEEDS_COUNTEREXAMPLE",
    "DISCARD",
]

ANALYSIS_SYSTEM_INSTRUCTION = """당신은 패션 이미지의 관찰 사실과 미적 판단 후보를 분리합니다.
좋은 이미지라고 전제하고 칭찬을 만들어내지 마세요. 먼저 보이는 의류와 영역을 식별한 뒤,
영역 사이에서 실제로 관찰 가능한 관계만 최대 3개 claim으로 작성하세요. claim은 좋은 이유일
수도, 충돌 가능성일 수도, 단순 관찰일 수도 있습니다. 보이지 않는 소재, 체형, 성격, 브랜드,
가격, 착용감은 추측하지 마세요. 성별 그룹은 데이터 분포 메타데이터일 뿐 품질 기준이 아닙니다.
TPO·계절·착용자 적합성은 명시적 근거가 없으면 UNAVAILABLE로 남기세요. 모든 claim은 하나
이상의 region_id를 참조해야 하며 전체 인상이 필요하면 `whole-look`을 사용하세요. 단일
이미지에서 보편 패션 원칙을 확정하지 마세요. 답변의 모든 자연어는 한국어 JSON으로 작성하세요."""


def _string_array() -> dict[str, object]:
    return {"type": "array", "items": {"type": "string"}}


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "region_id": {"type": "string"},
                    "item_name": {"type": "string"},
                    "category_large": {"type": "string"},
                    "bbox": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "visible_attributes": _string_array(),
                    "uncertain_attributes": _string_array(),
                },
                "required": [
                    "region_id",
                    "item_name",
                    "category_large",
                    "bbox",
                    "visible_attributes",
                    "uncertain_attributes",
                ],
            },
        },
        "look_tags": {
            "type": "object",
            "properties": {
                "style": _string_array(),
                "season_cues": _string_array(),
                "colors": _string_array(),
                "overall_silhouette": {"type": "string"},
            },
            "required": ["style", "season_cues", "colors", "overall_silhouette"],
        },
        "axis_assessability": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "axis": {"type": "string", "enum": AXES},
                    "mode": {
                        "type": "string",
                        "enum": ["FULL", "DEGRADED", "UNAVAILABLE"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["axis", "mode", "reason"],
            },
        },
        "claims": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "axis": {"type": "string", "enum": AXES},
                    "statement": {"type": "string"},
                    "evidence_region_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "evidence_type": {
                        "type": "string",
                        "enum": ["OBJECT", "RELATION", "WHOLE_LOOK"],
                    },
                    "relation_polarity": {
                        "type": "string",
                        "enum": ["HARMONY", "CONFLICT", "NEUTRAL"],
                    },
                    "contribution_direction": {
                        "type": "string",
                        "enum": [
                            "POSITIVE",
                            "NEGATIVE",
                            "CONTEXT_DEPENDENT",
                            "DESCRIPTIVE_ONLY",
                        ],
                    },
                    "importance_rank": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3,
                    },
                    "model_confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "disagreement_risk": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                },
                "required": [
                    "claim_id",
                    "axis",
                    "statement",
                    "evidence_region_ids",
                    "evidence_type",
                    "relation_polarity",
                    "contribution_direction",
                    "importance_rank",
                    "model_confidence",
                    "disagreement_risk",
                ],
            },
        },
        "relationship_summary": {
            "type": "object",
            "properties": {
                "strongest_harmony_claim_id": {"type": "string"},
                "conflict_claim_ids": _string_array(),
                "no_conflict_reason": {"type": "string"},
            },
            "required": [
                "strongest_harmony_claim_id",
                "conflict_claim_ids",
                "no_conflict_reason",
            ],
        },
        "minimum_edit": {
            "type": "object",
            "properties": {
                "target_region_id": {"type": "string"},
                "target_attribute": {"type": "string"},
                "change": {"type": "string"},
                "tested_axis": {"type": "string", "enum": AXES},
                "expected_effect": {"type": "string"},
                "expected_direction": {
                    "type": "string",
                    "enum": ["IMPROVE", "WORSEN", "CHANGE_ONLY", "UNCERTAIN"],
                },
                "single_variable_change": {"type": "boolean"},
                "preserves_style_intent": {"type": "boolean"},
                "requires_visual_variant": {"type": "boolean"},
                "hypothesis_only": {"type": "boolean"},
            },
            "required": [
                "target_region_id",
                "target_attribute",
                "change",
                "tested_axis",
                "expected_effect",
                "expected_direction",
                "single_variable_change",
                "preserves_style_intent",
                "requires_visual_variant",
                "hypothesis_only",
            ],
        },
        "unassessable": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "attribute_or_axis": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["attribute_or_axis", "reason"],
            },
        },
    },
    "required": [
        "observations",
        "look_tags",
        "axis_assessability",
        "claims",
        "relationship_summary",
        "minimum_edit",
        "unassessable",
    ],
}


CONDITION_SCHEMA = {
    "type": "object",
    "properties": {
        "style_intents": _string_array(),
        "pursuit_images": _string_array(),
        "seasons": _string_array(),
        "occasions": _string_array(),
        "garment_conditions": _string_array(),
        "unavailable_context": _string_array(),
    },
    "required": [
        "style_intents",
        "pursuit_images",
        "seasons",
        "occasions",
        "garment_conditions",
        "unavailable_context",
    ],
}


PRINCIPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "principles": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "principle_key": {"type": "string"},
                    "axis": {"type": "string", "enum": AXES},
                    "statement": {"type": "string"},
                    "applies_when": CONDITION_SCHEMA,
                    "exceptions": _string_array(),
                    "principle_type": {
                        "type": "string",
                        "enum": ["SOFT_PRINCIPLE", "EXPLANATION_KNOWLEDGE"],
                    },
                    "knowledge_role": {
                        "type": "string",
                        "enum": KNOWLEDGE_ROLES,
                    },
                    "evidence": {
                        "type": "array",
                        "minItems": 2,
                        "items": {
                            "type": "object",
                            "properties": {
                                "golden_id": {"type": "string"},
                                "claim_id": {"type": "string"},
                            },
                            "required": ["golden_id", "claim_id"],
                        },
                    },
                    "model_confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": [
                    "principle_key",
                    "axis",
                    "statement",
                    "applies_when",
                    "exceptions",
                    "principle_type",
                    "knowledge_role",
                    "evidence",
                    "model_confidence",
                ],
            },
        }
    },
    "required": ["principles"],
}


def analysis_prompt(*, metadata_json: str) -> str:
    return f"""프롬프트 버전: {PROMPT_VERSION}
스키마 버전: {SCHEMA_VERSION}

첨부한 전신 코디를 다음 순서로 분석하세요.
1. 의류·신발·가방·액세서리의 bbox를 0~1000 좌표로 기록합니다.
2. 실제로 보이는 색·패턴·핏·길이와 불확실한 속성을 분리합니다.
3. A1~A8 각 축의 판정 가능성을 FULL/DEGRADED/UNAVAILABLE로 표시합니다.
4. 영역 사이 관계에서 중요한 claim을 최대 3개만 작성합니다. 조화, 충돌, 중립을
   구분하고 단순 관찰을 좋은 이유로 바꾸지 마세요.
5. 코디 의도를 유지하면서 판단 경계를 시험할 최소 수정 가설 한 가지를 작성합니다.
   이것은 개선 정답이 아니므로 hypothesis_only는 항상 true입니다.
6. 단일 이미지에서 일반 패션 원칙이나 사용자 개인 선호를 확정하지 마세요.

아래 메타데이터는 조건 후보일 뿐 이미지 관찰의 증거가 아닙니다. 이미지와 충돌하거나
비어 있다면 추정하지 말고 UNAVAILABLE 또는 unassessable에 기록하세요.
{metadata_json}"""


def principle_prompt(
    *,
    cluster_id: str,
    axis: str,
    evidence_json: str,
    allowed_refs: str = "",
) -> str:
    # 쓸 수 있는 근거를 목록으로 못 박는다. evidence_json 안에 있긴 하지만, 그것만으로는
    # 다른 묶음의 golden_id를 끌어다 쓰는 일이 생긴다(실제로 발생). 목록을 따로 주면
    # 모델이 대조할 대상이 명확해지고, 어겨도 _validate_principles가 저장 전에 막는다.
    allowed_block = (
        "\n\n사용할 수 있는 근거는 다음이 전부입니다. 이 목록 밖의 golden_id나 "
        f"claim_id를 evidence에 쓰지 마세요.\n{allowed_refs}"
        if allowed_refs
        else ""
    )
    return f"""원칙 합성 버전: {PRINCIPLE_VERSION}
클러스터: {cluster_id}
판단 축: {axis}

아래에는 서로 다른 사람 2명 이상이 승인한 이미지 claim만 있습니다. 단일 이미지의
우연한 특징을 일반화하지 말고 서로 다른 이미지 2장 이상에서 반복되는 관계만 조건부
패션 원칙으로 합치세요. 각 원칙 evidence에도 서로 다른 golden_id 2개 이상을 넣으세요. TPO,
계절, 착용자 적합성은 근거가 없으면 unavailable_context에 남기고 추정하지 마세요.
원칙은 취향과 무관한 절대 법칙이 아니라 적용 조건과 예외를 가진 소프트 지식입니다.
비교·반례가 부족하면 knowledge_role을 NEEDS_COUNTEREXAMPLE로 지정하세요. 하드 규칙은
생성하지 마세요. 모든 원칙은 원본 golden_id와 claim_id를 포함해야 합니다.

{evidence_json}{allowed_block}"""
