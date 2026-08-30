"""네 축을 실측 5,092명으로 차트화한다.

구조도(four-axes-body-analysis.png)가 "축을 왜 넷으로 나누나"를 말한다면,
이 차트는 "그 축이 실제 데이터에서 어떻게 생겼나"를 보여준다. 배치를 구조도와
똑같이 좌 1 / 우 3으로 맞춰 두 장이 한 쌍으로 읽히게 한다.

두 가지 판단:

1. **색으로 체형을 구분하지 않는다.** 산점도는 모든 쌍이 서로 인접하므로
   (dataviz: `--pairs all`) 검증을 통과하는 계열은 3개까지다. 체형은 6개라
   색 구분이 불가능하다. 대신 분류 규칙을 **영역**으로 그리고 영역마다 이름을
   직접 붙인다 — 애초에 이 그림의 주제가 "규칙이 공간을 어떻게 자르나"이므로
   색보다 영역이 더 정확한 표현이다. 계열이 하나뿐이라 범례도 필요 없다.

2. **가로축은 백분위 공간에 그린다.** 분류가 p33/p67/p90으로 정의돼 있는데
   원래 비율값으로 그리면 가운데 열(p33~p67)이 x축의 10퍼센트도 안 돼
   세 영역의 이름을 넣을 자리가 없다. 백분위로 바꾸면 규칙이 격자로 보이고
   여섯 영역이 고르게 잡힌다. 축 눈금에 해당 원래 비율값을 같이 적는다.

실행:
    python golden-set/body/tools/gen_axes_charts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "ml/body_measurement/data/processed/sizekorea_measurements_clean.csv"
TH = ROOT / "golden-set/body/rules/body_shape_thresholds.json"
OUT_DIR = ROOT / "golden-set/body/images/diagrams"

# dataviz 기준 팔레트 (light) — 계열이 하나라 series-1만 쓴다
SURFACE = "#FCFCFB"
INK = "#0B0B0B"
SECOND = "#52514E"
MUTED = "#8A8A85"
GRID = "#E4E4E0"
DATA = "#2A78D6"
CUT = "#C2410C"        # 임계선 — 데이터와 역할이 달라 다른 색을 쓴다

GENDER_LABEL = {"F": "여성", "M": "남성"}

VERTICAL = [
    ("torso_leg_ratio", "세로축 ①  상체 : 하체", "torso_leg_ratio",
     ("상체 짧음", "균형", "상체 김")),
    ("neck_length", "세로축 ②  목 길이", "neck_length (cm)",
     ("짧은 목", "균형", "긴 목")),
    ("thigh_calf_ratio", "세로축 ③  허벅지 : 종아리", "thigh_calf_ratio",
     ("종아리 김", "균형", "허벅지 김")),
]


def pick_font() -> str:
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for k in ("Malgun Gothic", "Noto Sans KR", "NanumGothic", "Gulim"):
        if k in installed:
            return k
    raise SystemExit("한글 폰트를 찾지 못했다")


def to_percentile(series: pd.Series) -> pd.Series:
    """값을 표본 내 백분위(0~100)로 바꾼다. 분류가 백분위로 정의돼 있으므로
    이 공간에서 그려야 규칙이 격자로 보인다."""
    return series.rank(pct=True) * 100


def draw_width_panel(ax, sub: pd.DataFrame, t: dict, gender: str) -> None:
    x = to_percentile(sub["shoulder_hip"])
    y = to_percentile(sub["waist_hip"])
    sh, wa = t["shoulder_hip"], t["waist_hip"]

    ax.scatter(x, y, s=9, c=DATA, alpha=0.28, linewidths=0, zorder=2)

    # 규칙 경계 — 판정 순서상 round(y≥90)가 가로 전체를 먼저 가져간다.
    ax.axhline(90, color=CUT, lw=1.8, zorder=3)
    for xv in (33, 67):
        ax.plot([xv, xv], [0, 90], color=CUT, lw=1.8, ls=(0, (6, 4)), zorder=3)
    for yv in (33, 67):
        ax.plot([33, 67], [yv, yv], color=CUT, lw=1.8, ls=(0, (6, 4)), zorder=3)

    counts = sub["body_shape"].value_counts()
    n = len(sub)

    def label(cx, cy, name, key, *, small=False):
        c = int(counts.get(key, 0))
        ax.text(cx, cy + 2.4, name, fontsize=13.5 if not small else 12,
                fontweight="bold", ha="center", va="center", color=INK, zorder=4)
        ax.text(cx, cy - 2.6, f"{c:,}명 · {c / n:.0%}", fontsize=11 if not small else 10,
                ha="center", va="center", color=SECOND, zorder=4)

    label(50, 95, "둥근체형", "round")
    label(83.5, 45, "역삼각체형", "inverted_triangle")
    label(16.5, 45, "삼각체형", "triangle")
    label(50, 78.5, "사각체형", "rectangle", small=True)
    label(50, 50, "표준체형", "standard", small=True)
    label(50, 16.5, "모래시계체형", "hourglass", small=True)

    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_xticks([0, 33, 67, 100])
    ax.set_yticks([0, 33, 67, 90, 100])
    ax.set_xticklabels(["0", f"p33\n{sh['p33']:.3f}", f"p67\n{sh['p67']:.3f}", "100"],
                       fontsize=10.5, color=SECOND)
    ax.set_yticklabels(["0", f"p33  {wa['p33']:.3f}", f"p67  {wa['p67']:.3f}",
                        f"p90  {wa['p90']:.3f}", "100"], fontsize=10.5, color=SECOND)
    ax.set_xlabel("shoulder / hip  (백분위)", fontsize=12.5, color=SECOND, labelpad=8)
    ax.set_ylabel("waist / hip  (백분위)", fontsize=12.5, color=SECOND, labelpad=8)
    # 성별·표본수는 그림 제목에 이미 있다 — 여기서 반복하면 제목줄과 부딪힌다
    ax.set_title("가로축 — 체형 (모양)",
                 fontsize=16.5, fontweight="bold", color=INK, pad=12, loc="left")


def draw_vertical_panel(ax, sub: pd.DataFrame, t: dict, col: str,
                        title: str, xlabel: str, zone: tuple[str, str, str]) -> None:
    v = sub[col]
    lo, hi = v.quantile(0.005), v.quantile(0.995)   # 이상치가 축을 늘이지 않게 자른다
    ax.hist(v[(v >= lo) & (v <= hi)], bins=44, color=DATA, alpha=0.75,
            edgecolor=SURFACE, linewidth=0.6, zorder=2)

    p33, p67 = t[col]["p33"], t[col]["p67"]
    for cut in (p33, p67):
        ax.axvline(cut, color=CUT, lw=1.8, ls=(0, (6, 4)), zorder=3)

    top = ax.get_ylim()[1]
    for cx, name in zip(((lo + p33) / 2, (p33 + p67) / 2, (p67 + hi) / 2), zone):
        ax.text(cx, top * 0.92, name, fontsize=12, fontweight="bold",
                ha="center", va="center", color=INK, zorder=4)

    # 임계값은 선 옆에 붙인다. 축 아래에 두면 눈금 숫자와, 가운데에 모으면
    # 서로 겹친다 — p33은 왼쪽, p67은 오른쪽으로 밀어 분리한다.
    pad = (hi - lo) * 0.012
    bbox = dict(facecolor=SURFACE, edgecolor="none", pad=1.5)
    ax.text(p33 - pad, top * 0.68, f"p33\n{p33:g}", fontsize=10, ha="right",
            va="center", color=CUT, zorder=5, linespacing=1.35, bbox=bbox)
    ax.text(p67 + pad, top * 0.68, f"p67\n{p67:g}", fontsize=10, ha="left",
            va="center", color=CUT, zorder=5, linespacing=1.35, bbox=bbox)

    ax.set_xlim(lo, hi)
    ax.set_title(title, fontsize=14.5, fontweight="bold", color=INK, pad=8, loc="left")
    ax.set_xlabel(xlabel, fontsize=11.5, color=MUTED, labelpad=6)
    ax.set_yticks([])


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=SECOND, length=0)


def build(gender: str, df: pd.DataFrame, th: dict) -> Path:
    sub = df[df.gender == gender].copy()
    t = th[gender]["all"]

    fig = plt.figure(figsize=(17, 9.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    gs = fig.add_gridspec(3, 2, width_ratios=[1.32, 1], hspace=0.62, wspace=0.16,
                          left=0.055, right=0.975, top=0.815, bottom=0.075)

    ax_w = fig.add_subplot(gs[:, 0])
    draw_width_panel(ax_w, sub, t, gender)
    style(ax_w)

    for i, (col, title, xlabel, zone) in enumerate(VERTICAL):
        ax = fig.add_subplot(gs[i, 1])
        draw_vertical_panel(ax, sub, t, col, title, xlabel, zone)
        style(ax)

    fig.text(0.055, 0.955, f"네 축의 실제 분포 — {GENDER_LABEL[gender]} {len(sub):,}명",
             fontsize=25, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.055, 0.905,
             "사이즈코리아 8차 인체치수조사(2020~2024) 직접측정  ·  "
             "주황 선 = 분류 임계값(성별 내 백분위)",
             fontsize=13, color=SECOND, ha="left", va="top")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"four-axes-distribution-{gender.lower()}.png"
    fig.savefig(out, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out


def classify(row, t) -> str:
    sh, wa = t["shoulder_hip"], t["waist_hip"]
    if row.waist_hip >= wa["p90"]:
        return "round"
    if row.shoulder_hip >= sh["p67"]:
        return "inverted_triangle"
    if row.shoulder_hip <= sh["p33"]:
        return "triangle"
    if row.waist_hip <= wa["p33"]:
        return "hourglass"
    if row.waist_hip >= wa["p67"]:
        return "rectangle"
    return "standard"


def main() -> int:
    plt.rcParams["font.family"] = pick_font()
    plt.rcParams["axes.unicode_minus"] = False

    df = pd.read_csv(SRC)
    df["shoulder_hip"] = df.shoulder / df.hip
    df["waist_hip"] = df.waist / df.hip
    th = json.loads(TH.read_text(encoding="utf-8"))["thresholds"]

    df["body_shape"] = [
        classify(r, th[r.gender]["all"]) for r in df.itertuples()
    ]

    for gender in ("F", "M"):
        out = build(gender, df, th)
        print(f"OK: {out.relative_to(ROOT).as_posix()} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
