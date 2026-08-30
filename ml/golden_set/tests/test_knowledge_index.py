"""원칙만 적재하는 경로와 style 필터 키 규칙.

payload의 `style`은 리트리버가 **필터**로 쓴다. 여기 taxonomy 밖 값이 들어가면
검색이 조용히 0건이 된다 — 에러도 경고도 없이 그냥 안 잡힌다. 실제로 승인된 원칙
53건에서 style 고유값 66개 중 6개만 taxonomy 안에 있었고, 댄디·스트릿·시크 원칙은
해당 스타일로 걸러도 하나도 잡히지 않는 상태였다.

원인은 LLM이 `applies_when.style_intents`에 스타일 이름 대신 효과 설명을 채운 것이다
('단조로움 피하기'). 사람이 붙인 스타일 라벨에서 온 `cluster_id`가 신뢰할 수 있는
출처라, 그걸 1순위로 쓰도록 고쳤다. 그 규칙을 여기서 고정한다.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.golden_set.config import GoldenSettings
from ml.golden_set.qdrant_index import _principle_styles, index_principles_only

STYLES = {"캐주얼", "댄디", "러블리", "미니멀", "포멀"}


def _settings() -> GoldenSettings:
    return GoldenSettings(
        gemini_api_key="test",
        gemini_api_base_url="https://example.test",
        gemini_model="gemini-3.5-flash",
        gemini_timeout_seconds=1,
        max_multimodal_calls=1,
        fashion_model_id="f",
        text_model_id="t",
        device="cpu",
        embedding_batch_size=1,
        s3_bucket="b",
    )


def _principle(**overrides):
    row = {
        "principle_key": "댄디:A1_COLOR_HARMONY:p01",
        "cluster_id": "댄디",
        "axis": "A1_COLOR_HARMONY",
        "statement": "문장",
        "applies_when": {"style_intents": []},
        "exceptions": [],
        "status": "APPROVED",
        "knowledge_role": "EXPLANATION_ONLY",
        "support_image_count": 3,
        "comparison_evidence_count": 0,
        "reviewer_count": 2,
        "eligible_for_scoring": False,
        "evidence": [],
    }
    row.update(overrides)
    return row


class StyleFilterKeyTests(unittest.TestCase):
    def _styles(self, row, allowed=STYLES):
        with patch(
            "ml.golden_set.qdrant_index._taxonomy_styles", return_value=allowed
        ):
            return _principle_styles(row)

    def test_cluster_id_becomes_the_filter_key(self) -> None:
        row = _principle(applies_when={"style_intents": ["단조로움 피하기"]})
        self.assertEqual(self._styles(row), ["댄디"])

    def test_free_text_intent_is_dropped(self) -> None:
        """'깔끔하고 단정한 실루엣 연출' 같은 값으로는 아무도 검색하지 않는다."""
        row = _principle(
            cluster_id="",
            applies_when={"style_intents": ["깔끔하고 단정한 실루엣 연출"]},
        )
        self.assertEqual(self._styles(row), [])

    def test_valid_intent_is_kept_alongside_cluster(self) -> None:
        row = _principle(
            cluster_id="댄디",
            applies_when={"style_intents": ["포멀", "시각적 대비감 부여"]},
        )
        self.assertEqual(self._styles(row), ["댄디", "포멀"])

    def test_duplicate_between_cluster_and_intent_is_collapsed(self) -> None:
        row = _principle(
            cluster_id="러블리", applies_when={"style_intents": ["러블리"]}
        )
        self.assertEqual(self._styles(row), ["러블리"])

    def test_synthetic_cluster_id_is_not_a_style(self) -> None:
        """임베딩 클러스터의 cluster-003은 스타일 이름이 아니다."""
        row = _principle(
            cluster_id="cluster-003", applies_when={"style_intents": ["캐주얼"]}
        )
        self.assertEqual(self._styles(row), ["캐주얼"])

    def test_cluster_outside_taxonomy_is_dropped(self) -> None:
        row = _principle(cluster_id="미분류", applies_when={"style_intents": []})
        self.assertEqual(self._styles(row), [])

    def test_legacy_list_format_still_works(self) -> None:
        row = _principle(cluster_id="", applies_when=["style: 캐주얼, 댄디"])
        self.assertEqual(self._styles(row), ["캐주얼", "댄디"])

    def test_intents_are_skipped_when_taxonomy_is_unavailable(self) -> None:
        """검증할 수 없으면 필터 키에 자유문장을 넣지 않는다."""
        row = _principle(applies_when={"style_intents": ["단조로움 피하기"]})
        self.assertEqual(self._styles(row, allowed=None), ["댄디"])


class IndexPrinciplesOnlyTests(unittest.TestCase):
    def _run(self, principles, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "review-batch1-v1"
            run_dir.mkdir(parents=True)
            (run_dir / "principles.jsonl").write_text(
                "\n".join(json.dumps(p, ensure_ascii=False) for p in principles) + "\n",
                encoding="utf-8",
            )
            with patch(
                "ml.golden_set.qdrant_index._taxonomy_styles", return_value=STYLES
            ):
                summary = index_principles_only(
                    run_dir=run_dir,
                    settings=_settings(),
                    text_backend_name="deterministic",
                    dry_run=True,
                    **kwargs,
                )
            plan = json.loads(
                (run_dir / "qdrant_index_plan.knowledge.json").read_text(
                    encoding="utf-8"
                )
            )
            return summary, plan

    def test_runs_without_the_image_pipeline_artifacts(self) -> None:
        """검수 경로로 만든 run에는 images.jsonl도 임베딩도 없다."""
        summary, _ = self._run([_principle()])
        self.assertEqual(summary["principle_points"], 1)
        self.assertEqual(summary["only"], "knowledge")

    def test_draft_is_excluded_by_default(self) -> None:
        summary, _ = self._run([_principle(), _principle(
            principle_key="댄디:A2:p02", status="DRAFT"
        )])
        self.assertEqual(summary["principle_points"], 1)
        self.assertEqual(summary["principle_statuses"], ["APPROVED"])

    def test_allow_draft_includes_them(self) -> None:
        summary, _ = self._run(
            [_principle(), _principle(principle_key="댄디:A2:p02", status="DRAFT")],
            allow_draft=True,
        )
        self.assertEqual(summary["principle_points"], 2)

    def test_no_approved_principle_raises(self) -> None:
        """승인 0건이면 조용히 빈 컬렉션을 만들지 않고 멈춘다."""
        with self.assertRaises(ValueError):
            self._run([_principle(status="DRAFT")])

    def test_version_falls_back_to_the_run_directory_name(self) -> None:
        """run_manifest.json이 없어도 포인트 id가 안정적이어야 한다."""
        summary, _ = self._run([_principle()])
        self.assertEqual(summary["dataset_version"], "review-batch1-v1")

    def test_plan_reports_only_taxonomy_styles(self) -> None:
        summary, plan = self._run(
            [_principle(applies_when={"style_intents": ["단조로움 피하기"]})]
        )
        self.assertEqual(summary["styles"], ["댄디"])
        self.assertEqual(plan["styles"], ["댄디"])


if __name__ == "__main__":
    unittest.main()
