"""모델×이미지 결과 집계 → output/summary.json + summary.md."""
from __future__ import annotations

import json
from pathlib import Path


def write_summary(out_root: Path, records: list[dict]) -> Path:
    """records: run_all.py가 아이템 단위로 쌓은 실행 기록 목록.

    각 record: {model, image, item_id, label_ko, ok, latency_sec, error}
    """
    by_model: dict[str, dict] = {}
    for r in records:
        m = by_model.setdefault(r["model"], {
            "items_total": 0, "items_ok": 0, "items_failed": 0,
            "latency_sum": 0.0, "images": set(), "errors": [],
        })
        m["items_total"] += 1
        m["images"].add(r["image"])
        if r["ok"]:
            m["items_ok"] += 1
            m["latency_sum"] += r["latency_sec"]
        else:
            m["items_failed"] += 1
            m["errors"].append(
                {"image": r["image"], "item": r.get("label_ko"),
                 "error": r.get("error")}
            )

    summary = {}
    for model, m in by_model.items():
        ok = m["items_ok"]
        summary[model] = {
            "images": len(m["images"]),
            "items_total": m["items_total"],
            "items_ok": ok,
            "items_failed": m["items_failed"],
            "avg_latency_sec": round(m["latency_sum"] / ok, 2) if ok else None,
            "errors": m["errors"],
        }

    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# test-llm 실행 요약", "",
        "| 모델 | 이미지 | 아이템 성공/전체 | 평균 latency(s) | 실패 |",
        "|---|---|---|---|---|",
    ]
    for model, s in summary.items():
        lines.append(
            f"| {model} | {s['images']} | {s['items_ok']}/{s['items_total']} "
            f"| {s['avg_latency_sec'] if s['avg_latency_sec'] is not None else '-'} "
            f"| {s['items_failed']} |"
        )
    lines.append("")
    md_path = out_root / "summary.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path
