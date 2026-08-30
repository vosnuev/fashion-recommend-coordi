import importlib
import os
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

import config  # noqa: F401 - collector 루트를 sys.path에 추가한다.
from util.tagging import get_provider, get_sync_tagger


class TaggingProviderTests(unittest.TestCase):
    def test_reads_eleven_provider_case_insensitively(self):
        with patch.dict(
            os.environ,
            {"ELEVEN_TAGGING_PROVIDER": " Claude "},
            clear=False,
        ):
            self.assertEqual(
                get_provider("ELEVEN_TAGGING_PROVIDER"),
                "claude",
            )

    def test_rejects_unknown_eleven_provider(self):
        with patch.dict(
            os.environ,
            {"ELEVEN_TAGGING_PROVIDER": "unknown"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "ELEVEN_TAGGING_PROVIDER"):
                get_provider("ELEVEN_TAGGING_PROVIDER")

    def test_claude_batch_mode_falls_back_to_sync(self):
        with patch.dict(
            os.environ,
            {
                "ELEVEN_TAGGING_PROVIDER": "claude",
                "ELEVEN_TAGGING_MODE": "batch",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config)
            self.assertEqual(reloaded.TAGGING_MODE, "sync")
        importlib.reload(config)

    def test_factory_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "tagging provider"):
            get_sync_tagger("unknown")

    def test_factory_selects_openai_without_external_call(self):
        instance = object()
        module = ModuleType("util.tagging.openai_tagger")
        module.OpenAITagger = lambda: instance

        with patch.dict(
            sys.modules,
            {"util.tagging.openai_tagger": module},
        ):
            self.assertIs(get_sync_tagger("openai"), instance)

    def test_factory_selects_claude_without_external_call(self):
        instance = object()
        module = ModuleType("util.tagging.claude_tagger")
        module.ClaudeTagger = lambda: instance

        with patch.dict(
            sys.modules,
            {"util.tagging.claude_tagger": module},
        ):
            self.assertIs(get_sync_tagger("claude"), instance)


if __name__ == "__main__":
    unittest.main()
