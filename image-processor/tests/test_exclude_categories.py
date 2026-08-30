"""'입은 옷과 겹치는 부위는 건너뛴다' 계약의 단위 테스트.

핵심은 두 가지다.
1. 제외 대상은 **열거 직후** 빠져야 한다 — 생성·태깅·임베딩이 아이템당 비용의
   거의 전부이므로, 뒤에서 걸러 내면 절감 효과가 사라진다.
2. 전부 제외돼 남는 아이템이 없어도 **실패가 아니다** — 사용자가 사진 속 부위를
   직접 다 지정한 정상 흐름이다.

실행: python -m unittest discover -s tests  (image-processor 디렉터리에서)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import WardrobePipeline  # noqa: E402
from pipeline.base import (  # noqa: E402
    Embedder,
    EnumeratedItem,
    ItemEnumerator,
    ItemTagger,
    ProductImageGenerator,
)
from worker import (  # noqa: E402
    build_manifest,
    callback_payload_from_manifest,
    normalize_payload,
)


class FakeEnumerator(ItemEnumerator):
    def __init__(self, items: list[EnumeratedItem]) -> None:
        self.items = items

    def enumerate(self, image_bytes: bytes, mime: str) -> list[EnumeratedItem]:
        return list(self.items)


class CountingGenerator(ProductImageGenerator):
    key = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, image_bytes: bytes, mime: str, item: EnumeratedItem) -> bytes:
        self.calls.append(item.label_ko)
        return b"png"


class CountingTagger(ItemTagger):
    def __init__(self) -> None:
        self.calls = 0

    def tag(self, product_png: bytes) -> dict:
        self.calls += 1
        return {"category_large": "신발", "item_name": "테스트"}


class CountingEmbedder(Embedder):
    version = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def embed_image(self, product_png: bytes) -> list[float]:
        self.calls += 1
        return [0.0]

    def embed_text(self, caption: str) -> list[float]:
        return [0.0]


def enumerated(label: str, category: str) -> EnumeratedItem:
    return EnumeratedItem(
        descriptor_en=f"the {label}",
        label_ko=label,
        category_large=category,
    )


class ExcludeCategoriesTests(unittest.TestCase):
    def _pipeline(self, items: list[EnumeratedItem]):
        generator = CountingGenerator()
        tagger = CountingTagger()
        embedder = CountingEmbedder()
        pipeline = WardrobePipeline(
            enumerator=FakeEnumerator(items),
            generator=generator,
            tagger=tagger,
            embedder=embedder,
        )
        return pipeline, generator, tagger, embedder

    def test_excluded_categories_never_reach_generation(self) -> None:
        pipeline, generator, tagger, embedder = self._pipeline(
            [
                enumerated("흰 티셔츠", "상의"),
                enumerated("검정 슬랙스", "하의"),
                enumerated("스니커즈", "신발"),
            ]
        )

        items, excluded = pipeline.process(b"img", "image/jpeg", ["상의"])

        self.assertEqual([it.enum.label_ko for it in items], ["검정 슬랙스", "스니커즈"])
        self.assertEqual([it.label_ko for it in excluded], ["흰 티셔츠"])
        # 제외한 아이템에는 생성·태깅·임베딩 비용이 전혀 들지 않는다.
        self.assertEqual(generator.calls, ["검정 슬랙스", "스니커즈"])
        self.assertEqual(tagger.calls, 2)
        self.assertEqual(embedder.calls, 2)

    def test_empty_exclusion_keeps_previous_behaviour(self) -> None:
        pipeline, generator, _, _ = self._pipeline(
            [enumerated("흰 티셔츠", "상의"), enumerated("스니커즈", "신발")]
        )

        items, excluded = pipeline.process(b"img", "image/jpeg")

        self.assertEqual(len(items), 2)
        self.assertEqual(excluded, [])
        self.assertEqual(len(generator.calls), 2)

    def test_unknown_category_excludes_nothing(self) -> None:
        pipeline, _, _, _ = self._pipeline([enumerated("스니커즈", "신발")])

        items, excluded = pipeline.process(b"img", "image/jpeg", ["없는분류", ""])

        self.assertEqual(len(items), 1)
        self.assertEqual(excluded, [])


class PayloadTests(unittest.TestCase):
    def test_normalize_reads_exclude_categories(self) -> None:
        job = normalize_payload(
            {
                "job_id": "abc",
                "user_id": 1,
                "source": {"bucket": "b", "key": "k"},
                "output": {"bucket": "b", "prefix": "p/"},
                "exclude_categories": ["상의"],
                "callback_url": "http://x/",
            }
        )

        self.assertEqual(job["exclude_categories"], ["상의"])

    def test_legacy_payload_without_exclusion_still_works(self) -> None:
        job = normalize_payload(
            {
                "job_id": "abc",
                "source": {"bucket": "b", "key": "k"},
                "output_prefix": "p/",
            }
        )

        self.assertEqual(job["exclude_categories"], [])


class CallbackStatusTests(unittest.TestCase):
    def _manifest(self, *, items, excluded):
        job = {
            "job_id": "abc",
            "out_prefix": "wardrobe/1/abc/",
            "exclude_categories": ["상의", "하의"],
        }
        return build_manifest(job, "fake", items, 1.0, excluded)

    def test_all_items_excluded_reports_success_not_failure(self) -> None:
        """사용자가 사진 속 부위를 전부 직접 지정한 정상 흐름."""

        manifest = self._manifest(
            items=[],
            excluded=[enumerated("흰 티셔츠", "상의"), enumerated("슬랙스", "하의")],
        )

        payload = callback_payload_from_manifest(manifest)

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["error"], "")
        self.assertEqual(manifest["counts"]["excluded"], 2)
        self.assertEqual(manifest["excluded_categories"], ["상의", "하의"])

    def test_nothing_found_and_nothing_excluded_is_still_a_failure(self) -> None:
        manifest = self._manifest(items=[], excluded=[])

        payload = callback_payload_from_manifest(manifest)

        self.assertEqual(payload["status"], "failed")
        self.assertIn("처리 성공한 아이템이 없습니다", payload["error"])


if __name__ == "__main__":
    unittest.main()
