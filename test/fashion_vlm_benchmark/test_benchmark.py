from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import benchmark


class BenchmarkTests(unittest.TestCase):
    def test_prompt_uses_wardrobe_taxonomy(self) -> None:
        prompt = benchmark.build_prompt("남성 오버핏 라운드 니트")

        self.assertIn("언더웨어/이너웨어", prompt)
        self.assertIn("스카이블루", prompt)
        self.assertIn("그래픽/로고", prompt)
        self.assertIn("오버핏", prompt)
        self.assertIn("니트/스웨터", prompt)
        self.assertIn("패딩충전재", prompt)
        self.assertIn('"layer_order": null', prompt)
        self.assertNotIn("살짝 넉넉한 핏", prompt)

    def test_dataset_validation_rejects_out_of_taxonomy_value(self) -> None:
        dataset = {
            "samples": [
                {
                    "id": "sample",
                    "file_name": "sample.jpg",
                    "product_name": "니트",
                    "expected": {
                        "category_large": "상의",
                        "color": "네이비",
                        "pattern": "무지",
                        "fit": "살짝 넉넉한 핏",
                    },
                }
            ]
        }

        errors = benchmark.validate_dataset(
            dataset,
            image_dir=Path("unused"),
            require_images=False,
            expected_count=1,
        )

        self.assertTrue(any("expected.fit" in error for error in errors))

    def test_scoring_parses_fenced_json_and_compares_fields(self) -> None:
        dataset = {
            "samples": [
                {
                    "id": "knit",
                    "expected": {
                        "item_name": "네이비 오버핏 니트",
                        "category_large": "상의",
                        "category_small": "니트/스웨터",
                        "season": ["가을", "겨울"],
                        "style": ["캐주얼", "미니멀"],
                        "color": "네이비",
                        "pattern": "무지",
                        "fit": "오버핏",
                        "material": "니트",
                        "sleeve": "긴팔",
                        "length": "기본",
                        "usage": ["데일리", "외출"],
                        "layer_role": "기본 상의",
                        "layer_order": 1,
                    },
                }
            ]
        }
        results = [
            {
                "sample_id": "knit",
                "model": "test-model",
                "raw_output": (
                    "```json\n"
                    + json.dumps(dataset["samples"][0]["expected"], ensure_ascii=False)
                    + "\n```"
                ),
                "latency_seconds": 2.5,
                "peak_vram_mb": 4096,
            }
        ]

        rows, summary = benchmark.score_results(dataset, results)

        self.assertTrue(rows[0]["json_valid"])
        self.assertTrue(rows[0]["schema_complete"])
        self.assertTrue(rows[0]["taxonomy_valid"])
        self.assertTrue(rows[0]["all_fields_match"])
        self.assertEqual(summary["models"]["test-model"]["all_fields_accuracy"], 1.0)
        self.assertEqual(summary["models"]["test-model"]["season_f1"], 1.0)

    def test_write_prompts_creates_one_jsonl_record_per_sample(self) -> None:
        dataset = {
            "samples": [
                {
                    "id": "knit",
                    "file_name": "knit.jpg",
                    "product_name": "네이비 니트",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "prompts.jsonl"
            benchmark.write_prompts(dataset, output)
            records = benchmark.load_jsonl(output)

        self.assertEqual(records[0]["sample_id"], "knit")
        self.assertIn("네이비 니트", records[0]["prompt"])

    def test_init_does_not_overwrite_existing_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "dataset.example.json"
            destination = root / "dataset.json"
            source.write_text('{"samples": []}', encoding="utf-8")
            destination.write_text("keep", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "source": str(source),
                    "destination": str(destination),
                    "force": False,
                },
            )()

            with self.assertRaises(SystemExit):
                benchmark.command_init(args)

            self.assertEqual(destination.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
