"""선택형 스타일리스트 설정 파일을 검증하고 불변 객체로 제공한다."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "stylist_personas.json"

EXPECTED_PERSONA_ORDER = ("minimal", "experimental", "practical")
EXPECTED_DISPLAY_ORDERS = {
    persona_id: index
    for index, persona_id in enumerate(EXPECTED_PERSONA_ORDER, start=1)
}


class StylistPersonaConfigurationError(ValueError):
    """설정 파일이 없거나 선택형 스타일리스트 계약과 맞지 않는다."""


@dataclass(frozen=True)
class StrategyWeight:
    """전략 점수에서 사용할 하나의 지표와 정규화된 가중치."""

    metric: str
    weight: float


@dataclass(frozen=True)
class StrategyProfile:
    """검색·점수화·조합 선택에만 영향을 주는 전략 설정."""

    objectives: tuple[str, ...]
    search_directives: tuple[str, ...]
    score_weights: tuple[StrategyWeight, ...]
    hypothesis_count: int

    def weight_for(self, metric: str) -> float:
        """지표가 없으면 0을 반환해 전략 소비자의 조건 분기를 줄인다."""

        return next(
            (row.weight for row in self.score_weights if row.metric == metric),
            0.0,
        )


@dataclass(frozen=True)
class VoiceProfile:
    """확정된 추천을 한 문장으로 설명할 때만 사용하는 말투 설정."""

    worldview: str
    tone_traits: tuple[str, ...]
    sentence_guidelines: tuple[str, ...]
    examples: tuple[str, ...]
    max_sentences: int


@dataclass(frozen=True)
class StylistPersona:
    """외부 식별자와 버전이 고정된 선택형 스타일리스트 한 명."""

    id: str
    display_name: str
    description: str
    display_order: int
    strategy_profile: StrategyProfile
    voice_profile: VoiceProfile
    version: int
    prompt_version: str
    enabled: bool


@dataclass(frozen=True)
class StylistPersonaCatalog:
    """선택 제한과 고정 순서를 포함한 전체 스타일리스트 설정."""

    schema_version: str
    min_select: int
    max_select: int
    personas: tuple[StylistPersona, ...]

    def enabled_personas(self) -> tuple[StylistPersona, ...]:
        return tuple(persona for persona in self.personas if persona.enabled)

    @property
    def supported_persona_ids(self) -> tuple[str, ...]:
        """현재 실행 대상으로 선택할 수 있는 스타일리스트 ID를 반환한다."""

        return tuple(persona.id for persona in self.enabled_personas())

    def get(self, persona_id: str, *, enabled_only: bool = True) -> StylistPersona:
        normalized = persona_id.strip()
        for persona in self.personas:
            if persona.id == normalized and (persona.enabled or not enabled_only):
                return persona
        raise StylistPersonaConfigurationError(
            f"사용할 수 없는 스타일리스트 ID입니다: {persona_id!r}"
        )

    def versions(self, persona_ids: tuple[str, ...] | list[str]) -> dict[str, int]:
        """실행 스냅샷에 그대로 저장할 ID별 설정 버전을 반환한다."""

        if len(persona_ids) != len(set(persona_ids)):
            raise StylistPersonaConfigurationError(
                "실행 스냅샷의 스타일리스트 ID는 중복될 수 없습니다."
            )
        return {persona_id: self.get(persona_id).version for persona_id in persona_ids}


def _configuration_error(path: Path, message: str) -> StylistPersonaConfigurationError:
    return StylistPersonaConfigurationError(f"{path}: {message}")


def _reject_unknown_keys(
    value: dict[str, Any],
    allowed: frozenset[str],
    *,
    where: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{where}: 허용되지 않은 필드 {', '.join(unknown)}")


def _mapping(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{where}: JSON 객체여야 합니다.")
    return value


def _text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _integer(value: Any, *, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{where}: {minimum} 이상의 정수여야 합니다.")
    return value


def _boolean(value: Any, *, where: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{where}: true 또는 false여야 합니다.")
    return value


def _text_tuple(value: Any, *, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: 하나 이상의 문자열 배열이어야 합니다.")
    result = tuple(
        _text(row, where=f"{where}[{index}]") for index, row in enumerate(value)
    )
    if len(result) != len(set(result)):
        raise ValueError(f"{where}: 같은 문자열을 중복해서 사용할 수 없습니다.")
    return result


def _strategy_profile(value: Any, *, where: str) -> StrategyProfile:
    raw = _mapping(value, where=where)
    _reject_unknown_keys(
        raw,
        frozenset(
            {
                "objectives",
                "search_directives",
                "score_weights",
                "hypothesis_count",
            }
        ),
        where=where,
    )
    weights_raw = raw.get("score_weights")
    if not isinstance(weights_raw, list) or not weights_raw:
        raise ValueError(f"{where}.score_weights: 하나 이상의 가중치가 필요합니다.")

    weights: list[StrategyWeight] = []
    for index, row in enumerate(weights_raw):
        row_where = f"{where}.score_weights[{index}]"
        item = _mapping(row, where=row_where)
        _reject_unknown_keys(item, frozenset({"metric", "weight"}), where=row_where)
        metric = _text(item.get("metric"), where=f"{row_where}.metric")
        weight = item.get("weight")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise ValueError(f"{row_where}.weight: 0보다 큰 유한한 수여야 합니다.")
        weights.append(StrategyWeight(metric=metric, weight=float(weight)))

    metrics = [row.metric for row in weights]
    if len(metrics) != len(set(metrics)):
        raise ValueError(f"{where}.score_weights: 지표 이름은 중복될 수 없습니다.")
    if not math.isclose(sum(row.weight for row in weights), 1.0, abs_tol=1e-9):
        raise ValueError(f"{where}.score_weights: 가중치 합은 1이어야 합니다.")

    return StrategyProfile(
        objectives=_text_tuple(raw.get("objectives"), where=f"{where}.objectives"),
        search_directives=_text_tuple(
            raw.get("search_directives"),
            where=f"{where}.search_directives",
        ),
        score_weights=tuple(weights),
        hypothesis_count=_integer(
            raw.get("hypothesis_count"),
            where=f"{where}.hypothesis_count",
        ),
    )


def strategy_profile_from_snapshot(value: object) -> StrategyProfile:
    """ChatRunPersona에 고정된 전략 JSON을 현재 설정과 독립적으로 복원한다."""

    try:
        return _strategy_profile(value, where="strategy_snapshot")
    except (TypeError, ValueError) as exc:
        raise StylistPersonaConfigurationError(str(exc)) from exc


def _voice_profile(value: Any, *, where: str) -> VoiceProfile:
    raw = _mapping(value, where=where)
    _reject_unknown_keys(
        raw,
        frozenset(
            {
                "worldview",
                "tone_traits",
                "sentence_guidelines",
                "examples",
                "max_sentences",
            }
        ),
        where=where,
    )
    max_sentences = _integer(
        raw.get("max_sentences"),
        where=f"{where}.max_sentences",
        minimum=1,
    )
    if max_sentences != 1:
        raise ValueError(f"{where}.max_sentences: 제품 정책상 1이어야 합니다.")
    return VoiceProfile(
        worldview=_text(raw.get("worldview"), where=f"{where}.worldview"),
        tone_traits=_text_tuple(
            raw.get("tone_traits"),
            where=f"{where}.tone_traits",
        ),
        sentence_guidelines=_text_tuple(
            raw.get("sentence_guidelines"),
            where=f"{where}.sentence_guidelines",
        ),
        examples=_text_tuple(raw.get("examples"), where=f"{where}.examples"),
        max_sentences=max_sentences,
    )


def _persona(value: Any, *, index: int) -> StylistPersona:
    where = f"personas[{index}]"
    raw = _mapping(value, where=where)
    _reject_unknown_keys(
        raw,
        frozenset(
            {
                "id",
                "display_name",
                "description",
                "display_order",
                "strategy_profile",
                "voice_profile",
                "version",
                "prompt_version",
                "enabled",
            }
        ),
        where=where,
    )
    return StylistPersona(
        id=_text(raw.get("id"), where=f"{where}.id"),
        display_name=_text(raw.get("display_name"), where=f"{where}.display_name"),
        description=_text(raw.get("description"), where=f"{where}.description"),
        display_order=_integer(
            raw.get("display_order"),
            where=f"{where}.display_order",
            minimum=1,
        ),
        strategy_profile=_strategy_profile(
            raw.get("strategy_profile"),
            where=f"{where}.strategy_profile",
        ),
        voice_profile=_voice_profile(
            raw.get("voice_profile"),
            where=f"{where}.voice_profile",
        ),
        version=_integer(raw.get("version"), where=f"{where}.version", minimum=1),
        prompt_version=_text(
            raw.get("prompt_version"),
            where=f"{where}.prompt_version",
        ),
        enabled=_boolean(raw.get("enabled"), where=f"{where}.enabled"),
    )


def _parse_catalog(document: Any) -> StylistPersonaCatalog:
    raw = _mapping(document, where="root")
    _reject_unknown_keys(
        raw,
        frozenset({"schema_version", "min_select", "max_select", "personas"}),
        where="root",
    )
    rows = raw.get("personas")
    if not isinstance(rows, list):
        raise TypeError("personas: JSON 배열이어야 합니다.")
    personas = tuple(_persona(row, index=index) for index, row in enumerate(rows))

    ids = [persona.id for persona in personas]
    if len(ids) != len(set(ids)):
        raise ValueError("personas: 스타일리스트 ID는 중복될 수 없습니다.")
    if set(ids) != set(EXPECTED_PERSONA_ORDER):
        raise ValueError(
            "personas: minimal, experimental, practical 세 ID를 모두 포함해야 합니다."
        )
    for persona in personas:
        expected_order = EXPECTED_DISPLAY_ORDERS[persona.id]
        if persona.display_order != expected_order:
            raise ValueError(
                f"personas.{persona.id}.display_order: {expected_order}이어야 합니다."
            )
    ordered = tuple(sorted(personas, key=lambda persona: persona.display_order))
    if tuple(persona.id for persona in ordered) != EXPECTED_PERSONA_ORDER:
        raise ValueError("personas: 제품 정책의 고정 표시 순서와 맞지 않습니다.")

    min_select = _integer(raw.get("min_select"), where="min_select", minimum=1)
    max_select = _integer(raw.get("max_select"), where="max_select", minimum=1)
    if min_select != 1 or max_select != 3:
        raise ValueError(
            "선택 제한은 제품 정책상 min_select=1, max_select=3이어야 합니다."
        )

    experimental = next(persona for persona in ordered if persona.id == "experimental")
    if experimental.strategy_profile.hypothesis_count != 2:
        raise ValueError(
            "personas.experimental.strategy_profile.hypothesis_count: "
            "제품 정책상 2여야 합니다."
        )
    for persona in ordered:
        if persona.id != "experimental" and persona.strategy_profile.hypothesis_count:
            raise ValueError(
                f"personas.{persona.id}.strategy_profile.hypothesis_count: "
                "실험형이 아니면 0이어야 합니다."
            )

    return StylistPersonaCatalog(
        schema_version=_text(raw.get("schema_version"), where="schema_version"),
        min_select=min_select,
        max_select=max_select,
        personas=ordered,
    )


@lru_cache(maxsize=8)
def _load_catalog(resolved_path: str) -> StylistPersonaCatalog:
    path = Path(resolved_path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _configuration_error(path, "설정 파일을 찾을 수 없습니다.") from exc
    except json.JSONDecodeError as exc:
        raise _configuration_error(
            path,
            f"JSON 문법 오류가 있습니다 (line={exc.lineno}, column={exc.colno}).",
        ) from exc
    except OSError as exc:
        raise _configuration_error(path, "설정 파일을 읽을 수 없습니다.") from exc

    try:
        return _parse_catalog(document)
    except (TypeError, ValueError) as exc:
        raise _configuration_error(path, str(exc)) from exc


def load_stylist_personas(
    path: str | Path | None = None,
) -> StylistPersonaCatalog:
    """설정을 한 번 검증해 프로세스 안에서 재사용한다.

    배포된 설정은 프로세스 재시작 때 반영한다. 테스트나 관리 명령에서 다른 파일을
    검증할 수 있도록 경로 주입을 지원한다.
    """

    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    return _load_catalog(str(target.resolve()))


def clear_stylist_persona_cache() -> None:
    """테스트 또는 명시적인 설정 재검증 전에 로더 캐시를 비운다."""

    _load_catalog.cache_clear()
