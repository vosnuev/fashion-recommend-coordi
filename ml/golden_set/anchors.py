"""사람 쌍대 비교를 보조 Q 점수 앵커로 환산한다.

비교 그래프가 끊겨 있으면 Bradley-Terry 상대 점수를 낼 수 없다. 남성 코디와 여성
코디처럼 애초에 서로 비교하지 않는 묶음은 한 파일에 담겨도 하나의 그래프가 아니라
**독립된 그래프 여러 개**다. 검수표의 `anchor_graph` 열이 그 경계를 적고, 여기서
그 값으로 나눠 각각 fit한다.

⚠️ **그래프가 다르면 점수를 비교하지 마라.** 0~100 환산은 그래프 안에서 최저~최고를
펴는 것이라, men의 80점과 women의 80점은 같은 뜻이 아니다. 리트리버가
`presentation_group`(성별)으로 먼저 거르기 때문에 실제 랭킹에서는 같은 그래프끼리만
경쟁하지만, 그 필터를 푸는 순간 이 전제가 조용히 깨진다. `score_band`도 같은 이유로
그래프 안에서 계산한다.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np

from .artifacts import write_json, write_jsonl
from .review import PAIRWISE_OUTCOMES, aggregate_axis_scores, read_csv_rows

logger = logging.getLogger("golden_set.anchors")

METHOD = "bradley-terry-mm-pilot-v2"
ANCHOR_SCOPE = "Q_OVERALL_STYLE_EXECUTION"

#: `anchor_graph` 열이 없는 검수표(파일럿 표준 표)를 담는 이름. 하나뿐이면 나누기
#: 전과 동작이 같다.
DEFAULT_GRAPH = ""

#: pair -> 그 쌍을 판정한 행들
PairRows = dict[tuple[str, str], list[dict[str, str]]]


def build_anchor_scores(
    *,
    pairwise_csv: Path,
    run_dir: Path,
    observation_reviews_csv: Path | None = None,
    iterations: int = 200,
    minimum_reviewers_per_pair: int = 2,
) -> list[dict[str, Any]]:
    rows = read_csv_rows(pairwise_csv)
    graphs, skipped_rows = _collect_pairs(rows)
    _assert_single_graph_membership(graphs)

    axis_scores = (
        aggregate_axis_scores(observation_reviews_csv)
        if observation_reviews_csv is not None
        else {}
    )

    result: list[dict[str, Any]] = []
    graph_stats: dict[str, dict[str, int]] = {}
    for graph_name, pair_rows in sorted(graphs.items()):
        eligible_pairs = {
            pair: pair_votes
            for pair, pair_votes in pair_rows.items()
            if len({row["reviewer_label"] for row in pair_votes})
            >= minimum_reviewers_per_pair
        }
        if not eligible_pairs:
            # 그래프 하나가 통째로 빠지면 그 이미지들에는 앵커가 없다. 조용히
            # 넘어가면 "왜 이 코디만 점수가 없지"를 나중에 추적하게 된다.
            logger.warning(
                "그래프 %s: 검수자 %d명 이상이 완료한 쌍이 없어 앵커를 만들지 않는다 "
                "(수집된 쌍 %d개)",
                graph_name or "(미지정)",
                minimum_reviewers_per_pair,
                len(pair_rows),
            )
            graph_stats[graph_name] = {
                "num_eligible_pairs": 0,
                "num_completed_votes": 0,
                "num_images": 0,
            }
            continue
        fitted = _fit_graph(
            graph_name=graph_name,
            eligible_pairs=eligible_pairs,
            iterations=iterations,
            axis_scores=axis_scores,
        )
        result.extend(fitted)
        graph_stats[graph_name] = {
            "num_eligible_pairs": len(eligible_pairs),
            "num_completed_votes": sum(
                len(votes) for votes in eligible_pairs.values()
            ),
            "num_images": len(fitted),
        }

    if not result:
        raise ValueError(
            f"검수자 {minimum_reviewers_per_pair}명 이상이 완료한 "
            "비교 가능한 쌍대 비교가 없습니다."
        )

    result.sort(key=lambda row: (str(row["anchor_graph"]), str(row["golden_id"])))
    write_jsonl(run_dir / "anchor_scores.jsonl", result)
    write_json(
        run_dir / "anchor_scores.meta.json",
        {
            "method": METHOD,
            "anchor_scope": ANCHOR_SCOPE,
            "minimum_reviewers_per_pair": minimum_reviewers_per_pair,
            "num_graphs": len(graph_stats),
            "graphs": graph_stats,
            "num_eligible_pairs": sum(
                stats["num_eligible_pairs"] for stats in graph_stats.values()
            ),
            "num_completed_votes": sum(
                stats["num_completed_votes"] for stats in graph_stats.values()
            ),
            "num_skipped_rows": skipped_rows,
            "warning": (
                "파일럿 상대 Q 점수이며 개인 선호 P나 상황 적합도 C를 포함하지 않는 보조 앵커"
            ),
            "cross_graph_warning": (
                "human_score와 score_band는 같은 anchor_graph 안에서만 비교 가능하다"
            ),
        },
    )
    return result


def _collect_pairs(
    rows: list[dict[str, str]],
) -> tuple[dict[str, PairRows], int]:
    """유효한 판정 행을 그래프별·쌍별로 모은다.

    좌우가 뒤집힌 행도 같은 쌍으로 묶는다 — 순서 편향을 없애려고 검수자마다 좌우를
    바꿔 배치하므로, 정렬한 튜플이 쌍의 정체성이다.
    """
    graphs: dict[str, PairRows] = defaultdict(lambda: defaultdict(list))
    seen_reviewer_votes: set[tuple[str, str, str]] = set()
    skipped_rows = 0
    for row in rows:
        winner = row.get("winner", "").strip().lower()
        if not winner:
            continue
        if winner not in PAIRWISE_OUTCOMES and winner not in {
            row.get("left_id", ""),
            row.get("right_id", ""),
        }:
            raise ValueError(f"지원하지 않는 쌍대 비교 결과입니다: {winner}")
        if winner in {"context_dependent", "unassessable"}:
            skipped_rows += 1
            continue
        left, right = row.get("left_id", ""), row.get("right_id", "")
        reviewer = row.get("reviewer_label", "").strip()
        if not left or not right or left == right or not reviewer:
            skipped_rows += 1
            continue
        pair = (min(left, right), max(left, right))
        duplicate_key = (*pair, reviewer)
        if duplicate_key in seen_reviewer_votes:
            raise ValueError(f"동일 검수자의 중복 쌍대 비교가 있습니다: {duplicate_key}")
        seen_reviewer_votes.add(duplicate_key)
        graphs[row.get("anchor_graph", DEFAULT_GRAPH).strip()][pair].append(row)
    return graphs, skipped_rows


def _assert_single_graph_membership(graphs: dict[str, PairRows]) -> None:
    """한 코디가 두 그래프에 걸치면 멈춘다.

    걸치면 그 코디에 서로 비교할 수 없는 점수가 두 개 생기고, `anchor_scores.jsonl`에
    같은 golden_id가 두 줄로 들어간다. 적재 단계에서 뒤쪽이 앞쪽을 덮어써서
    "어느 쪽이 반영됐는지 모르는" 상태가 된다.
    """
    owner: dict[str, str] = {}
    conflicts: list[str] = []
    for graph_name, pair_rows in sorted(graphs.items()):
        for pair in pair_rows:
            for golden_id in pair:
                previous = owner.setdefault(golden_id, graph_name)
                if previous != graph_name:
                    conflicts.append(f"{golden_id}({previous}/{graph_name})")
    if conflicts:
        raise ValueError(
            "두 개 이상의 anchor_graph에 걸친 코디가 있습니다: "
            f"{sorted(set(conflicts))}"
        )


def _fit_graph(
    *,
    graph_name: str,
    eligible_pairs: PairRows,
    iterations: int,
    axis_scores: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = sorted({golden_id for pair in eligible_pairs for golden_id in pair})
    index = {golden_id: position for position, golden_id in enumerate(ids)}
    wins = np.zeros(len(ids), dtype=float)
    comparisons = np.zeros((len(ids), len(ids)), dtype=float)
    reviewer_votes: dict[tuple[str, str], list[str]] = defaultdict(list)
    confidence_values: dict[tuple[str, str], list[float]] = defaultdict(list)

    for pair, pair_rows in eligible_pairs.items():
        for row in pair_rows:
            left, right = row["left_id"], row["right_id"]
            left_index, right_index = index[left], index[right]
            comparisons[left_index, right_index] += 1
            comparisons[right_index, left_index] += 1
            winner = row["winner"].strip()
            if winner in {"left", left}:
                wins[left_index] += 1
                normalized = left
            elif winner in {"right", right}:
                wins[right_index] += 1
                normalized = right
            elif winner.lower() == "tie":
                wins[left_index] += 0.5
                wins[right_index] += 0.5
                normalized = "tie"
            else:
                raise ValueError(f"유효하지 않은 비교 결과입니다: {winner}")
            reviewer_votes[pair].append(normalized)
            if row.get("confidence_1_3", ""):
                confidence_values[pair].append(
                    _bounded_number(row["confidence_1_3"], 1, 3)
                )

    _assert_connected(comparisons, ids, graph_name)
    abilities = _fit_bradley_terry(
        wins=wins,
        comparisons=comparisons,
        iterations=iterations,
    )
    logits = np.log(np.maximum(abilities, 1e-12))
    if np.ptp(logits) < 1e-12:
        scores = np.full(len(ids), 50.0)
    else:
        scores = 100 * (logits - logits.min()) / np.ptp(logits)
    order = np.argsort(-scores)
    bands: dict[int, str] = {}
    for rank, item_index in enumerate(order):
        fraction = rank / max(1, len(ids))
        bands[int(item_index)] = (
            "high" if fraction < 1 / 3 else ("mid" if fraction < 2 / 3 else "low")
        )

    comparison_counts = comparisons.sum(axis=1)
    result: list[dict[str, Any]] = []
    for position, golden_id in enumerate(ids):
        related_pairs = [pair for pair in eligible_pairs if golden_id in pair]
        related_agreements = []
        related_confidences = []
        related_reviewers: set[str] = set()
        for pair in related_pairs:
            votes = reviewer_votes[pair]
            if votes:
                top_count = max(votes.count(value) for value in set(votes))
                related_agreements.append(top_count / len(votes))
            related_confidences.extend(confidence_values.get(pair, []))
            related_reviewers.update(
                row["reviewer_label"] for row in eligible_pairs[pair]
            )
        agreement = float(np.mean(related_agreements)) if related_agreements else 0.0
        mean_confidence = (
            float(np.mean(related_confidences)) if related_confidences else 1.0
        )
        coverage = min(1.0, float(comparison_counts[position]) / 8.0)
        score_confidence = coverage * agreement * (mean_confidence / 3.0)
        result.append(
            {
                "golden_id": golden_id,
                "anchor_graph": graph_name,
                "anchor_scope": ANCHOR_SCOPE,
                "human_score": round(float(scores[position]), 3),
                "score_band": bands[position],
                "score_confidence": round(score_confidence, 3),
                "comparison_count": int(comparison_counts[position]),
                "reviewer_count": len(related_reviewers),
                "reviewer_agreement": round(agreement, 3),
                "mean_human_confidence_1_3": round(mean_confidence, 3),
                "human_axis_scores_1_5": axis_scores.get(golden_id, {}).get(
                    "axis_scores_1_5", {}
                ),
                "method": METHOD,
            }
        )
    return result


def _fit_bradley_terry(
    *,
    wins: np.ndarray,
    comparisons: np.ndarray,
    iterations: int,
) -> np.ndarray:
    # 0승·전승에서 발산하지 않도록 관측된 edge에 약한 Jeffreys prior를 둔다.
    abilities = np.ones(len(wins), dtype=float)
    smoothed_wins = wins + 0.5 * (comparisons > 0).sum(axis=1)
    for _ in range(iterations):
        denominator = np.zeros(len(wins), dtype=float)
        for left in range(len(wins)):
            for right in range(len(wins)):
                if left == right or comparisons[left, right] == 0:
                    continue
                denominator[left] += (comparisons[left, right] + 1.0) / (
                    abilities[left] + abilities[right]
                )
        updated = smoothed_wins / np.maximum(denominator, 1e-12)
        updated /= np.exp(np.mean(np.log(np.maximum(updated, 1e-12))))
        if np.max(np.abs(updated - abilities)) < 1e-8:
            return updated
        abilities = updated
    return abilities


def _assert_connected(
    comparisons: np.ndarray,
    ids: list[str],
    graph_name: str = DEFAULT_GRAPH,
) -> None:
    adjacency = {
        index: set(np.flatnonzero(comparisons[index] > 0).tolist())
        for index in range(len(ids))
    }
    visited = {0}
    queue = deque([0])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    if len(visited) != len(ids):
        missing = [ids[index] for index in range(len(ids)) if index not in visited]
        raise ValueError(
            f"anchor_graph={graph_name or '(미지정)'}: 비교 가능한 2인 검수 쌍만으로 "
            f"그래프가 연결되지 않았습니다: {missing}"
        )


def _bounded_number(value: str, lower: int, upper: int) -> float:
    number = float(value)
    if not lower <= number <= upper:
        raise ValueError(f"점수 범위는 {lower}~{upper}입니다: {value}")
    return number
