"""팀 드라이브 원본 폴더 → 본 검수용 metadata.csv와 표집 배치.

파일럿은 10장을 손으로 적어 `local/golden-pilot/metadata.csv`를 만들었다. 본 검수는
수집자 4명이 각자 다른 규칙으로 모은 수백 장이라 같은 방법을 쓸 수 없다. 이 모듈이
푸는 문제는 셋이다.

1. **파일명 충돌** — 수집자마다 이름 규칙이 다르고(`001.jpg` 연번, 핀터레스트 원본명,
   해시 이름) 성별 폴더 사이에서도 겹친다. 검수 화면은 경로가 아니라 파일 이름으로
   이미지를 찾으므로, 겹친 이름을 그대로 두면 다른 사진이 뜬다. 여기서 폴더 정보를
   이름에 접어 넣어(`shj-m-casual-001.jpg`) 평면 폴더에서도 유일하게 만든다.
2. **스타일 라벨 소실** — 신혜지 폴더만 20개 스타일로 나뉘어 있고 나머지는 평면이다.
   폴더명을 taxonomy 어휘로 옮겨 `style`에 싣고, 원본 폴더명은 `style_source_label`에
   남긴다. 매핑이 틀렸을 때 되짚을 근거가 있어야 한다.
3. **검수 물량** — 이미지 1장이 관찰 1행 + claim 최대 3행이라 전량 검수는 불가능하다.
   전체는 인벤토리로 남기고, 수집자·성별·스타일을 고르게 훑는 배치만 잘라낸다.

`metadata.csv`는 `manifest.build_manifest(metadata_csv=...)`가 읽는 스키마 그대로이고,
뒤에 붙은 열(collector, original_relpath 등)은 사람이 추적할 때만 쓴다 — manifest는
아는 열만 읽으므로 남는 열은 무시된다.
"""

from __future__ import annotations

import csv
import hashlib
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

#: 검수 화면과 파이프라인이 함께 읽는 이미지 확장자. 목록 밖 파일은 조용히 빼지 않고
#: 요약에 남긴다 — 드라이브에는 확장자 없이 올라온 파일이 실제로 섞여 있다.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

#: 성별 폴더 이름 → 표준 값. manifest.normalize_presentation_group과 같은 어휘를 쓴다.
GENDER_DIRS = {
    "남자": "men", "남성": "men", "men": "men", "man": "men", "male": "men",
    "여자": "women", "여성": "women", "women": "women", "woman": "women",
    "female": "women",
}

#: 스타일 폴더명 → (taxonomy STYLES 값, 파일명 슬러그).
#:
#: 드라이브 폴더명은 "[13] 블록코어 Blokecore"처럼 번호와 영문이 섞여 있어 키를 부분
#: 문자열로 찾는다. taxonomy(image-processor pipeline.taxonomy.STYLES)에 없는 이름은
#: 새로 만들지 않고 가장 가까운 값 1~2개로 옮긴다 — 어휘를 늘리면 리트리버 필터가
#: 조용히 갈라진다. 옮긴 결과가 애매한 항목(고프코어→아웃도어, 웨스턴룩→빈티지 등)은
#: style_source_label로 원본을 남겨 두었으니 검수 때 확인하고 고치면 된다.
STYLE_MAP: dict[str, tuple[list[str], str]] = {
    "캐주얼룩": (["캐주얼"], "casual"),
    "스트릿룩": (["스트릿"], "street"),
    "미니멀룩": (["미니멀"], "minimal"),
    "시티보이룩": (["캐주얼", "베이직"], "cityboy"),
    "아메카지룩": (["아메카지"], "amekaji"),
    "프레피룩": (["댄디", "캐주얼"], "preppy"),
    "빈티지룩": (["빈티지"], "vintage"),
    "걸리시룩": (["러블리"], "girlish"),
    "페미닌룩": (["페미닌"], "feminine"),
    "스포티룩": (["스포티"], "sporty"),
    "Y2K": (["트렌디", "스트릿"], "y2k"),
    "고프코어": (["아웃도어"], "gorpcore"),
    "블록코어": (["스포티", "스트릿"], "blokecore"),
    "발레코어": (["러블리", "페미닌"], "balletcore"),
    "코켓코어": (["러블리", "페미닌"], "coquettecore"),
    "클래식 룩": (["포멀", "베이직"], "classic"),
    "보헤미안 시크": (["빈티지", "리조트"], "boho"),
    "웨스턴룩": (["빈티지"], "western"),
    "리조트룩": (["리조트"], "resort"),
    "포엣코어": (["페미닌", "빈티지"], "poetcore"),
}

