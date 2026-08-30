"""골든셋 파일럿 전체 사이클 CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import analyze_run
from .anchors import build_anchor_scores
from .artifacts import read_json, write_json
from .clustering import cluster_embeddings
from .config import GoldenSettings, load_project_env
from .embedding import embed_manifest_images
from .items import extract_items
from .manifest import build_manifest, build_manifest_from_s3
from .principles import apply_principle_reviews, synthesize_principles
from .qdrant_index import index_principles_only, index_run
from .review import collect_accepted_claims, create_review_templates
from .review_manifest import build_review_manifest
from .review_apply import apply_review_payload
from .review_intake import prepare_review_run
from .review_publish import load_review, publish_review
from .review_sheets import build_review_sheets
from .style_clusters import build_style_clusters


def main() -> None:
    parser = argparse.ArgumentParser(
        description="골든 이미지 → 판단 원칙(메인) + 점수 앵커(보조) 파일럿"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_manifest = subparsers.add_parser(
        "review-manifest",
        help="드라이브 원본 폴더 → 본 검수용 metadata.csv·표집 배치·정규화 파일명",
    )
    review_manifest.add_argument(
        "--root", type=Path, required=True, help="수집자 폴더를 모아둔 로컬 루트"
    )
    review_manifest.add_argument(
        "--out-dir", type=Path, default=Path("local/golden-review")
    )
    review_manifest.add_argument("--batch-size", type=int, default=100)
    review_manifest.add_argument("--batch-label", default="batch1")
    review_manifest.add_argument(
        "--apply",
        action="store_true",
        help="정규화한 이름으로 이미지를 평면 폴더에 복사한다 (원본은 그대로 둔다)",
    )
    review_manifest.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="앞 배치 metadata CSV. 여기 있는 golden_id는 빼고 이어 뽑는다 (반복 가능)",
    )
    review_manifest.add_argument(
        "--quota",
        action="append",
        default=[],
        metavar="CODE=N",
        help="수집자별 상한 (예: shj=177). 반복 가능",
    )

    review_sheets = subparsers.add_parser(
        "review-sheets",
        help="metadata CSV만으로 사람이 채울 검수표 생성 (모델 호출 없음)",
    )
    review_sheets.add_argument("--metadata-csv", type=Path, required=True)
    review_sheets.add_argument("--images-dir", type=Path, required=True)
    review_sheets.add_argument("--out-dir", type=Path, required=True)
    review_sheets.add_argument("--pair-count", type=int, default=120)
    review_sheets.add_argument("--reviewer-label", default="")
    review_sheets.add_argument(
        "--analysis",
        type=Path,
        help="golden_id별 관찰·claim·최소 수정 JSONL. 미리 채워 둔 내용",
    )

    intake = subparsers.add_parser(
        "prepare-review-run",
        help="검수자에게 받은 파일을 run 디렉터리로 들인다 (스키마 변환 + 2인 병합)",
    )
    intake.add_argument("--run-dir", type=Path, required=True)
    intake.add_argument(
        "--analysis",
        type=Path,
        required=True,
        help="검수표를 만든 analysis.jsonl (스킬 산출)",
    )
    for name, help_text in (
        ("observation", "관찰 검수표"),
        ("claim", "claim 검수표"),
        ("pairwise", "쌍대 비교표"),
    ):
        intake.add_argument(
            f"--{name}",
            type=Path,
            action="append",
            default=[],
            metavar="CSV",
            help=f"{help_text} (검수자마다 하나씩, 반복 지정)",
        )

    style_clusters = subparsers.add_parser(
        "style-clusters",
        help="스타일 라벨로 clusters.jsonl 생성 (이미지 임베딩 없이)",
    )
    style_clusters.add_argument("--run-dir", type=Path, required=True)
    style_clusters.add_argument("--metadata-csv", type=Path, required=True)
    style_clusters.add_argument(
        "--style-labels",
        type=Path,
        help="사람이 채운 style_labels.csv. 비어 있는 style만 메운다",
    )
    style_clusters.add_argument(
        "--out-metadata",
        type=Path,
        help="병합 결과를 쓸 경로. 지정하지 않으면 metadata를 건드리지 않는다",
    )

    prepare = subparsers.add_parser(
        "prepare",
        help="manifest·임베딩·클러스터 생성 (기본 입력은 S3 원본 prefix)",
    )
    # 기본은 GOLDEN_S3_* 환경변수. --input-dir을 주면 로컬 디렉터리로 대체한다
    # (테스트·오프라인 실험용).
    prepare.add_argument("--input-dir", type=Path)
    prepare.add_argument("--run-dir", type=Path)
    prepare.add_argument("--dataset-name")
    prepare.add_argument("--dataset-version")
    prepare.add_argument("--metadata-csv", type=Path)
    prepare.add_argument("--limit", type=int)
    prepare.add_argument(
        "--embedding-backend",
        choices=["fashion", "deterministic"],
        default="fashion",
    )
    prepare.add_argument("--clusters", type=int)

    items = subparsers.add_parser(
        "extract-items",
        help="코디에서 의상 아이템 분리·태깅·임베딩 (image-processor 파이프라인)",
    )
    items.add_argument("--run-dir", type=Path)
    items.add_argument(
        "--force",
        action="store_true",
        help="S3에 완료 manifest가 있어도 다시 처리한다",
    )

    analyze = subparsers.add_parser("analyze", help="대표 이미지 통합 분석")
    analyze.add_argument("--run-dir", type=Path, required=True)
    analyze.add_argument(
        "--all", action="store_true", help="대표·경계가 아닌 이미지도 분석"
    )

    templates = subparsers.add_parser("templates", help="사람 검수 CSV 생성")
    templates.add_argument("--run-dir", type=Path, required=True)
    templates.add_argument("--pair-count", type=int, default=12)

    validate_reviews = subparsers.add_parser(
        "validate-reviews",
        help="2인 관찰·claim 검수 범위와 불일치 확인",
    )
    validate_reviews.add_argument("--run-dir", type=Path, required=True)
    validate_reviews.add_argument(
        "--observation-reviews", type=Path, required=True
    )
    validate_reviews.add_argument("--claim-reviews", type=Path, required=True)

    fit_anchors = subparsers.add_parser(
        "fit-anchors",
        help="비교 가능한 2인 쌍대 검수로 보조 Q 앵커 계산",
    )
    fit_anchors.add_argument("--run-dir", type=Path, required=True)
    fit_anchors.add_argument("--pairwise-reviews", type=Path, required=True)
    fit_anchors.add_argument("--observation-reviews", type=Path)

    publish = subparsers.add_parser(
        "publish-review",
        help="사람 검수 결과(앵커·승인 이미지)를 S3로 발행 — sha256으로 잇는다",
    )
    publish.add_argument("--run-dir", type=Path, required=True)
    publish.add_argument(
        "--metadata-csv",
        type=Path,
        required=True,
        help="정규화 golden_id ↔ image_sha256 대응표 (review-manifest 산출)",
    )
    publish.add_argument(
        "--dry-run", action="store_true", help="올리지 않고 요약만 출력"
    )

    apply_review = subparsers.add_parser(
        "apply-review",
        help="재임베딩 없이 코디 payload에만 검수 결과 반영 (GPU 불필요)",
    )
    apply_review.add_argument(
        "--human-review",
        type=Path,
        help="검수 결과 JSON 경로. 생략하면 S3에서 읽는다 (개발용 우회)",
    )
    apply_review.add_argument("--limit", type=int, help="처리할 최대 코디 수 (시험용)")
    apply_review.add_argument(
        "--dry-run", action="store_true", help="쓰지 않고 대상 건수만 출력"
    )

    synthesize = subparsers.add_parser(
        "synthesize-principles",
        help="2인 승인 claim만으로 조건부 원칙 초안 합성",
    )
    synthesize.add_argument("--run-dir", type=Path, required=True)
    synthesize.add_argument("--observation-reviews", type=Path, required=True)
    synthesize.add_argument("--claim-reviews", type=Path, required=True)
    synthesize.add_argument(
        "--provider",
        choices=["gemini", "openai"],
        default="gemini",
        help="원칙 합성에 쓸 LLM. openai는 OPENAI_API_KEY를 읽는다",
    )
    synthesize.add_argument(
        "--model",
        help="공급자 기본 모델을 덮어쓴다 (캐시 키는 GOLDEN_GEMINI_MODEL을 따른다)",
    )

    finalize = subparsers.add_parser(
        "finalize",
        help="호환 명령: 검수 후 앵커 계산과 원칙 합성을 순서대로 실행",
    )
    finalize.add_argument("--run-dir", type=Path, required=True)
    finalize.add_argument("--observation-reviews", type=Path, required=True)
    finalize.add_argument("--claim-reviews", type=Path, required=True)
    finalize.add_argument("--pairwise-reviews", type=Path, required=True)

    approve = subparsers.add_parser("approve", help="원칙 검수 결과 반영")
    approve.add_argument("--run-dir", type=Path, required=True)
    approve.add_argument("--principle-reviews", type=Path, required=True)
    approve.add_argument(
        "--minimum-reviewers",
        type=int,
        default=2,
        help="승인에 필요한 검수자 수 (기본 2). 1로 낮추면 교차 검증이 사라진다",
    )

    index = subparsers.add_parser("index", help="Qdrant 파생 컬렉션 적재")
    index.add_argument("--run-dir", type=Path, required=True)
    index.add_argument(
        "--text-backend",
        choices=["bge", "deterministic"],
        default="bge",
    )
    index.add_argument("--allow-draft", action="store_true")
    index.add_argument("--dry-run", action="store_true")
    index.add_argument(
        "--only",
        choices=["knowledge"],
        help="원칙만 적재한다. 이미지 임베딩·아이템 산출물이 없어도 된다",
    )

    args = parser.parse_args()
    load_project_env()
    settings = GoldenSettings.from_env()

    def _parse_quotas(values: list[str]) -> dict[str, int]:
        quotas: dict[str, int] = {}
        for value in values:
            code, _, count = value.partition("=")
            if not code or not count.isdigit():
                raise SystemExit(f"--quota 형식은 CODE=N 입니다: {value!r}")
            quotas[code.strip()] = int(count)
        return quotas

    if args.command == "review-sheets":
        paths, counts = build_review_sheets(
            metadata_csv=args.metadata_csv,
            images_dir=args.images_dir,
            out_dir=args.out_dir,
            pair_count=args.pair_count,
            reviewer_label=args.reviewer_label,
            analysis_jsonl=args.analysis,
        )
        print(f"관찰 검수표: {paths.observation} ({counts['images']}행)")
        print(f"관찰 내용 채워진 행: {counts['analyzed']}/{counts['images']}")
        print(f"claim 검수표: {paths.claim} ({counts['claims']}행)")
        print(f"최소 수정 검수표: {paths.minimum_edit} ({counts['minimum_edits']}행)")
        print(f"쌍대 비교표: {paths.pairwise} ({counts['pairs']}행)")
        print(f"스타일 묶음을 잇는 다리 쌍: {counts['bridge_pairs']}행")
    elif args.command == "review-manifest":
        summary = build_review_manifest(
            root=args.root,
            out_dir=args.out_dir,
            batch_size=args.batch_size,
            batch_label=args.batch_label,
            apply_rename=args.apply,
            exclude_csvs=args.exclude,
            quotas=_parse_quotas(args.quota),
        )
        print(f"전체 인벤토리: {summary['total']}장 → {summary['metadata_csv']}")
        print(f"검수 배치: {summary['batch']}장 → {summary['batch_csv']}")
        print(f"중복 제외 대상: {summary['duplicates']}장")
        if summary["copied"]:
            print(f"정규화 복사: 전체 {summary['copied']}장, 배치 {summary['batch_copied']}장")
        for missing in summary["missing_collectors"]:
            print(f"[미수집] {missing}")
        for unmapped in summary["unmapped_styles"]:
            print(f"[스타일 미매핑] {unmapped}")
        for unknown in summary["unknown_styles"]:
            print(f"[taxonomy 밖 값] {unknown}")
        print(f"요약: {summary['out_dir'] / 'inventory_summary.md'}")
    elif args.command == "prepare-review-run":
        result = prepare_review_run(
            run_dir=args.run_dir,
            analysis_jsonl=args.analysis,
            observation_csvs=args.observation,
            claim_csvs=args.claim,
            pairwise_csvs=args.pairwise,
        )
        print(f"분석 초안: {result.num_analyses}건 → {result.run_dir / 'analyses.jsonl'}")
        for name, count in result.counts.items():
            labels = ", ".join(result.reviewers.get(name, []))
            print(f"{name}: {count}행 (검수자 {labels})")
    elif args.command == "style-clusters":
        report = build_style_clusters(
            run_dir=args.run_dir,
            metadata_csv=args.metadata_csv,
            style_labels_csv=args.style_labels,
            out_metadata_csv=args.out_metadata,
        )
        merge = report.get("merge")
        if merge:
            print(f"라벨 {merge['num_labels']}건 중 {merge['num_filled']}건을 채웠다")
        print(f"묶음 {report['num_clusters']}개 → {report['clusters_jsonl']}")
        for name, size in sorted(
            report["cluster_sizes"].items(), key=lambda kv: -kv[1]
        ):
            print(f"   {name:<10} {size}장")
        if report["metadata_csv"] if "metadata_csv" in report else None:
            print(f"병합 metadata: {report['metadata_csv']}")
    elif args.command == "prepare":
        run_dir = args.run_dir or settings.run_dir
        if args.input_dir is not None:
            build_manifest(
                input_dir=args.input_dir,
                run_dir=run_dir,
                dataset_name=args.dataset_name or settings.dataset_name,
                dataset_version=args.dataset_version or settings.dataset_version,
                metadata_csv=args.metadata_csv,
                limit=args.limit,
            )
        else:
            build_manifest_from_s3(
                settings=settings,
                run_dir=run_dir,
                limit=args.limit,
            )
        _, _, model_name = embed_manifest_images(
            run_dir=run_dir,
            settings=settings,
            backend_name=args.embedding_backend,
        )
        cluster_embeddings(run_dir=run_dir, cluster_count=args.clusters)
        manifest = read_json(run_dir / "run_manifest.json")
        manifest.update({"image_embedding_version": model_name, "status": "CLUSTERED"})
        write_json(run_dir / "run_manifest.json", manifest)
        print(f"준비 완료: {run_dir}")
    elif args.command == "extract-items":
        rows = extract_items(
            run_dir=args.run_dir or settings.run_dir,
            settings=settings,
            force=args.force,
        )
        print(f"아이템: {len(rows)}건")
    elif args.command == "analyze":
        results = analyze_run(
            run_dir=args.run_dir,
            settings=settings,
            analyze_all=args.all,
        )
        print(f"분석 갱신: {len(results)}건")
    elif args.command == "templates":
        paths = create_review_templates(
            run_dir=args.run_dir,
            pair_count=args.pair_count,
        )
        print(f"관찰 검수표: {paths.observation}")
        print(f"claim 검수표: {paths.claim}")
        print(f"최소 수정 검수표: {paths.minimum_edit}")
        print(f"쌍대 비교표: {paths.pairwise}")
        print(f"검수 가이드: {paths.guide}")
    elif args.command == "validate-reviews":
        _, report = collect_accepted_claims(
            observation_reviews_csv=args.observation_reviews,
            claim_reviews_csv=args.claim_reviews,
            run_dir=args.run_dir,
        )
        print(report)
    elif args.command == "fit-anchors":
        anchors = build_anchor_scores(
            pairwise_csv=args.pairwise_reviews,
            observation_reviews_csv=args.observation_reviews,
            run_dir=args.run_dir,
        )
        print(f"점수 앵커: {len(anchors)}건")
    elif args.command == "publish-review":
        key, payload = publish_review(
            run_dir=args.run_dir,
            metadata_csv=args.metadata_csv,
            settings=settings,
            dry_run=args.dry_run,
        )
        print(f"코디 {payload['num_images']}건 "
              f"(검수 통과 {payload['num_verified']} / 앵커 {payload['num_anchored']})")
        if payload["unmatched_golden_ids"]:
            print(f"sha256 미확인: {len(payload['unmatched_golden_ids'])}건")
        if args.dry_run:
            print(f"dry-run: 올리지 않았습니다. 대상 키 = {key}")
        else:
            print(f"발행: s3://{settings.s3_bucket}/{key}")
    elif args.command == "apply-review":
        review = load_review(settings=settings, local_path=args.human_review)
        summary = apply_review_payload(
            settings=settings,
            review=review,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        print(
            f"S3 코디 {summary['num_manifests']}건 / 검수 {summary['num_reviews_loaded']}건 "
            f"→ 일치 {summary['num_matched']}건"
        )
        if summary["applied"]:
            print(f"payload 갱신: {summary['num_updated']}건")
            print(f"검수 키 제거: {summary['num_cleared']}건")
            if summary["num_not_indexed"]:
                print(f"미적재라 반영 못 함: {summary['num_not_indexed']}건")
        else:
            print(f"dry-run: 쓰지 않았습니다 (제거 대상 후보 {summary['num_cleared_candidates']}건)")
    elif args.command == "synthesize-principles":
        client = None
        if args.provider == "openai":
            from .openai_client import OpenAIPrincipleClient

            client = OpenAIPrincipleClient(
                model=args.model,
                timeout_seconds=settings.gemini_timeout_seconds,
            )
            print(f"공급자: openai ({client.model})")
        principles = synthesize_principles(
            run_dir=args.run_dir,
            observation_reviews_csv=args.observation_reviews,
            claim_reviews_csv=args.claim_reviews,
            settings=settings,
            client=client,
        )
        print(f"원칙 초안: {len(principles)}건")
        print(f"원칙 검수표: {args.run_dir / 'principle_reviews.template.csv'}")
    elif args.command == "finalize":
        anchors = build_anchor_scores(
            pairwise_csv=args.pairwise_reviews,
            observation_reviews_csv=args.observation_reviews,
            run_dir=args.run_dir,
        )
        principles = synthesize_principles(
            run_dir=args.run_dir,
            observation_reviews_csv=args.observation_reviews,
            claim_reviews_csv=args.claim_reviews,
            settings=settings,
        )
        print(f"점수 앵커: {len(anchors)}건")
        print(f"원칙 초안: {len(principles)}건")
        print(f"원칙 검수표: {args.run_dir / 'principle_reviews.template.csv'}")
    elif args.command == "approve":
        if args.minimum_reviewers < 2:
            print(
                f"주의: 검수자 {args.minimum_reviewers}명으로 승인한다. "
                "두 사람이 독립적으로 같은 판단을 내렸는지가 원래의 승인 근거였고, "
                "그 교차 검증이 사라진다."
            )
        principles = apply_principle_reviews(
            run_dir=args.run_dir,
            principle_reviews_csv=args.principle_reviews,
            minimum_reviewers=args.minimum_reviewers,
        )
        counts: dict[str, int] = {}
        for row in principles:
            status = str(row.get("status", "DRAFT"))
            counts[status] = counts.get(status, 0) + 1
        approved_count = counts.get("APPROVED", 0)
        print(f"원칙 검수 반영: {len(principles)}건, 승인 {approved_count}건")
        for status, count in sorted(counts.items()):
            print(f"   {status:<10} {count}건")
        if not approved_count:
            print(
                "승인 0건 — index가 APPROVED만 적재하므로 knowledge에 아무것도 "
                "들어가지 않는다. 검수표의 knowledge_role을 확인하라."
            )
    elif args.command == "index" and args.only == "knowledge":
        summary = index_principles_only(
            run_dir=args.run_dir,
            settings=settings,
            text_backend_name=args.text_backend,
            allow_draft=args.allow_draft,
            dry_run=args.dry_run,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.command == "index":
        summary = index_run(
            run_dir=args.run_dir,
            settings=settings,
            text_backend_name=args.text_backend,
            allow_draft=args.allow_draft,
            dry_run=args.dry_run,
        )
        print(summary)


if __name__ == "__main__":
    main()
