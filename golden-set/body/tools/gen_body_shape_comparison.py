"""사이즈코리아 6체형 비교 일러스트 생성 (여성/남성 각 1장).

**왜 스크립트로 그리는가**: 두 장(여/남)의 그림체가 지금도, 앞으로도 똑같아야 한다.
사람이 그리거나 이미지 생성 모델을 쓰면 재생성할 때마다 선 굵기·머리 비율·얼굴이 달라진다.
여기서는 남녀가 **완전히 같은 함수**(`draw_figure`)를 통과하고, 성별로 달라지는 것은
`SEX_STYLE`의 소수 파라미터(목 두께·머리 모양·가슴선)뿐이다. 나머지는 전부 데이터가 만든다.

**폭은 어디서 오는가**: `rules/body_shape_thresholds.json`의 `centroids_cm`
(8차 인체치수조사 5,092명 실측 체형별 평균). 손으로 "역삼각은 어깨를 넓게" 같은 보정을
하지 않는다. 그러면 그림이 데이터에 대해 거짓말을 한다.

**단위 변환 주의**: `shoulder`는 **너비(cm)**, `chest/waist/hip`은 **둘레(cm)**다
(02-body-proportion-rules.md §2-3의 경고와 동일한 함정). 둘레를 그대로 너비로 쓰면
모든 체형이 엉덩이 우세로 보여 전부 삼각형이 된다. 그래서 둘레는 타원 둘레 근사로
반너비로 환산한다(`CIRC_TO_HALF`). 셋 다 **같은 계수**를 쓰므로 그림의 waist/hip,
shoulder/hip 비율이 판정에 쓰는 cm 비율과 정확히 일치한다.

**세로는 왜 다 같은가**: 가로축은 "크기"가 아니라 "모양"을 나누는 축이다(§2 v3).
그래서 6명의 그려지는 키를 동일하게 두고, 폭만 실측 cm에 비례시킨다.
여/남 이미지는 **같은 스케일 계수**(`CM_TO_UNIT`)를 공유하므로 두 장을 나란히 놓고
비교해도 된다.

실행:
    python golden-set/body/tools/gen_body_shape_comparison.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch

ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / "golden-set/body/rules/body_shape_thresholds.json"
OUT_DIR = ROOT / "golden-set/body/images/comparison"

# ── 그림체 (두 장이 공유한다. 여기만 바꾸면 두 장이 함께 바뀐다)
INK = "#1A1A1A"
LW = 1.6            # 몸 외곽선
LW_DETAIL = 1.15    # 얼굴·가슴선 등 내부 디테일
LW_GUIDE = 1.1      # 어깨·허리·엉덩이 점선
GUIDE = "#4A4A4A"
MUTED = "#6B7280"
BG = "#FFFFFF"

# ── 판정 순서 = 그리는 순서 (02-body-proportion-rules.md §2-4)
#    표준체형을 맨 끝에 두는 이유: "치우침 없음"의 기준선이라 마지막에 놓아야 비교가 읽힌다.
SHAPES = [
    ("round", "둥근체형"),
    ("inverted_triangle", "역삼각체형"),
    ("triangle", "삼각체형"),
    ("hourglass", "모래시계체형"),
    ("rectangle", "사각체형"),
    ("standard", "표준체형"),
]

# 둘레(cm) → 반너비(cm). 타원 가로:세로 = 1.3 가정, Ramanujan 근사.
#   P ≈ π[3(a+b) − sqrt((3a+b)(a+3b))],  a/b = 1.3  →  a = 0.1792 P
def _circ_to_half(ratio: float = 1.3) -> float:
    a, b = ratio, 1.0
    p = math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))
    return a / p


CIRC_TO_HALF = _circ_to_half()
CM_TO_UNIT = 1.0 / 165.0     # 여·남 공통. 165 = 표본 평균 키 근처 → 실제 비율에 가깝게 그려진다

# ── 세로 앵커 (신장 대비 비율, 인체 계측 통용값). 6명 모두 동일.
Y_TOP, Y_TEMPLE, Y_CHEEK, Y_JAW, Y_CHIN = 1.000, 0.948, 0.906, 0.874, 0.858
Y_NECK, Y_SHOULDER, Y_CHEST, Y_WAIST = 0.824, 0.802, 0.735, 0.620
Y_HIP, Y_CROTCH, Y_KNEE, Y_CALF, Y_ANKLE, Y_SOLE = 0.520, 0.468, 0.278, 0.205, 0.058, 0.000

SEX_STYLE = {
    "F": {"label": "여성", "neck": 0.030, "head": 0.047, "long_hair": True, "bust": 0.062},
    "M": {"label": "남성", "neck": 0.035, "head": 0.049, "long_hair": False, "bust": 0.030},
}

SLOT_W = 0.44        # 한 명이 차지하는 가로 폭 (데이터 좌표)


# ─────────────────────────────────────────────── 곡선

def catmull_rom(points: list[tuple[float, float]], closed: bool = True, n: int = 24) -> np.ndarray:
    """중심 Catmull-Rom 스플라인. 꼭짓점을 그대로 지나므로 좌표가 곧 치수다."""
    p = np.asarray(points, dtype=float)
    if closed:
        p = np.vstack([p[-1], p, p[0], p[1]])
    else:
        p = np.vstack([p[0], p, p[-1]])
    out = []
    for i in range(len(p) - 3):
        p0, p1, p2, p3 = p[i], p[i + 1], p[i + 2], p[i + 3]
        t = np.linspace(0, 1, n, endpoint=False)[:, None]
        out.append(
            0.5 * ((2 * p1)
                   + (-p0 + p2) * t
                   + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                   + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3)
        )
    out.append(p[-2][None, :])
    return np.vstack(out)


def stroke(ax, pts, dx=0.0, closed=False, lw=LW, n=24):
    c = catmull_rom(pts, closed=closed, n=n)
    ax.plot(c[:, 0] + dx, c[:, 1], color=INK, lw=lw, solid_capstyle="round",
            solid_joinstyle="round", zorder=3)


def fill_white(ax, pts, dx=0.0, n=24, z=2):
    """뒤 선을 가리는 흰 채움 (머리카락 밑의 두상 선 등)."""
    c = catmull_rom(pts, closed=True, n=n)
    c[:, 0] += dx
    ax.add_patch(PathPatch(MplPath(c), facecolor=BG, edgecolor="none", zorder=z))


def mirror_close(right: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """오른쪽 절반 → 좌우대칭 닫힌 윤곽. 대칭이므로 좌우가 어긋날 수 없다."""
    return right + [(-x, y) for x, y in reversed(right[1:])]


# ─────────────────────────────────────────────── 한 사람

def body_points(sh: float, ch: float, wa: float, hp: float, nk: float, hd: float):
    """머리 꼭대기 → 오른쪽 몸통 → 오른다리 바깥 → 발 → 오른다리 안쪽 → 가랑이.

    다리·발·목의 굵기는 엉덩이 반너비(hp)에 비례시킨다. 체형이 바뀌면 다리도 함께
    굵어져야 같은 사람으로 보인다 — 다리를 고정하면 둥근체형이 몸통만 부푼 인형이 된다.
    """
    return [
        (0.000, Y_TOP),
        (hd * 0.55, Y_TOP - 0.012),
        (hd, Y_TEMPLE),
        (hd * 0.98, Y_CHEEK),
        (hd * 0.74, Y_JAW),
        (nk * 1.10, Y_CHIN),
        (nk, Y_NECK),
        (sh * 0.60, Y_SHOULDER + 0.012),
        (sh, Y_SHOULDER),                      # 어깨끝 = shoulder(어깨사이너비)/2
        (ch * 0.99, Y_CHEST),                  # 가슴 최대
        (wa, Y_WAIST),                         # 허리 최소
        (hp, Y_HIP),                           # 엉덩이 최대
        (hp * 0.985, Y_CROTCH),
        (hp * 0.80, 0.380),                    # 허벅지 바깥
        (hp * 0.63, Y_KNEE),                   # 무릎 바깥
        (hp * 0.62, Y_CALF),                   # 종아리 바깥
        (hp * 0.44, Y_ANKLE + 0.018),          # 발목 바깥
        (hp * 0.48, 0.020),
        (hp * 0.46, Y_SOLE),                   # 발끝 바깥
        (hp * 0.12, Y_SOLE),                   # 발끝 안쪽
        (hp * 0.17, Y_ANKLE),                  # 발목 안쪽
        (hp * 0.24, Y_CALF),
        (hp * 0.30, Y_KNEE),                   # 무릎 안쪽
        (hp * 0.21, 0.400),                    # 허벅지 안쪽
        (0.014, Y_CROTCH),                     # 가랑이
    ]


def torso_half_fn(pts):
    """y → 몸통 반너비. 팔을 몸통 바깥에 붙여 놓기 위해 필요하다."""
    seg = [(y, x) for x, y in pts[6:12]]        # 목~엉덩이 구간만
    seg.sort()
    ys = np.array([y for y, _ in seg])
    xs = np.array([x for _, x in seg])
    return lambda y: float(np.interp(y, ys, xs))


def arm_points(half_at, sh: float):
    """팔은 **몸통 윤곽 바깥에** 붙인다.

    팔 위치를 고정하면 둥근체형에서는 허리가 팔을 뚫고 나가고 모래시계에서는 붕 뜬다.
    `half_at(y) + 간격`으로 안쪽 선을 잡으면 6체형 전부에서 자동으로 자연스럽게 붙는다.
    """
    ys = [Y_CHEST, 0.690, 0.640, 0.590, Y_WAIST - 0.075, 0.485, 0.448, 0.410, 0.386]
    thick = [0.036, 0.034, 0.031, 0.029, 0.027, 0.025, 0.028, 0.029, 0.020]
    gap = [0.004, 0.007, 0.009, 0.010, 0.011, 0.012, 0.013, 0.013, 0.013]

    inner = [max(half_at(y), sh * 0.55) + g for y, g in zip(ys, gap)]
    outer = [i + t for i, t in zip(inner, thick)]

    right = [(sh, Y_SHOULDER)]                                  # 어깨끝에서 시작
    right += [(o, y) for o, y in zip(outer, ys)]                # 바깥 선 (어깨 → 손끝)
    right += [(i, y) for i, y in zip(reversed(inner), reversed(ys))]  # 안쪽 선 (손끝 → 겨드랑이)
    return right


def draw_figure(ax, dx: float, m: dict, style: dict):
    """6명 × 2성별 = 12명이 전부 이 함수 하나를 통과한다. 그림체가 갈라질 여지가 없다."""
    sh = m["shoulder"] / 2 * CM_TO_UNIT                     # 어깨는 이미 '너비'
    ch = m["chest"] * CIRC_TO_HALF * CM_TO_UNIT             # 나머지는 '둘레' → 반너비 환산
    wa = m["waist"] * CIRC_TO_HALF * CM_TO_UNIT
    hp = m["hip"] * CIRC_TO_HALF * CM_TO_UNIT
    nk, hd = style["neck"], style["head"]

    body = body_points(sh, ch, wa, hp, nk, hd)
    half_at = torso_half_fn(body)

    # 팔 (몸통보다 먼저 → 어깨선 위에 몸통이 덮이지 않도록 같은 zorder 유지)
    arm = arm_points(half_at, sh)
    stroke(ax, arm, dx=dx, closed=True)
    stroke(ax, [(-x, y) for x, y in arm], dx=dx, closed=True)

    # 몸통 + 머리 + 다리 (한 덩어리 실루엣 — 이음매가 생기지 않는다)
    stroke(ax, mirror_close(body), dx=dx, closed=True)

    # ── 얼굴 (최소한만. 표정이 들어가면 체형 비교에서 시선을 뺏는다)
    for s in (-1, 1):
        ax.plot([dx + s * 0.019, dx + s * 0.008], [Y_CHEEK + 0.005, Y_CHEEK + 0.005],
                color=INK, lw=LW_DETAIL, solid_capstyle="round", zorder=4)
    ax.plot([dx, dx], [Y_CHEEK - 0.004, Y_JAW + 0.014], color=INK, lw=LW_DETAIL,
            solid_capstyle="round", zorder=4)                       # 코
    ax.plot([dx - 0.009, dx + 0.009], [Y_JAW + 0.001, Y_JAW + 0.001], color=INK,
            lw=LW_DETAIL, solid_capstyle="round", zorder=4)         # 입

    # ── 머리카락 (성별 차이는 여기까지. 선의 성질은 동일하다)
    if style["long_hair"]:
        hair = [
            (0.000, Y_TOP + 0.006),
            (hd * 0.85, Y_TOP - 0.008),
            (hd * 1.22, Y_TEMPLE - 0.010),
            (hd * 1.30, 0.860),
            (hd * 1.26, Y_NECK - 0.060),
            (hd * 0.96, Y_NECK - 0.066),
            (hd * 0.92, 0.880),
            (hd * 0.86, Y_TEMPLE),
            (hd * 0.40, Y_CHEEK + 0.030),
        ]
        fill_white(ax, mirror_close(hair), dx=dx)
        stroke(ax, mirror_close(hair), dx=dx, closed=True, lw=LW_DETAIL)
    else:
        cap = [
            (0.000, Y_TOP + 0.005),
            (hd * 0.80, Y_TOP - 0.010),
            (hd * 1.06, Y_TEMPLE + 0.004),
            (hd * 1.00, Y_CHEEK + 0.026),
            (hd * 0.62, Y_CHEEK + 0.040),
            (hd * 0.26, Y_CHEEK + 0.028),
        ]
        fill_white(ax, mirror_close(cap), dx=dx)
        stroke(ax, mirror_close(cap), dx=dx, closed=True, lw=LW_DETAIL)

    # ── 쇄골
    for s in (-1, 1):
        stroke(ax, [(s * nk * 0.55, Y_NECK - 0.014), (s * sh * 0.55, Y_SHOULDER - 0.010)],
               dx=dx, lw=LW_DETAIL)

    # ── 가슴선 (여: 언더버스트, 남: 대흉근 아래선 — 같은 곡선 함수, 파라미터만 다름)
    b = style["bust"]
    for s in (-1, 1):
        stroke(ax, [
            (s * ch * 0.92, Y_CHEST + 0.036),
            (s * ch * 0.74, Y_CHEST + 0.036 - b * 0.55),
            (s * ch * 0.30, Y_CHEST + 0.030 - b),
            (s * ch * 0.10, Y_CHEST + 0.052 - b * 0.85),
        ], dx=dx, lw=LW_DETAIL)

    # ── 배꼽 (허리~엉덩이 중간). 세로 위치를 고정해 두면 허리 높이 비교의 기준점이 된다.
    ax.plot([dx, dx], [Y_WAIST - 0.040, Y_WAIST - 0.056], color=INK, lw=LW_DETAIL,
            solid_capstyle="round", zorder=4)


# ─────────────────────────────────────────────── 페이지

def pick_font() -> str:
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for korean in ("Malgun Gothic", "Noto Sans KR", "NanumGothic", "Gulim"):
        if korean in installed:
            return korean
    raise SystemExit("한글 폰트를 찾지 못했다 — 라벨이 전부 깨진다")


def render(sex: str, centroids: dict, out: Path) -> None:
    style = SEX_STYLE[sex]
    fig, ax = plt.subplots(figsize=(19.2, 10.2), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.set_xlim(-SLOT_W * 0.60, SLOT_W * (len(SHAPES) - 0.40))
    ax.set_ylim(-0.30, 1.20)
    ax.set_aspect("equal")

    ax.text(SLOT_W * (len(SHAPES) - 1) / 2, 1.155,
            f"사이즈코리아 6체형 비율 비교 — {style['label']}",
            fontsize=27, fontweight="bold", ha="center", va="center", color=INK)
    ax.text(SLOT_W * (len(SHAPES) - 1) / 2, 1.100,
            "8차 인체치수조사 5,092명 체형별 실측 평균(centroids_cm)을 그대로 폭에 반영. "
            "키는 6명 모두 동일 — 가로축은 크기가 아니라 모양을 나누는 축이다.",
            fontsize=13, ha="center", va="center", color=MUTED)

    x0, x1 = -SLOT_W * 0.52, SLOT_W * (len(SHAPES) - 1) + SLOT_W * 0.52
    for y, name in ((Y_SHOULDER, "어깨"), (Y_WAIST, "허리"), (Y_HIP, "엉덩이")):
        ax.plot([x0, x1], [y, y], ls=(0, (6, 5)), lw=LW_GUIDE, color=GUIDE, zorder=1)
        ax.text(x0 - 0.012, y, name, fontsize=11.5, ha="right", va="center", color=GUIDE)

    for i, (slug, korean) in enumerate(SHAPES):
        m = centroids[sex][slug]
        dx = i * SLOT_W
        draw_figure(ax, dx, m, style)

        ax.text(dx, -0.075, korean, fontsize=19, fontweight="bold",
                ha="center", va="center", color=INK)
        ax.text(dx, -0.130, slug, fontsize=12, ha="center", va="center",
                color=MUTED, family="Consolas")
        ax.text(dx, -0.190,
                f"어깨/엉덩이 {m['shoulder'] / m['hip']:.3f}\n허리/엉덩이 {m['waist'] / m['hip']:.3f}",
                fontsize=12.5, ha="center", va="center", color=INK, linespacing=1.6)
        ax.text(dx, -0.268,
                f"가슴 {m['chest']:.1f} · 허리 {m['waist']:.1f} · 엉덩이 {m['hip']:.1f} cm",
                fontsize=10.5, ha="center", va="center", color=MUTED)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", pad_inches=0.25, facecolor=BG)
    plt.close(fig)
    print(f"OK: {out.relative_to(ROOT).as_posix()} ({out.stat().st_size // 1024} KB)")


def main() -> int:
    plt.rcParams["font.family"] = pick_font()
    plt.rcParams["axes.unicode_minus"] = False

    centroids = json.loads(RULES.read_text(encoding="utf-8"))["centroids_cm"]
    missing = {s for _, sx in SEX_STYLE.items() for s in ()}  # noqa: F841 (자리표시)
    for sex in ("F", "M"):
        for slug, _ in SHAPES:
            if slug not in centroids[sex]:
                raise SystemExit(f"centroids_cm[{sex}]에 {slug}가 없다 — 임계값 JSON을 먼저 갱신할 것")

    render("F", centroids, OUT_DIR / "all-shapes-female.png")
    render("M", centroids, OUT_DIR / "all-shapes-male.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
