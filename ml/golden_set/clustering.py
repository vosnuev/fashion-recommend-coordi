"""임베딩 클러스터링과 대표·경계 이미지 선택."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import KMeans

from .artifacts import write_json, write_jsonl
from .embedding import load_embeddings


def _recommended_cluster_count(count: int) -> int:
    if count <= 2:
        return 1
    return max(2, min(count, round(math.sqrt(count / 2))))


def cluster_embeddings(
    *,
    run_dir: Path,
    cluster_count: int | None = None,
    random_state: int = 28,
) -> list[dict[str, Any]]:
    ids, vectors = load_embeddings(run_dir / "image_embeddings.npz")
    if not ids:
        raise ValueError("클러스터링할 임베딩이 없습니다.")
    count = cluster_count or _recommended_cluster_count(len(ids))
    count = max(1, min(count, len(ids)))

    if count == 1:
        labels = np.zeros(len(ids), dtype=int)
        centroids = np.mean(vectors, axis=0, keepdims=True)
    else:
        model = KMeans(
            n_clusters=count,
            random_state=random_state,
            n_init=10,
        )
        labels = model.fit_predict(vectors)
        centroids = model.cluster_centers_

    rows: list[dict[str, Any]] = []
    for label in range(count):
        indexes = np.flatnonzero(labels == label)
        distances = np.linalg.norm(vectors[indexes] - centroids[label], axis=1)
        ordered = indexes[np.argsort(distances)]
        medoid = int(ordered[0])
        boundary = int(ordered[-1])
        for index in indexes:
            role = "member"
            if int(index) == medoid:
                role = "representative"
            elif int(index) == boundary and boundary != medoid:
                role = "boundary"
            rows.append(
                {
                    "golden_id": ids[int(index)],
                    "cluster_id": f"cluster-{label:03d}",
                    "selection_role": role,
                    "distance_to_centroid": round(
                        float(np.linalg.norm(vectors[index] - centroids[label])), 6
                    ),
                }
            )

    rows.sort(key=lambda row: str(row["golden_id"]))
    write_jsonl(run_dir / "clusters.jsonl", rows)
    write_json(
        run_dir / "clustering.meta.json",
        {
            "algorithm": "kmeans-cosine-equivalent-on-normalized-vectors",
            "cluster_count": count,
            "random_state": random_state,
            "representatives": [
                row["golden_id"]
                for row in rows
                if row["selection_role"] == "representative"
            ],
            "boundaries": [
                row["golden_id"] for row in rows if row["selection_role"] == "boundary"
            ],
        },
    )
    return rows
