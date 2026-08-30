"""기본 4개 모델 + Hugging Face 3개 모델, 총 7개 모델을 한 표로 비교한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS = (
    "baseline",
    "random_forest",
    "hist_gradient_boosting",
    "knn",
    "tabpfn_v2",
    "nori",
    "tabpfn_mix",
)
MODEL_GROUP = {
    "baseline": "기본",
    "random_forest": "기본",
    "hist_gradient_boosting": "기본",
    "knn": "기본",
    "tabpfn_v2": "Hugging Face",
    "nori": "Hugging Face",
    "tabpfn_mix": "Hugging Face",
}
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)


def load_detail(run_name: str) -> pd.DataFrame:
    frames = []
    for model in MODELS:
        path = (
            BASE_DIR
            / "experiments"
            / "tabular"
            / model
            / run_name
            / "metrics.json"
        )
        records = json.loads(path.read_text(encoding="utf-8-sig"))
        frames.extend(record for record in records if record["model"] == model)
    return pd.DataFrame(frames)


def summarize(detail_df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        detail_df.groupby("model", as_index=False)
        .agg(
            mean_mae=("mae", "mean"),
            mean_rmse=("rmse", "mean"),
            mean_r2=("r2", "mean"),
            mean_p90_error=("p90_absolute_error", "mean"),
            fit_seconds=("fit_seconds", "max"),
            predict_ms_per_row=("predict_ms_per_row", "max"),
        )
        .sort_values("mean_r2", ascending=False)
    )
    summary["group"] = summary["model"].map(MODEL_GROUP)
    return summary.reset_index(drop=True)


def plot_summary(summary: pd.DataFrame) -> Path:
    order = summary["model"].tolist()
    colors = ["#4C72B0" if MODEL_GROUP[m] == "기본" else "#DD8452" for m in order]
    x = range(len(order))

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    metrics = [("mean_r2", "R2 (높을수록 좋음)"), ("mean_mae", "MAE cm (낮을수록 좋음)"), ("mean_rmse", "RMSE cm (낮을수록 좋음)")]
    for ax, (col, title) in zip(axes, metrics):
        values = summary.set_index("model").loc[order, col]

        ax.plot(x, values, "-", color="#999999", linewidth=1, zorder=1)
        ax.scatter(x, values, color=colors, s=70, zorder=2, edgecolor="white", linewidth=0.8)

        span = values.max() - values.min()
        pad = span * 0.4 if span > 0 else values.max() * 0.05
        ax.set_ylim(values.min() - pad, values.max() + pad)

        ax.set_title(title)
        ax.set_xticks(list(x))
        ax.set_xticklabels(order, rotation=30, ha="right")
        for i, v in zip(x, values):
            ax.annotate(f"{v:.4f}", (i, v), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8)

    handles = [
        plt.Rectangle((0, 0), 1, 1, color="#4C72B0", label="기본 모델"),
        plt.Rectangle((0, 0), 1, 1, color="#DD8452", label="Hugging Face 모델"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.05))
    fig.suptitle("신체치수 예측 모델 7종 비교 (SizeKorea, gender/height/weight -> 7개 치수)", y=1.12)
    fig.tight_layout()

    out_path = REPORT_DIR / "model_comparison.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", default="sizekorea-1000-v1")
    args = parser.parse_args()

    detail_df = load_detail(args.run_name)
    summary_df = summarize(detail_df)

    detail_df.to_csv(REPORT_DIR / "model_comparison_detail.csv", index=False)
    summary_df.to_csv(REPORT_DIR / "model_comparison_summary.csv", index=False)
    chart_path = plot_summary(summary_df)

    print(summary_df.round(4).to_string(index=False))
    print(f"\n차트 저장: {chart_path}")
    print(f"요약 CSV: {REPORT_DIR / 'model_comparison_summary.csv'}")
    print(f"상세 CSV: {REPORT_DIR / 'model_comparison_detail.csv'}")


if __name__ == "__main__":
    main()
