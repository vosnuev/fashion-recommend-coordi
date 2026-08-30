"""Gemini 3.1 Flash Image 단일 모델 — 편집 + Confluence 태그 프로퍼티 테스트.

test-llm(5개 모델 비교)에서 gemini-3.1-flash-image 경로만 가져오고,
편집 결과 이미지를 Confluence 「의류 상품 데이터 카테고리-태그 매핑 문서」
스키마로 태깅해 items.json에 프로퍼티를 추가한다.

흐름:
  ① 열거: Gemini 비전이 사진 속 아이템 목록 생성 (이미지당 1회, 캐시)
  ② 편집: gemini-3.1-flash-image가 아이템별 흰 배경 정면 상품 이미지 생성
  ③ 태깅: 편집 결과 이미지를 Gemini structured output으로 태깅
     (item_name, category_large/small, season, style, color, pattern,
      fit, material, sleeve, length, usage, layer_role, layer_order)
  ④ 검증: 문서 §5-2 대분류별 필수 필드 누락을 _missing_required로 기록
  ⑤ 저장: output/gemini-3.1-flash-image/<이미지명>/item_XX_<대분류>.png
          + items.json

실행:
  python run_all.py                # input/ 전체
  python run_all.py img1.jpg       # 특정 이미지만
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))

from common.enumerator import GeminiEnumerator, enumerate_with_cache  # noqa: E402
from common.prompts import build_edit_prompt  # noqa: E402
from common.providers import PROVIDER  # noqa: E402
from common.tagger import ConfluenceTagger  # noqa: E402
from common.taxonomy import missing_required  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def load_env() -> None:
    """test-llm2/.env → 저장소 루트 .env 순으로 로드 (기존 값 우선)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for env in (ROOT / ".env", ROOT.parent.parent / ".env"):
        if env.is_file():
            load_dotenv(env, override=False)


def collect_input_images(input_dir: Path) -> list[Path]:
    images = sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ) if input_dir.is_dir() else []
    if not images:
        raise SystemExit(f"입력 이미지가 없습니다: {input_dir} (jpg/png을 넣어주세요)")
    return images


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="*", help="이미지 경로. 생략 시 input/ 전체")
    ap.add_argument("--input", default=str(ROOT / "input"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    args = ap.parse_args()

    load_env()

    images = ([Path(p) for p in args.images]
              or collect_input_images(Path(args.input)))
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    provider = PROVIDER()
    tagger = ConfluenceTagger()
    enum_cache = out_root / "_enumeration"
    enumerator: GeminiEnumerator | None = None

    total_ok = total_fail = 0
    for path in images:
        image_bytes = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"

        # ── ① 아이템 열거 (캐시) ────────────────────────────
        if not (enum_cache / f"{path.stem}.json").exists() and enumerator is None:
            enumerator = GeminiEnumerator()
        items = enumerate_with_cache(enumerator, path, image_bytes, mime, enum_cache)
        print(f"\n[enumerate] {path.name}: {len(items)} items "
              f"({', '.join(i['label_ko'] for i in items)})")

        out_dir = out_root / PROVIDER.key / path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for it in items:
            record: dict = {"_error": None}
            timings: dict[str, float] = {}
            fname = None
            try:
                # ── ② 편집: 분리·복구·정면화 ────────────────
                t0 = time.perf_counter()
                edited = provider.edit(image_bytes, mime,
                                       build_edit_prompt(it), item=it)
                timings["edit"] = round(time.perf_counter() - t0, 2)

                # ── ③ 태깅: Confluence 스키마 프로퍼티 ──────
                t0 = time.perf_counter()
                tags = tagger.tag(edited, hint=it["descriptor_en"])
                timings["tagging"] = round(time.perf_counter() - t0, 2)

                fname = (f"item_{it['id']:02d}_{tags['category_large']}.png"
                         .replace("/", "_"))
                (out_dir / fname).write_bytes(edited)

                record.update(tags)
                record["_missing_required"] = missing_required(tags)  # ④ 검증
                total_ok += 1
            except Exception as e:  # 아이템 1개 실패가 배치를 막지 않도록
                record["_error"] = str(e)
                total_fail += 1
                print(f"  [fail] {path.name} / {it['label_ko']}: {e}",
                      file=sys.stderr)

            record["_enum"] = {k: it.get(k) for k in
                               ("id", "label_ko", "descriptor_en",
                                "category_large", "occluded_by", "view_angle")}
            record["_image_file"] = fname
            record["_timings_sec"] = timings
            results.append(record)
            if fname:
                print(f"  [ok] {it['label_ko']} -> {fname} "
                      f"({record.get('item_name')}) {timings}")

        (out_dir / "items.json").write_text(
            json.dumps({
                "source_image": str(path),
                "model": PROVIDER.key,
                "tag_schema": "confluence-14286849-v4",
                "num_items": len(results),
                "num_ok": sum(1 for r in results if r["_image_file"]),
                "items": results,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[{PROVIDER.key}] {path.name} -> {out_dir}/items.json")

    print(f"\n완료: 성공 {total_ok} / 실패 {total_fail}")
    if total_ok == 0 and total_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
