import io
import unittest

from PIL import Image

from pipeline.qwen import (
    NormalizeGenerator,
    SingleItemEnumerator,
    _json,
    _prompt,
    normalize_tags,
)
from worker import callback_payload_from_manifest


class QwenPipelineSmokeTest(unittest.TestCase):
    def test_prompt_requires_non_empty_season_array(self):
        prompt = _prompt()

        self.assertIn("season은 반드시 위 목록에서 1개 이상", prompt)
        self.assertIn("season에 빈 배열, 빈 문자열, null", prompt)

    def test_normalize_parse_and_taxonomy(self):
        source = io.BytesIO()
        Image.new("RGB", (1500, 500)).save(source, "JPEG")
        item = SingleItemEnumerator().enumerate(source.getvalue(), "image/jpeg")[0]
        result = NormalizeGenerator().generate(source.getvalue(), "image/jpeg", item)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual((image.format, max(image.size)), ("PNG", 1024))

        parsed = _json(
            '설명 ```json {"category_large":"하의","category_small":"티셔츠",'
            '"style":["캐주얼","시크","포멀"],"usage":["데일리","", " "]} ```'
        )
        tags = normalize_tags(parsed)
        self.assertEqual(tags["category_large"], "상의")
        self.assertEqual(len(tags["style"]), 2)
        self.assertEqual(tags["usage"], ["데일리"])

    def test_normalize_accepts_scalar_season_and_usage(self):
        tags = normalize_tags({
            "category_large": "아우터",
            "season": "겨울",
            "usage": "외출",
        })

        self.assertEqual(tags["season"], ["겨울"])
        self.assertEqual(tags["usage"], ["외출"])

    def test_callback_removes_blank_usage_from_existing_manifest(self):
        payload = callback_payload_from_manifest({
            "job_id": "job-1",
            "pipeline": {"impl": "qwen-tag"},
            "counts": {"failed": 0},
            "items": [{
                "s3_key": "item.png",
                "tags": {"category_large": "상의", "usage": ["데일리", "", " "]},
                "image_vector": [],
                "text_vector": [],
            }],
        })

        self.assertEqual(payload["items"][0]["usage"], ["데일리"])

    def test_callback_normalizes_scalar_season_and_usage(self):
        payload = callback_payload_from_manifest({
            "job_id": "job-1",
            "pipeline": {"impl": "qwen-tag"},
            "counts": {"failed": 0},
            "items": [{
                "s3_key": "item.png",
                "tags": {
                    "category_large": "아우터",
                    "season": "겨울",
                    "usage": "외출",
                },
                "image_vector": [],
                "text_vector": [],
            }],
        })

        self.assertEqual(payload["items"][0]["season"], ["겨울"])
        self.assertEqual(payload["items"][0]["usage"], ["외출"])


if __name__ == "__main__":
    unittest.main()
