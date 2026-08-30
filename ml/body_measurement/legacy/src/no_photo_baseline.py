"""Legacy: 과거 7개/둘레 기준 VLM 평가 대상자의 **무사진 기준선** 산출.

처음 기준은 `thigh`, `calf`, `arm`을 둘레로 다루는 구조였고, 이후 현재의
11개 길이/비율 계약으로 바뀌었다. 이 파일은 현재 Swagger/API 계약에 사용하지 않는
참고용 archive다.

왜 이게 필요한가
----------------
`reports/model_evaluation_summary.md`는 Kimi K2.5가 Test 145명에서 평균 MAE
3.126~3.363cm를 냈다고 기록한다. 그런데 **같은 사람들을 사진 없이 성별·키·몸무게만으로
예측했을 때의 오차는 어디에도 계산돼 있지 않다.**

그 숫자가 없으면 "사진이 도움이 됐다"고 말할 근거가 없다. 1단계 보고서의 1.915cm는
SizeKorea 5,092명 모집단에서 나온 값이라 그대로 비교하면 안 된다 (모집단·측정
프로토콜이 다름 — docs/multimodal-model-selection.md §0).

판정식
------
    같은 split 행에서   MAE(VLM, 사진 사용)  <  MAE(tabular, 사진 없음)   → 사진이 이득
                        그 반대                                          → 사진이 손해

`baseline`이 하는 일
--------------------
1. SizeKorea 정제본으로 tabular 모델(knn / hist_gradient_boosting)을 학습한다.
2. VLM split(`data/splits/vlm/*.csv`)의 gender/height/weight로 예측한다.
3. 같은 행의 실측값과 비교해 부위별 MAE/RMSE/P90을 낸다.
4. split에 이미 들어 있는 VLM 예측(`pred_chest` 등)이 있으면 나란히 출력한다.

`match`가 하는 일
-----------------
split의 `chest`가 SizeKorea의 `가슴둘레`인지 `젖가슴둘레`인지 이름만으로는 알 수 없다.
두 항목은 평균 2.47cm 차이라 잘못 잡으면 모델 오차보다 큰 편향이 생긴다.
SizeKorea에서 후보 컬럼별로 KNN을 학습해 split을 예측하고, MAE가 가장 낮은 후보를
대응 항목으로 판정한다.

⚠️ 1·2위 MAE 격차가 `--min-margin`(기본 0.3cm) 미만이면 "구분 불가"로 보고,
   해당 부위는 사진 有/無 비교에서 제외하는 편이 안전하다.

⚠️ 선결 과제 — 평가셋 라벨의 출처
---------------------------------
이 모듈은 SizeKorea에서 학습해 split을 예측하므로 **두 쪽 측정 프로토콜이 같아야** 한다.
그런데 split의 `source`는 `sizkorea`인데도 실제로는 데이터셋 20일 정황이 강하다
(피험자 ID 체계, `front_camera_number` 컬럼, SizeKorea 정제본에 해당 키·몸무게 행 부재).
상세와 확정 방법은 `reports/model_evaluation_summary.md` §6-1 참조.
데이터셋 20이 맞다면 `chest`는 `젖가슴둘레`이므로, `match`로 확정하기 전에는
`CONFIRMED_MAPPING`에 chest를 넣지 말 것.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark import (  # noqa: E402
    DEFAULT_S3_URI,
    DEFAULT_SHEET,
    FEATURES,
    GENDER_CODES,
    RANDOM_STATE,
    build_model,
    download_s3_file,
)

# SizeKorea 원본은 길이·둘레가 mm, 체중만 kg다.
KG_COLUMNS = {"체중(몸무게)"}
GENDER_COLUMN = "성별"

# 이름만으로는 대응이 확정되지 않는 부위의 후보군.
MATCH_CANDIDATES: dict[str, list[str]] = {
    "chest": ["가슴둘레", "젖가슴둘레", "젖가슴아래둘레(여)"],
    "shoulder": ["어깨너비", "어깨사이길이", "어깨가쪽사이길이", "목뒤어깨사이길이"],
    "waist": ["허리둘레", "배꼽수준허리둘레", "가는허리둘레(여)"],
    "hip": ["엉덩이둘레", "배돌출점기준엉덩이둘레"],
    "thigh": ["넙다리둘레", "넙다리중간둘레"],
    "calf": ["장딴지둘레"],
    "arm": ["(편)위팔둘레", "(팔굽힌)위팔둘레"],
}

# match로 확정되기 전까지의 기본 매핑. 확정되면 이 표를 갱신한다.
CONFIRMED_MAPPING: dict[str, str] = {
    "waist": "허리둘레",
    "hip": "엉덩이둘레",
    "thigh": "넙다리둘레",
    "calf": "장딴지둘레",
    "arm": "(편)위팔둘레",
}

# VLM이 실제로 예측한 부위 — split CSV에 pred_* 컬럼이 있는 것들.
VLM_TARGETS = ("chest", "waist", "hip")


def load_sizekorea_columns(
    path: Path,
    names: list[str],
    *,
    sheet: str = DEFAULT_SHEET,
) -> pd.DataFrame:
    """SizeKorea 시트에서 임의의 한글 측정항목명 컬럼들을 뽑아 cm/kg로 맞춘다.

    benchmark.load_sizekorea_excel은 과거 서비스 필드로 고정돼 있어 재사용할 수 없다.
    후보 컬럼을 자유롭게 지정해야 하므로 헤더 탐색 방식만 같게 가져왔다.
    """

    header = pd.read_excel(
        path, sheet_name=sheet, header=None, skiprows=4, nrows=1, engine="openpyxl"
    ).iloc[0]

    wanted = [GENDER_COLUMN, "키", "체중(몸무게)", *names]
    indexes: dict[str, int] = {}
    for name in wanted:
        matches = [
            int(index)
            for index, value in header.items()
            if isinstance(value, str) and value.strip() == name
        ]
        if len(matches) != 1:
            raise ValueError(f"`{name}` 컬럼을 1개 찾을 수 없습니다: {matches}")
        indexes[name] = matches[0]

    order = sorted(indexes.values())
    raw = pd.read_excel(
        path, sheet_name=sheet, header=6, usecols=order, engine="openpyxl"
    )
    raw.columns = [
        next(name for name, index in indexes.items() if index == position)
        for position in order
    ]

    frame = pd.DataFrame()
    frame["gender"] = raw[GENDER_COLUMN].astype("string").str.strip().str.upper()
    for name in ["키", "체중(몸무게)", *names]:
        values = pd.to_numeric(raw[name], errors="coerce")
        frame[name] = values if name in KG_COLUMNS else values / 10.0
    frame = frame.rename(columns={"키": "height", "체중(몸무게)": "weight"})
    return frame[frame["gender"].isin(GENDER_CODES)].reset_index(drop=True)


def load_splits(paths: list[Path]) -> pd.DataFrame:
    """VLM split CSV 여러 개를 하나로 합친다. `split` 컬럼으로 출처를 남긴다."""

    frames = []
    for path in paths:
        frame = pd.read_csv(path)
        frame["split"] = path.stem
        frames.append(frame)
    merged = pd.concat(frames, ignore_index=True)
    merged["gender"] = merged["gender"].astype("string").str.strip().str.upper()
    return merged


def drop_impossible_rows(frame: pd.DataFrame, targets: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """정답지 자체가 잘못된 행을 걸러낸다.

    model_evaluation_summary.md §6이 기록한 정답 오류 2건(가슴 8.2cm 등)을
    지표에서 빼기 위한 것이다. 판정 기준도 그 문서와 같게 맞췄다.
    """

    limits = {"chest": 60.0, "hip": 60.0, "waist": 40.0}
    mask = pd.Series(True, index=frame.index)
    for target in targets:
        if target in limits and target in frame.columns:
            mask &= frame[target] >= limits[target]
    return frame[mask].reset_index(drop=True), frame[~mask].reset_index(drop=True)


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    features = frame[FEATURES].copy()
    features["gender"] = features["gender"].map(GENDER_CODES)
    return features


def transfer_metrics(
    sizekorea: pd.DataFrame,
    evaluation: pd.DataFrame,
    *,
    source_column: str,
    target_column: str,
    model_name: str = "knn",
) -> dict[str, float]:
    """SizeKorea에서 학습해 평가셋을 예측했을 때의 오차를 잰다."""

    train = sizekorea[["gender", "height", "weight", source_column]].dropna()
    test = evaluation[["gender", "height", "weight", target_column]].dropna()

    model = build_model(model_name)
    model.fit(_features(train), train[[source_column]])
    predicted = np.asarray(model.predict(_features(test))).reshape(-1)
    actual = test[target_column].to_numpy(dtype=float)
    error = predicted - actual
    return {
        "mae": float(np.mean(np.abs(error))),
        "bias": float(np.mean(error)),  # 부호 있는 평균 오차 = 계통 편향
        "rmse": float(np.sqrt(np.mean(error**2))),
        "p90": float(np.percentile(np.abs(error), 90)),
        "n_train": int(len(train)),
        "n_test": int(len(test)),
    }


def vlm_metrics(frame: pd.DataFrame, target: str) -> dict[str, float] | None:
    """split CSV에 이미 들어 있는 VLM 예측의 오차를 같은 방식으로 계산한다.

    ⚠️ split의 `pred_*` 컬럼에는 **어느 모델이 낸 값인지 기록돼 있지 않다.**
    모델을 특정해 비교하려면 `--vlm-predictions`로
    `experiments/vlm/{모델}/{run}/predictions.csv`를 직접 지정할 것.
    """

    column = f"pred_{target}"
    if column not in frame.columns:
        return None
    rows = frame[[target, column]].dropna()
    if rows.empty:
        return None
    error = rows[column].to_numpy(dtype=float) - rows[target].to_numpy(dtype=float)
    return {
        "mae": float(np.mean(np.abs(error))),
        "bias": float(np.mean(error)),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "p90": float(np.percentile(np.abs(error), 90)),
        "n_test": int(len(rows)),
    }


def command_match(args: argparse.Namespace) -> None:
    """부위별로 후보 컬럼을 전부 시험해 어느 정의가 맞는지 순위를 낸다."""

    evaluation = load_splits(args.splits)
    targets = args.targets or list(MATCH_CANDIDATES)
    names = sorted({name for target in targets for name in MATCH_CANDIDATES[target]})
    if args.drop_impossible:
        evaluation, _ = drop_impossible_rows(evaluation, targets)
    sizekorea = load_sizekorea_columns(
        download_s3_file(args.s3_uri, args.cache_dir), names
    )

    rows = [
        {
            "target": target,
            "candidate": candidate,
            **transfer_metrics(
                sizekorea,
                evaluation,
                source_column=candidate,
                target_column=target,
            ),
        }
        for target in targets
        for candidate in MATCH_CANDIDATES[target]
    ]

    report = pd.DataFrame(rows).sort_values(["target", "mae"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(args.out, index=False)

    print(report.to_string(index=False))
    print()
    for target in targets:
        block = report[report["target"] == target].reset_index(drop=True)
        best = block.iloc[0]
        margin = float(block.iloc[1]["mae"] - best["mae"]) if len(block) > 1 else np.inf
        verdict = "확정" if margin >= args.min_margin else "구분 불가 → 비교 제외 권고"
        print(
            f"{target:9s} → {best['candidate']:20s} "
            f"MAE {best['mae']:.3f}cm  2위와 격차 {margin:.3f}cm  [{verdict}]"
        )
    print(f"\n저장: {args.out}")


def command_baseline(args: argparse.Namespace) -> None:
    """무사진 기준선을 산출하고, 같은 행의 VLM 성적과 나란히 비교한다."""

    mapping = (
        json.loads(args.mapping.read_text(encoding="utf-8"))
        if args.mapping
        else CONFIRMED_MAPPING
    )
    evaluation = load_splits(args.splits)
    if args.drop_impossible:
        evaluation, dropped = drop_impossible_rows(evaluation, list(mapping))
        if len(dropped):
            print(f"정답 오류로 제외한 행 {len(dropped)}개: "
                  f"{dropped['subject_id'].tolist()}\n")
    sizekorea = load_sizekorea_columns(
        download_s3_file(args.s3_uri, args.cache_dir), sorted(set(mapping.values()))
    )

    rows: list[dict[str, object]] = []
    for model_name in args.models:
        for target, source_column in mapping.items():
            rows.append(
                {
                    "model": model_name,
                    "photo": False,
                    "target": target,
                    "source_column": source_column,
                    **transfer_metrics(
                        sizekorea,
                        evaluation,
                        source_column=source_column,
                        target_column=target,
                        model_name=model_name,
                    ),
                }
            )
    if args.vlm_predictions:
        # 모델명이 명시된 experiments 예측 파일을 쓴다 (provenance 확실).
        predictions = pd.read_csv(args.vlm_predictions)
        label = str(predictions["model"].iloc[0]) if "model" in predictions else args.vlm_predictions.stem
        predictions = predictions.rename(
            columns={f"actual_{t}": t for t in VLM_TARGETS}
        )
        vlm_frame, vlm_label = predictions, f"vlm({label})"
    else:
        vlm_frame, vlm_label = evaluation, "vlm(split 저장값·모델 미기록)"
        print("⚠️ split의 pred_* 컬럼에는 모델명이 없다. 특정 모델과 비교하려면 "
              "--vlm-predictions로 experiments 예측 파일을 지정할 것.\n")

    for target in VLM_TARGETS:
        metrics = vlm_metrics(vlm_frame, target)
        if metrics:
            rows.append(
                {
                    "model": vlm_label,
                    "photo": True,
                    "target": target,
                    "source_column": "-",
                    **metrics,
                }
            )

    detail = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(args.out, index=False)

    print("[부위별]")
    print(detail.to_string(index=False))

    # 사진 有/無를 공정하게 비교하려면 두 쪽 모두 예측한 부위만 놓고 봐야 한다.
    shared = [t for t in VLM_TARGETS if t in mapping and (detail["target"] == t).any()]
    if shared:
        comparison = (
            detail[detail["target"].isin(shared)]
            .groupby(["model", "photo"])["mae"]
            .mean()
            .reset_index()
            .sort_values("mae")
        )
        print(f"\n[사진 有/無 비교 — 공통 부위 {', '.join(shared)}]")
        print(comparison.to_string(index=False))
        print("\n판정: 사진 사용(photo=True) MAE가 더 낮아야 사진 투입이 정당화된다.")
    else:
        print("\n⚠️ VLM 예측 컬럼(pred_*)과 겹치는 부위가 없어 사진 有/無 비교를 못 했다.")
    print(f"\n저장: {args.out}")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    splits = root / "data" / "splits" / "vlm"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--splits",
        type=Path,
        nargs="+",
        default=[splits / "test_set.csv", splits / "validation_set.csv"],
    )
    parser.add_argument("--s3-uri", default=DEFAULT_S3_URI)
    parser.add_argument("--cache-dir", type=Path, default=root / "data" / "raw")
    parser.add_argument(
        "--drop-impossible",
        action="store_true",
        help="정답 오류 행(가슴·엉덩이 60cm 미만 등)을 제외한다",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    match = sub.add_parser("match", help="chest/shoulder 등 컬럼 정의를 측정으로 확정")
    match.add_argument("--targets", nargs="*", choices=list(MATCH_CANDIDATES))
    match.add_argument(
        "--min-margin",
        type=float,
        default=0.3,
        help="1위와 2위 MAE 격차가 이 값 미만이면 구분 불가로 본다 (cm)",
    )
    match.add_argument(
        "--out", type=Path, default=root / "reports" / "vlm_split_column_match.csv"
    )
    match.set_defaults(func=command_match)

    baseline = sub.add_parser("baseline", help="무사진 기준선 + 사진 有/無 비교")
    baseline.add_argument(
        "--models", nargs="*", default=["knn", "hist_gradient_boosting"]
    )
    baseline.add_argument(
        "--mapping",
        type=Path,
        help="확정된 {target: SizeKorea컬럼} JSON. 없으면 CONFIRMED_MAPPING 사용",
    )
    baseline.add_argument(
        "--vlm-predictions",
        type=Path,
        help="experiments/vlm/{모델}/{run}/predictions.csv — 모델명이 기록된 예측 파일",
    )
    baseline.add_argument(
        "--out", type=Path, default=root / "reports" / "no_photo_baseline.csv"
    )
    baseline.set_defaults(func=command_baseline)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    np.random.seed(RANDOM_STATE)
    args.func(args)


if __name__ == "__main__":
    main()
