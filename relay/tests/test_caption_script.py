"""Offline display-caption conversion tests (no real Gemini or secrets)."""
import os
import unittest
from unittest.mock import patch

from relay.caption_script import (
    CAPTION_SCRIPT_ENV,
    _taiwan_converter,
    make_caption_normalizer,
    read_caption_script,
)


class CaptionScriptTests(unittest.TestCase):
    def tearDown(self):
        _taiwan_converter.cache_clear()

    def test_default_and_raw_preferences(self):
        self.assertEqual(read_caption_script({}), "zh-TW")
        self.assertEqual(read_caption_script({CAPTION_SCRIPT_ENV: "raw"}), "raw")
        with self.assertRaises(ValueError):
            read_caption_script({CAPTION_SCRIPT_ENV: "s2t"})

    def test_raw_does_not_load_opencc(self):
        with patch("relay.caption_script._taiwan_converter", side_effect=AssertionError("should not load")):
            convert = make_caption_normalizer("raw")
            self.assertEqual(convert("没问题，我会回复你的。"), "没问题，我会回复你的。")

    def test_opencc_corpus_and_cache(self):
        _taiwan_converter.cache_clear()
        convert = make_caption_normalizer("zh-TW")
        self.assertEqual(convert("没问题，我会回复你的。"), "沒問題，我會回覆你的。")
        self.assertEqual(convert("會, OK! 123"), "會, OK! 123")
        self.assertEqual(convert(""), "")
        self.assertIs(_taiwan_converter(), _taiwan_converter())

    def test_fragments_are_independent_and_preserve_order(self):
        convert = make_caption_normalizer("zh-TW")
        fragments = ["我会回", "复你", "。"]
        outputs = [convert(chunk) for chunk in fragments]
        self.assertEqual(len(outputs), len(fragments))
        self.assertEqual(outputs[0], "我會回")
        self.assertTrue("".join(outputs).startswith("我會回"))
        self.assertEqual(outputs[-1], "。")
        # Phrase-sensitive Taiwan wording may differ from a whole-sentence pass.

    def test_converter_initialization_failure_remains_fail_closed(self):
        with patch("relay.caption_script._taiwan_converter", side_effect=ImportError("OpenCC unavailable")):
            with self.assertRaises(ImportError):
                make_caption_normalizer("zh-TW")

    def test_converter_failures_fall_back_with_one_sanitized_warning(self):
        private_detail = "private transcript and credential detail"

        class Broken:
            def convert(self, _text):
                raise RuntimeError(private_detail)

        with patch("relay.caption_script._taiwan_converter", return_value=Broken()):
            convert = make_caption_normalizer("zh-TW")
            with self.assertLogs("relay.caption_script", level="WARNING") as captured:
                first = "這段逐字稿"
                second = "另一段逐字稿"
                self.assertEqual(convert(first), first)
                self.assertEqual(convert(second), second)

        self.assertEqual(len(captured.records), 1)
        self.assertNotIn(first, captured.output[0])
        self.assertNotIn(second, captured.output[0])
        self.assertNotIn(private_detail, captured.output[0])


if __name__ == "__main__":
    unittest.main()
