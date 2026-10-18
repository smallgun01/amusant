from __future__ import annotations

import json
import unittest

from relay.hermes_result import (
    HERMES_RESULT_INSTRUCTIONS,
    build_hermes_run_prompt,
    parse_hermes_run_result,
)
from types import SimpleNamespace


def envelope(**overrides):
    value = {
        "schema_version": 1,
        "speak": "The report is ready.",
        "card": {"title": "Research complete", "lines": ["Three sources checked."]},
        "execution_status": "completed",
        "artifact_status": "committed",
        "presentation_status": "ready",
        "artifact_ref": "art_" + "a" * 24,
    }
    value.update(overrides)
    return json.dumps(value)


class HermesResultTests(unittest.TestCase):
    def test_accepts_only_bounded_facade_envelope_and_keeps_details_speech_only(self):
        result = parse_hermes_run_result(envelope())
        self.assertEqual(result.speak, "The report is ready.")
        self.assertEqual(result.card, {"title": "Research complete", "lines": ["Three sources checked."]})
        self.assertEqual(result.details_md, result.speak)

    def test_raw_text_and_legacy_details_md_are_rejected(self):
        for raw in ("A long raw report must never cross the facade boundary.", "{not json"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_hermes_run_result(raw)
        value = json.loads(envelope())
        value["details_md"] = "legacy full details"
        with self.assertRaisesRegex(ValueError, "field_set"):
            parse_hermes_run_result(json.dumps(value))

    def test_status_identity_and_shape_fields_are_not_model_controlled(self):
        invalid = (
            envelope(execution_status="running"),
            envelope(artifact_status="pending"),
            envelope(presentation_status="failed"),
            envelope(artifact_ref="../../private/report"),
            envelope(card={"title": "Title", "lines": []}),
            envelope(untrusted="extra"),
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_hermes_run_result(raw)

    def test_overlong_fields_are_rejected_instead_of_clipped(self):
        with self.assertRaisesRegex(ValueError, "speak_invalid"):
            parse_hermes_run_result(envelope(speak="x" * 241))
        with self.assertRaisesRegex(ValueError, "card_invalid"):
            parse_hermes_run_result(envelope(card={"title": "x" * 81, "lines": ["line"]}))
        with self.assertRaisesRegex(ValueError, "card_invalid"):
            parse_hermes_run_result(envelope(card={"title": "ok", "lines": ["x" * 121]}))

    def test_prompt_explicitly_bounds_legacy_details_field(self):
        self.assertIn("details_md to at most 512", HERMES_RESULT_INSTRUCTIONS)
        task = SimpleNamespace(task_kind="summarize", task="summarize the fixture")
        self.assertIn("Task request:", build_hermes_run_prompt(task))


if __name__ == "__main__":
    unittest.main()
