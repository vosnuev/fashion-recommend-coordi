"""4대 신체 축 구조도 생성.

"체형"이라는 한 단어에 성격이 다른 것들이 섞여 있다. 이 그림의 목적은
**가로축 1개(폭·모양) vs 세로축 3개(길이 비율)** 라는 구분을 한눈에 보이게 하는 것이다.

그래서 배치가 곧 설명이다:
  - 좌우로 갈라 1개 대 3개라는 개수 차이를 즉시 보이게 한다.
  - 가로축은 가로로 넓은 상자 하나, 세로축은 세로로 쌓은 상자 셋 —
    상자의 방향 자체가 그 축이 무엇을 재는지(폭이냐 길이냐)를 말한다.
  - 색은 흐름의 시작(입력)과 끝(최종)에만 쓴다. 축들은 동등하므로 색을 달리하지 않는다.

실행:
    python golden-set/body/tools/gen_four_axes_diagram.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "golden-set/body/images/diagrams"
OUT = OUT_DIR / "four-axes-body-analysis.png"

INK = "#1A202C"
BODY = "#2D3748"
MUTED = "#718096"
LINE = "#B4C0CE"
GROUP_LINE = "#8A97A8"
FILL = "#FFFFFF"
ACCENT = "#2B6CB0"
ACCENT_FILL = "#EAF2FA"

BOX = "round,pad=0.012,rounding_size=0.018"

VERTICAL_AXES = [
    {
        "tag": "세로축 ①",
        "title": "상체 : 하체",
        "q": "상체와 하체 중 어디가 긴가",
        "pres": "기장 (크롭/기본/롱)  ·  밑위 (하이웨스트/로우)",
        "metric": "torso_leg_ratio",
    },
    {
        "tag": "세로축 ②",
        "title": "목 길이",
        "q": "목이 긴가 짧은가",
        "pres": "넥라인 (V넥 / 하이넥 / 터틀넥)",
        "metric": "neck_length",
    },
    {
        "tag": "세로축 ③",
        "title": "허벅지 : 종아리",
        "q": "다리 안에서 무릎이 위인가 아래인가",
        "pres": "하의 실루엣 (와이드/슬림/부츠컷)  ·  신발 기장",
        "metric": "thigh_calf_ratio",
    },
]


def pick_font() -> str:
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for korean in ("Malgun Gothic", "Noto Sans KR", "NanumGothic", "Gulim"):
        if korean in installed:
            print(f"[font] {korean}")
            return korean
    raise SystemExit("한글 폰트를 찾지 못했다 — 라벨이 전부 깨진다")


def box(ax, xy, w, h, *, edge=LINE, face=FILL, lw=1.8, ls="solid"):
    ax.add_patch(patches.FancyBboxPatch(
        xy, w, h, boxstyle=BOX, linewidth=lw,
        edgecolor=edge, facecolor=face, linestyle=ls))


def arrow(ax, xy_from, xy_to):
    ax.annotate("", xy=xy_to, xytext=xy_from,
                arrowprops=dict(color="#8A97A8", width=1.8, headwidth=10, headlength=10))


def main() -> int:
    plt.rcParams["font.family"] = pick_font()
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(16, 11.5), dpi=200)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FFFFFF")
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # ── 제목
    ax.text(0.5, 0.985, "체형은 한 축이 아니다 — 가로 1 + 세로 3",
            fontsize=31, fontweight="bold", ha="center", va="top", color=INK)
    ax.text(0.5, 0.940, "성격이 다른 것을 섞어 두면 규칙이 서로를 지운다. 그래서 네 축으로 나눈다.",
            fontsize=16.5, ha="center", va="top", color=MUTED)

    # ── 입력
    box(ax, (0.315, 0.845), 0.37, 0.055, edge=ACCENT, face=ACCENT_FILL, lw=2.2)
    ax.text(0.5, 0.8725, "신체 측정 데이터  (Size Korea)",
            fontsize=18.5, fontweight="bold", ha="center", va="center", color=ACCENT)

    L_X, L_W = 0.030, 0.300      # 가로축 그룹
    R_X, R_W = 0.365, 0.605      # 세로축 그룹
    G_TOP, G_BOT = 0.800, 0.212
    CARD_TOP, CARD_BOT = 0.700, 0.255   # 좌·우 카드의 상·하단을 맞춘다

    arrow(ax, (0.44, 0.842), (L_X + L_W / 2, G_TOP + 0.012))
    arrow(ax, (0.56, 0.842), (R_X + R_W / 2, G_TOP + 0.012))

    # ── 그룹 컨테이너 (점선 = 묶음이지 판정 단위가 아님)
    for gx, gw in ((L_X, L_W), (R_X, R_W)):
        box(ax, (gx, G_BOT), gw, G_TOP - G_BOT,
            edge=GROUP_LINE, face="#FAFBFC", lw=1.6, ls=(0, (5, 4)))

    # ── 그룹 헤더
    ax.text(L_X + L_W / 2, 0.766, "↔  가 로 축",
            fontsize=25, fontweight="bold", ha="center", va="center", color=INK)
    ax.text(L_X + L_W / 2, 0.732, "폭 · 모양   |   1개",
            fontsize=16, ha="center", va="center", color=MUTED)

    ax.text(R_X + R_W / 2, 0.766, "↕  세 로 축",
            fontsize=25, fontweight="bold", ha="center", va="center", color=INK)
    ax.text(R_X + R_W / 2, 0.732, "길이 비율   |   3개, 서로 독립",
            fontsize=16, ha="center", va="center", color=MUTED)

    # ── 가로축 카드 (세로로 긴 1장 — 폭을 재는 축)
    bx, bw = L_X + 0.028, L_W - 0.056
    box(ax, (bx, CARD_BOT), bw, CARD_TOP - CARD_BOT)
    cx, left = bx + bw / 2, bx + 0.022

    ax.text(cx, 0.662, "체형 (모양)", fontsize=22, fontweight="bold",
            ha="center", va="center", color=INK)
    ax.text(cx, 0.628, "사이즈코리아 6체형", fontsize=14.5,
            ha="center", va="center", color=MUTED)
    ax.plot([bx + 0.018, bx + bw - 0.018], [0.602, 0.602], color="#E2E8F0", lw=1.5)

    ax.text(left, 0.575, "묻는 것", fontsize=14, fontweight="bold", color=MUTED, va="top")
    ax.text(left, 0.545, "어깨 · 허리 · 엉덩이 중\n어디가 상대적으로 넓은가",
            fontsize=16.5, color=BODY, va="top", linespacing=1.55)

    ax.text(left, 0.438, "처방하는 것", fontsize=14, fontweight="bold", color=MUTED, va="top")
    ax.text(left, 0.408, "핏 (오버/슬림/와이드)\n전체 실루엣",
            fontsize=16.5, color=BODY, va="top", linespacing=1.55)

    ax.text(left, 0.288, "shoulder, waist, hip",
            fontsize=13, color=MUTED, va="top", family="Consolas")

    # ── 세로축 카드 3장 (세로로 쌓는다 — 길이를 재는 축)
    vx, vw = R_X + 0.028, R_W - 0.056
    gap = 0.022
    card_h = (CARD_TOP - CARD_BOT - 2 * gap) / 3
    split = vx + 0.186          # 이름 | 내용 구분선
    right = split + 0.021
    for i, axis in enumerate(VERTICAL_AXES):
        y = CARD_TOP - card_h - i * (card_h + gap)
        box(ax, (vx, y), vw, card_h)

        # 왼쪽=이름/지표, 오른쪽=묻는 것·처방. 라벨과 본문은 0.026 간격으로 고정한다
        # (원본은 라벨이 위 본문과 겹쳤다).
        ax.text(vx + 0.022, y + card_h - 0.027, axis["tag"],
                fontsize=13.5, color=MUTED, va="center")
        ax.text(vx + 0.022, y + card_h - 0.062, axis["title"],
                fontsize=19.5, fontweight="bold", color=INK, va="center")
        ax.text(vx + 0.022, y + 0.024, axis["metric"],
                fontsize=12.5, color=MUTED, va="center", family="Consolas")

        ax.plot([split, split], [y + 0.016, y + card_h - 0.016], color="#E2E8F0", lw=1.5)

        ax.text(right, y + card_h - 0.027, "묻는 것",
                fontsize=13, fontweight="bold", color=MUTED, va="center")
        ax.text(right, y + card_h - 0.056, axis["q"],
                fontsize=16, color=BODY, va="center")

        ax.text(right, y + 0.050, "처방하는 것",
                fontsize=13, fontweight="bold", color=MUTED, va="center")
        ax.text(right, y + 0.021, axis["pres"],
                fontsize=14.5, color=BODY, va="center")

    # ── ①과 ③이 다른 차원이라는 주석 (세로축 그룹 안쪽 하단)
    ax.text(R_X + R_W / 2, 0.228,
            "① 과 ③ 은 다른 차원 — 다리 길이가 같아도 무릎 위치에 따라 달라 보인다",
            fontsize=13.5, ha="center", va="center", color=MUTED, style="italic")

    # ── 두 그룹 → 교집합
    arrow(ax, (L_X + L_W / 2, 0.208), (0.43, 0.158))
    arrow(ax, (R_X + R_W / 2, 0.208), (0.57, 0.158))

    box(ax, (0.215, 0.088), 0.57, 0.068, edge=GROUP_LINE, face="#F5F7FA", lw=1.8)
    ax.text(0.5, 0.133, "교집합 (∩)", fontsize=21, fontweight="bold",
            ha="center", va="center", color=INK)
    ax.text(0.5, 0.104, "네 축의 권장을 교집합으로 합치고,  한 축이라도 기피면 최종 기피",
            fontsize=15, ha="center", va="center", color=BODY)

    arrow(ax, (0.5, 0.085), (0.5, 0.062))

    # ── 최종
    box(ax, (0.215, 0.000), 0.57, 0.050, edge=ACCENT, face=ACCENT_FILL, lw=2.2)
    ax.text(0.5, 0.025, "핏 · 기장 · 밑위 · 넥라인 · 신발",
            fontsize=20, fontweight="bold", ha="center", va="center", color=ACCENT)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"OK: {OUT.relative_to(ROOT).as_posix()} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