#: 스타일 폴더가 없는 수집자의 슬러그. 빈 문자열을 쓰면 파일명에 `--`가 생긴다.
NO_STYLE_SLUG = "na"


@dataclass(frozen=True)
class Collector:
    """수집자 한 명의 원본 폴더.

    `rel_path`는 `--root` 기준 상대 경로다. 신혜지 폴더만 성별 폴더가 루트에 바로
    풀려 있어 "."이며, 성별 이름이 아닌 하위 폴더는 스캔에서 빠지므로 다른 수집자
    폴더를 같은 루트에 내려받아도 섞이지 않는다.
    """

    code: str
    name: str
    rel_path: str
    drive_url: str
    style_labeled: bool = False


#: 기본 배치. 박건우 폴더는 팀 결정으로 이번 골든셋에서 제외한다.
DEFAULT_COLLECTORS: list[Collector] = [
    Collector(
        code="shj",
        name="신혜지",
        rel_path=".",
        drive_url="https://drive.google.com/drive/folders/1QnJeSLAqV4y4FFWkZpqcyTEykmVoDPuU",
        style_labeled=True,
    ),
    Collector(
        code="kmw",
        name="김민욱",
        rel_path="김민욱",
        drive_url="https://drive.google.com/drive/folders/1spV29pDJS9HCgbYCmTewYPHFP58SPOfl",
    ),
    Collector(
        code="jhy",
        name="전하영",
        rel_path="전하영",
        drive_url="https://drive.google.com/drive/folders/1eWh08xHI0NsPhLDRHSTUkwx6OvhGz10T",
    ),
    Collector(
        code="lkw",
        name="이건우",
        rel_path="이건우",
        drive_url="https://drive.google.com/drive/folders/1b2dj8XarOYXs64tBmQpnC7tjrv7xbYXZ",
    ),
]

#: metadata.csv 열. 앞의 13개는 manifest가 읽는 계약이고 뒤는 사람용 추적 정보다.
METADATA_COLUMNS = [
    "file_name", "golden_id", "source", "source_uri", "usage_scope",
    "original_exposable", "presentation_group", "style", "season", "occasion",
    "selection_reason", "same_shoot_group", "split",
    "collector", "style_source_label", "original_relpath", "image_sha256",
    "duplicate_of", "review_batch",
]


@dataclass
class ImageRecord:
    collector: Collector
    presentation_group: str
    style_slug: str
    style_values: list[str]
    style_source_label: str
    source_path: Path
    original_relpath: str
    file_name: str = ""
    image_sha256: str = ""
    duplicate_of: str = ""
    review_batch: str = ""

    @property
    def golden_id(self) -> str:
        return Path(self.file_name).stem


