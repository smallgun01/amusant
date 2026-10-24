from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from relay.hermes_result import (
    MAX_INPUT_CHARS,
    CONTENTFUL_SPEECH_INSTRUCTIONS,
    HERMES_RESULT_INSTRUCTIONS,
    build_hermes_run_prompt,
    parse_hermes_run_result,
)
from relay.task_store import MAX_TASK_CHARS


def envelope(**overrides):
    value = {
        "schema_version": 2,
        "speak": "The findings favor option A.",
        "card": {"title": "Comparison", "lines": ["Option A costs less."]},
        "execution_status": "completed",
        "artifact_status": "none",
        "presentation_status": "ready",
        "artifact_ref": None,
    }
    value.update(overrides)
    return json.dumps(value)


class HermesResultTests(unittest.TestCase):
    def test_short_answer_without_artifact_is_a_bounded_completed_result(self):
        result = parse_hermes_run_result(envelope())
        self.assertEqual(result.speak, "The findings favor option A.")
        self.assertEqual(result.card, {"title": "Comparison", "lines": ["Option A costs less."]})
        self.assertEqual(result.artifact_status, "none")
        self.assertIsNone(result.artifact_ref)
        self.assertEqual(result.presentation_status, "ready")
        self.assertEqual(result.details_md, result.speak)

    def test_committed_full_report_uses_only_its_bounded_summary(self):
        reference = "art_" + "a" * 24
        result = parse_hermes_run_result(envelope(artifact_status="committed", artifact_ref=reference))
        self.assertEqual(result.artifact_status, "committed")
        self.assertEqual(result.artifact_ref, reference)
        self.assertEqual(result.details_md, result.speak)
        self.assertNotIn("complete report", result.speak)

    def test_raw_text_and_full_report_fields_are_rejected(self):
        for raw in ("A long raw report must never cross the facade boundary.", "{not json"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_hermes_run_result(raw)
        with self.assertRaisesRegex(ValueError, "field_set_invalid"):
            parse_hermes_run_result(envelope(report="complete report text"))

    def test_v1_schema_and_legacy_details_field_are_rejected(self):
        value = json.loads(envelope())
        value["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            parse_hermes_run_result(json.dumps(value))
        value["schema_version"] = 2
        value["details_md"] = "short old detail"
        with self.assertRaisesRegex(ValueError, "field_set_invalid"):
            parse_hermes_run_result(json.dumps(value))

    def test_facade_writer_failure_is_truthful_and_bounded(self):
        value = json.loads(envelope(
            speak="The task finished, but result delivery needs recovery.",
            card={"title": "Result needs recovery", "lines": ["The short result could not be safely delivered."]},
            artifact_status="failed",
            presentation_status="failed",
        ))
        result = parse_hermes_run_result(json.dumps(value))
        self.assertEqual(result.artifact_status, "failed")
        self.assertEqual(result.presentation_status, "failed")
        self.assertEqual(result.delivery_error, "artifact_write_failed")
        value["speak"] = "The report was saved!"
        with self.assertRaisesRegex(ValueError, "presentation_failure_invalid"):
            parse_hermes_run_result(json.dumps(value))

    def test_status_shape_and_artifact_binding_are_not_model_controlled(self):
        invalid = (
            envelope(execution_status="running"),
            envelope(artifact_status="pending"),
            envelope(presentation_status="unknown"),
            envelope(artifact_ref="../../private/report"),
            envelope(artifact_status="committed", artifact_ref="../../private/report"),
            envelope(card={"title": "Title", "lines": []}),
            envelope(untrusted="extra"),
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_hermes_run_result(raw)

    def test_oversized_and_malformed_envelopes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "over_cap"):
            parse_hermes_run_result(" " * 3001)
        with self.assertRaisesRegex(ValueError, "malformed_json"):
            parse_hermes_run_result('{"schema_version":2')

    def test_speech_and_card_limits_remain_bounded(self):
        with self.assertRaisesRegex(ValueError, "speak_invalid"):
            parse_hermes_run_result(envelope(speak="x" * 241))
        with self.assertRaisesRegex(ValueError, "card_invalid"):
            parse_hermes_run_result(envelope(card={"title": "x" * 81, "lines": ["line"]}))
        with self.assertRaisesRegex(ValueError, "card_invalid"):
            parse_hermes_run_result(envelope(card={"title": "ok", "lines": ["x" * 121]}))

    def test_prompt_always_requests_v2_and_rejects_a_v1_task_pin(self):
        task = SimpleNamespace(
            task_kind="compare",
            task="Compare BLE and SoftAP provisioning.",
            result_contract_version=2,
        )
        prompt = build_hermes_run_prompt(task)
        self.assertIn("schema_version=2", prompt)
        self.assertIn("save_report", prompt)
        self.assertIn("committed=true", prompt)
        self.assertIn("may be returned without an Artifact", prompt)
        self.assertIn("Speak findings, not a completion notice", prompt)
        self.assertIn("trade-offs and choice criteria", prompt)
        self.assertIn("Taiwan Traditional Chinese", prompt)
        self.assertIn("keep technical terms", prompt)
        self.assertIn("Be factual", prompt)
        task.result_contract_version = 1
        with self.assertRaisesRegex(ValueError, "hermes_result_contract_unsupported"):
            build_hermes_run_prompt(task)

    def test_maximum_admitted_task_remains_accepted_after_quality_guidance(self):
        task = SimpleNamespace(
            task_kind="summarize",
            task="x" * MAX_TASK_CHARS,
            result_contract_version=2,
        )
        prompt = build_hermes_run_prompt(task)
        self.assertLessEqual(len(prompt), MAX_INPUT_CHARS)
        self.assertIn(CONTENTFUL_SPEECH_INSTRUCTIONS, prompt)
        task.task = "x" * (MAX_TASK_CHARS + 1)
        with self.assertRaisesRegex(ValueError, "hermes_task_invalid"):
            build_hermes_run_prompt(task)

    def test_active_result_instructions_exclude_details_and_keep_report_private(self):
        self.assertIn("schema_version=2", HERMES_RESULT_INSTRUCTIONS)
        self.assertIn("Do not put the full report in this object", HERMES_RESULT_INSTRUCTIONS)
        self.assertIn("details_md", HERMES_RESULT_INSTRUCTIONS)
        self.assertIn("Be factual", CONTENTFUL_SPEECH_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
