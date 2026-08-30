"""프론티어 이미지 편집 모델 end-to-end 비교 테스트.

흐름 (SAM3 미사용, LLM에 분리·복구·정면화 전부 위임):
  ① 열거: Gemini 비전이 사진 속 아이템 목록 생성 (이미지당 1회, 캐시 공유)
  ② 편집: 모델 5종이 순차적으로, 아이템마다 "전체 사진 + 분리·복구·정면화
     프롬프트"를 받아 흰 배경 정면 상품 이미지를 생성
  ③ 저장: output/<모델>/<이미지명>/item_XX_<대분류>.png + items.json
  ④ 집계: output/summary.json + summary.md

실행:
  python run_all.py                          # input/ 전체 × 모든 모델
  python run_all.py --models gpt-image-2,seedream-5-0-pro
  python run_all.py img1.jpg img2.jpg        # 특정 이미지만

API 키가 없는 모델은 건너뛰고 요약에 skipped로 기록한다 (전체 중단 없음).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))

from common.enumerator import GeminiEnumerator, enumerate_with_cache  # noqa: E402
from common.prompts import build_edit_prompt  # noqa: E402
from common.providers import resolve  # noqa: E402
from common.report import write_summary  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def load_env() -> None:
    """test-llm/.env → 저장소 루트 .env 순으로 로드 (기존 값 우선)."""
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


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "image/jpeg"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="*", help="이미지 경로. 생략 시 input/ 전체")
    ap.add_argument("--models", default=os.getenv("TEST_MODELS", ""),
                    help="쉼표 구분 모델 키 (기본: 전체)")
    ap.add_argument("--input", default=str(ROOT / "input"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    args = ap.parse_args()

    load_env()

    images = ([Path(p) for p in args.images]
              or collect_input_images(Path(args.input)))
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    keys = [k.strip() for k in args.models.split(",") if k.strip()] or None
    provider_classes = resolve(keys)

    # ── ① 아이템 열거 (이미지당 1회, 모든 모델이 공유) ──────────
    enum_cache = out_root / "_enumeration"
    enumerator: GeminiEnumerator | None = None
    plan: list[tuple[Path, bytes, str, list[dict]]] = []
    for path in images:
        image_bytes = path.read_bytes()
        mime = guess_mime(path)
        if not (enum_cache / f"{path.stem}.json").exists() and enumerator is None:
            enumerator = GeminiEnumerator()  # 키 확인은 최초 필요 시 1회
        items = enumerate_with_cache(enumerator, path, image_bytes, mime, enum_cache)
        print(f"[enumerate] {path.name}: {len(items)} items "
              f"({', '.join(i['label_ko'] for i in items)})")
        plan.append((path, image_bytes, mime, items))

    # ── ②③ 모델 순차 실행 ──────────────────────────────────────
    records: list[dict] = []
    skipped: list[str] = []
    for cls in provider_classes:
        if not cls.available():
            print(f"\n[skip] {cls.key}: 환경변수 {cls.required_env} 없음")
            skipped.append(cls.key)
            continue
        try:
            provider = cls()
        except Exception as e:
            print(f"\n[skip] {cls.key}: 초기화 실패 -> {e}", file=sys.stderr)
            skipped.append(cls.key)
            continue

        print(f"\n===== {cls.key} =====")
        for path, image_bytes, mime, items in plan:
            out_dir = out_root / cls.key / path.stem
            out_dir.mkdir(parents=True, exist_ok=True)
            results = []
            for it in items:
                prompt = build_edit_prompt(it)
                fname = (f"item_{it['id']:02d}_{it['category_large']}.png"
                         .replace("/", "_"))
                rec = {"model": cls.key, "image": path.name,
                       "item_id": it["id"], "label_ko": it["label_ko"],
                       "ok": False, "latency_sec": 0.0, "error": None}
                t0 = time.perf_counter()
                try:
                    edited = provider.edit(image_bytes, mime, prompt, item=it)
                    (out_dir / fname).write_bytes(edited)
                    rec["ok"] = True
                except Exception as e:  # 아이템 1개 실패가 배치를 막지 않도록
                    rec["error"] = str(e)
                    print(f"  [fail] {path.name} / {it['label_ko']}: {e}",
                          file=sys.stderr)
                rec["latency_sec"] = round(time.perf_counter() - t0, 2)
                records.append(rec)
                results.append({
                    **{k: it.get(k) for k in
                       ("id", "label_ko", "descriptor_en", "category_large",
                        "occluded_by", "view_angle")},
                    "_image_file": fname if rec["ok"] else None,
                    "_latency_sec": rec["latency_sec"],
                    "_error": rec["error"],
                    "_prompt": prompt,
                })
            ok = sum(1 for r in results if r["_image_file"])
            (out_dir / "items.json").write_text(
                json.dumps({
                    "source_image": str(path), "model": cls.key,
                    "num_items": len(results), "num_ok": ok,
                    "items": results,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  [{cls.key}] {path.name}: {ok}/{len(results)} -> {out_dir}")

    # ── ④ 집계 ─────────────────────────────────────────────────
    if records:
        md = write_summary(out_root, records)
        print(f"\n요약: {md}")
        print((out_root / "summary.md").read_text(encoding="utf-8"))
    if skipped:
        print(f"건너뛴 모델: {skipped}")

    failed = sum(1 for r in records if not r["ok"])
    if records and failed == len(records):
        sys.exit(1)  # 전부 실패면 비정상 종료


if __name__ == "__main__":
    main()