@dataclass
class ScanResult:
    records: list[ImageRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing_collectors: list[str] = field(default_factory=list)
    unmapped_styles: list[str] = field(default_factory=list)


def _taxonomy_styles() -> set[str] | None:
    """image-processor의 STYLES. 검증할 수 없으면 None.

    STYLE_MAP이 옮겨 놓는 값은 리트리버가 필터로 쓰는 키라서 taxonomy 밖 값이 하나만
    섞여도 그 코디는 검색에서 통째로 빠진다. 컨테이너 밖에서 돌 때도 확인이 되도록
    리포 안의 image-processor를 sys.path에 얹어 본다. 그래도 안 되면 None을 돌려
    "검증 못 함"과 "검증 통과"를 호출부가 구분하게 한다 — 둘을 같은 빈 목록으로
    뭉개면 오타가 조용히 통과한다.
    """
    candidate = Path(__file__).resolve().parents[2] / "image-processor"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
    try:
        from pipeline.taxonomy import STYLES
    except ImportError:
        return None
    return set(STYLES)


def _match_style(folder_name: str) -> tuple[list[str], str, str] | None:
    """스타일 폴더명에서 (taxonomy 값, 슬러그, 원본 라벨)을 뽑는다."""
    label = re.sub(r"^\s*\[\d+\]\s*", "", folder_name).strip()
    for key, (values, slug) in STYLE_MAP.items():
        if key in folder_name:
            return list(values), slug, label
    return None


def resolve_collector_dir(root: Path, rel_path: str) -> Path | None:
    """수집자 폴더를 찾는다. 드라이브 zip이 씌우는 래퍼 한 겹을 넘는다.

    구글 드라이브에서 폴더를 내려받으면 `김민욱-20260816T160950Z-1-001/김민욱/`처럼
    타임스탬프 래퍼가 한 겹 생긴다. 받는 시각마다 이름이 달라져 설정에 적어 둘 수 없고,
    사람에게 매번 폴더를 옮기게 하면 그 단계에서 실수가 난다.
    """
    direct = root / rel_path
    if direct.is_dir():
        return direct.resolve()

    prefix = f"{rel_path}-"
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not child.name.startswith(prefix):
            continue
        nested = child / rel_path
        if nested.is_dir():
            return nested.resolve()
    return None


def _iter_images(directory: Path) -> list[Path]:
    return sorted(
        (p for p in directory.iterdir() if p.is_file()),
        key=lambda p: p.name.casefold(),
    )


def scan(root: Path, collectors: list[Collector]) -> ScanResult:
    """원본 루트를 훑어 이미지 레코드를 만든다 (파일명 부여 전)."""
    result = ScanResult()

    for collector in collectors:
        base = resolve_collector_dir(root, collector.rel_path)
        if base is None:
            result.missing_collectors.append(
                f"{collector.name}: {root / collector.rel_path}"
            )
            continue

        for gender_dir in sorted(base.iterdir(), key=lambda p: p.name):
            if not gender_dir.is_dir():
                continue
            group = GENDER_DIRS.get(gender_dir.name.strip().casefold())
            if group is None:
                # 성별 폴더가 아니면 다른 수집자 폴더이거나 잡폴더다. rel_path="."인
                # 수집자가 남의 폴더를 삼키지 않도록 여기서 끊는다.
                continue

            buckets: list[tuple[list[str], str, str, Path]] = []
            direct = _iter_images(gender_dir)
            if direct:
                buckets.append(([], NO_STYLE_SLUG, "", gender_dir))
            if collector.style_labeled:
                for style_dir in sorted(gender_dir.iterdir(), key=lambda p: p.name):
                    if not style_dir.is_dir():
                        continue
                    matched = _match_style(style_dir.name)
                    if matched is None:
                        result.unmapped_styles.append(
                            f"{collector.name}/{gender_dir.name}/{style_dir.name}"
                        )
                        buckets.append(([], NO_STYLE_SLUG, style_dir.name, style_dir))
                        continue
                    values, slug, label = matched
                    buckets.append((values, slug, label, style_dir))

            for values, slug, label, image_dir in buckets:
                for path in _iter_images(image_dir):
                    relpath = path.relative_to(root).as_posix()
                    if path.suffix.lower() not in IMAGE_SUFFIXES:
                        result.skipped.append(relpath)
                        continue
                    result.records.append(
                        ImageRecord(
                            collector=collector,
                            presentation_group=group,
                            style_slug=slug,
                            style_values=list(values),
                            style_source_label=label,
                            source_path=path,
                            original_relpath=relpath,
                        )
                    )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assign_names(records: list[ImageRecord]) -> None:
    """`{수집자}-{성별}-{스타일}-{연번}` 파일명을 붙이고 중복을 표시한다.

    연번은 (수집자, 성별, 스타일) 묶음마다 1부터 센다. 세 자리로 고정하는 이유는
    평면 폴더가 100장을 넘어 자릿수가 섞이면 이름 정렬이 번호 순서와 어긋나기
    때문이다.
    """
    counters: Counter[tuple[str, str, str]] = Counter()
    seen_sha: dict[str, str] = {}

    for record in records:
        key = (record.collector.code, record.presentation_group, record.style_slug)
        counters[key] += 1
        gender_code = "m" if record.presentation_group == "men" else "w"
        stem = (
            f"{record.collector.code}-{gender_code}-"
            f"{record.style_slug}-{counters[key]:03d}"
        )
        record.file_name = f"{stem}{record.source_path.suffix.lower()}"

        record.image_sha256 = _sha256(record.source_path)
        # 같은 사진이 수집자끼리 겹치는 일이 실제로 있다. 지우지는 않고(누가 무엇을
        # 모았는지가 기록이다) 표집에서만 뺀다.
        record.duplicate_of = seen_sha.get(record.image_sha256, "")
        seen_sha.setdefault(record.image_sha256, record.golden_id)


def select_batch(
    records: list[ImageRecord],
    size: int,
    batch_label: str,
    *,
    exclude_ids: frozenset[str] = frozenset(),
    quotas: dict[str, int] | None = None,
) -> list[ImageRecord]:
    """수집자 → 성별 → 스타일을 고르게 훑어 검수 배치를 고른다.

    무작위 표집을 쓰지 않는다. 이 배치의 목적은 모집단 추정이 아니라 "판단이 갈리는
    조건을 빠짐없이 한 번씩 보는 것"이고, 결정적이어야 배치를 늘릴 때 앞 배치와
    겹치지 않게 이어붙일 수 있다.

    `exclude_ids`로 앞 배치를 빼고 이어 뽑는다. 배치를 늘릴 때 size만 키우면 알고리즘이
    바뀔 때 앞 배치까지 달라지는데, 이미 검수를 시작한 뒤라면 그건 쓸 수 없다.

    `quotas`는 수집자별 상한이다. 수집자를 균등하게 뽑는 것이 언제나 옳지는 않다 —
    스타일 폴더로 나눠 모은 수집자 한 명에게 다양성이 몰려 있으면, 균등 표집은 비슷한
    코디만 잔뜩 가져오고 정작 채워야 할 스타일 셀은 비운다.
    """
    quotas = quotas or {}
    taken: Counter[str] = Counter()
    pool: dict[str, dict[str, dict[str, list[ImageRecord]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for record in records:
        if record.duplicate_of or record.golden_id in exclude_ids:
            continue
        pool[record.collector.code][record.presentation_group][record.style_slug].append(record)

    # 수집자 → 성별 → 스타일 순으로 라운드로빈 큐를 만든다.
    queues: list[tuple[str, list[ImageRecord]]] = []
    for collector_code in sorted(pool):
        for group in sorted(pool[collector_code]):
            style_queues = [pool[collector_code][group][slug] for slug in sorted(pool[collector_code][group])]
            merged: list[ImageRecord] = []
            index = 0
            while any(index < len(q) for q in style_queues):
                for queue in style_queues:
                    if index < len(queue):
                        merged.append(queue[index])
                index += 1
            queues.append((collector_code, merged))

    selected: list[ImageRecord] = []
    index = 0
    while len(selected) < size and any(index < len(q) for _, q in queues):
        for collector_code, queue in queues:
            if len(selected) >= size:
                break
            if index >= len(queue):
                continue
            limit = quotas.get(collector_code)
            if limit is not None and taken[collector_code] >= limit:
                continue
            selected.append(queue[index])
            taken[collector_code] += 1
        index += 1

    for record in selected:
        record.review_batch = batch_label
    return selected


def _row(record: ImageRecord) -> dict[str, str]:
    return {
        "file_name": record.file_name,
        "golden_id": record.golden_id,
        "source": "team-google-drive",
        "source_uri": record.collector.drive_url,
        "usage_scope": "INTERNAL",
        "original_exposable": "false",
        "presentation_group": record.presentation_group,
        "style": ";".join(record.style_values),
        "season": "",
        "occasion": "",
        "selection_reason": "",
        "same_shoot_group": "",
        "split": "KNOWLEDGE",
        "collector": record.collector.name,
        "style_source_label": record.style_source_label,
        "original_relpath": record.original_relpath,
        "image_sha256": record.image_sha256,
        "duplicate_of": record.duplicate_of,
        "review_batch": record.review_batch,
    }


def _read_golden_ids(paths: list[Path]) -> frozenset[str]:
    """앞 배치 metadata CSV에서 golden_id를 모은다. 이어 뽑을 때 제외할 대상이다."""
    ids: set[str] = set()
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                value = (row.get("golden_id") or "").strip()
                if value:
                    ids.add(value)
    return frozenset(ids)


def _write_csv(path: Path, records: list[ImageRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METADATA_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow(_row(record))


def _write_rename_map(path: Path, records: list[ImageRecord]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["original_relpath", "file_name", "image_sha256", "duplicate_of"])
        for record in records:
            writer.writerow(
                [record.original_relpath, record.file_name, record.image_sha256, record.duplicate_of]
            )


def _write_summary(
    path: Path,
    *,
    root: Path,
    records: list[ImageRecord],
    batch: list[ImageRecord],
    scan_result: ScanResult,
    unknown_styles: list[str],
    style_check: str,
) -> None:
    by_collector: Counter[str] = Counter()
    by_collector_group: Counter[tuple[str, str]] = Counter()
    by_style: Counter[str] = Counter()
    for record in records:
        by_collector[record.collector.name] += 1
        by_collector_group[(record.collector.name, record.presentation_group)] += 1
        by_style[record.style_source_label or "(라벨 없음)"] += 1

    lines = [
        "# 골든셋 본 검수 인벤토리",
        "",
        f"- 원본 루트: `{root}`",
        f"- 전체 이미지: {len(records)}장",
        f"- 중복(동일 sha256): {sum(1 for r in records if r.duplicate_of)}장",
        f"- 검수 배치: {len(batch)}장",
        f"- taxonomy STYLES 대조: {style_check}",
        "",
        "## 수집자·성별",
        "",
        "| 수집자 | men | women | 합계 |",
        "|---|---:|---:|---:|",
    ]
    for name in sorted(by_collector):
        men = by_collector_group[(name, "men")]
        women = by_collector_group[(name, "women")]
        lines.append(f"| {name} | {men} | {women} | {by_collector[name]} |")

    lines += ["", "## 스타일 라벨", "", "| 원본 폴더 라벨 | 장수 |", "|---|---:|"]
    for label in sorted(by_style):
        lines.append(f"| {label} | {by_style[label]} |")

    if scan_result.missing_collectors:
        lines += ["", "## 아직 내려받지 않은 수집자 폴더", ""]
        lines += [f"- {item}" for item in scan_result.missing_collectors]
    if scan_result.skipped:
        lines += ["", "## 확장자가 없어 제외된 파일", ""]
        lines += [f"- `{item}`" for item in scan_result.skipped]
    if scan_result.unmapped_styles:
        lines += ["", "## STYLE_MAP에 없는 폴더", ""]
        lines += [f"- `{item}`" for item in scan_result.unmapped_styles]
    if unknown_styles:
        lines += ["", "## taxonomy STYLES에 없는 매핑 값", ""]
        lines += [f"- `{item}`" for item in unknown_styles]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_images(records: list[ImageRecord], target: Path) -> int:
    """정규화한 이름으로 평면 폴더에 복사한다. 원본은 건드리지 않는다.

    복사 전에 폴더를 비운다. 표집은 원본이 늘거나 배치 크기가 바뀌면 다른 100장을
    고르는데, 남아 있던 이전 회차 파일이 그대로 섞이면 검수자가 검수표에 없는 사진을
    보게 된다. 실제로 202장 기준 배치와 776장 기준 배치가 겹쳐 176장이 된 적이 있다.
    """
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for record in records:
        shutil.copy2(record.source_path, target / record.file_name)
    return len(records)


def build_review_manifest(
    *,
    root: Path,
    out_dir: Path,
    collectors: list[Collector] | None = None,
    batch_size: int = 100,
    batch_label: str = "batch1",
    apply_rename: bool = False,
    exclude_csvs: list[Path] | None = None,
    quotas: dict[str, int] | None = None,
) -> dict[str, object]:
    """원본 루트를 훑어 metadata·표집 배치·리네임 결과를 만든다."""
    collectors = collectors or DEFAULT_COLLECTORS
    root = root.resolve()
    scan_result = scan(root, collectors)
    records = scan_result.records
    assign_names(records)
    exclude_ids = _read_golden_ids(exclude_csvs or [])
    batch = select_batch(
        records,
        batch_size,
        batch_label,
        exclude_ids=exclude_ids,
        quotas=quotas,
    )

    allowed = _taxonomy_styles()
    unknown_styles: list[str] = []
    style_check = "검증 못 함 (image-processor taxonomy를 불러오지 못함)"
    if allowed is not None:
        used = {value for values, _ in STYLE_MAP.values() for value in values}
        unknown_styles = sorted(used - allowed)
        style_check = "통과" if not unknown_styles else f"{len(unknown_styles)}건 불일치"

    out_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = out_dir / "metadata.csv"
    batch_path = out_dir / f"metadata.{batch_label}.csv"
    _write_csv(metadata_path, records)
    _write_csv(batch_path, batch)
    _write_rename_map(out_dir / "rename_map.csv", records)
    _write_summary(
        out_dir / "inventory_summary.md",
        root=root,
        records=records,
        batch=batch,
        scan_result=scan_result,
        unknown_styles=unknown_styles,
        style_check=style_check,
    )

    copied = 0
    batch_copied = 0
    if apply_rename:
        copied = _copy_images(records, out_dir / "images")
        batch_copied = _copy_images(batch, out_dir / f"images-{batch_label}")

    return {
        "total": len(records),
        "batch": len(batch),
        "duplicates": sum(1 for r in records if r.duplicate_of),
        "skipped": len(scan_result.skipped),
        "missing_collectors": scan_result.missing_collectors,
        "unmapped_styles": scan_result.unmapped_styles,
        "unknown_styles": unknown_styles,
        "style_check": style_check,
        "metadata_csv": metadata_path,
        "batch_csv": batch_path,
        "out_dir": out_dir,
        "copied": copied,
        "batch_copied": batch_copied,
    }
