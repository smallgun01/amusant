from __future__ import annotations

import json
import unittest

from relay.hermes_result import (
    HERMES_RESULT_INSTRUCTIONS,
    HERMES_RESULT_V2_INSTRUCTIONS,
    CONTENTFUL_SPEECH_INSTRUCTIONS,
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

    def test_raw_text_is_rejected_and_legacy_details_md_is_consumed(self):
        for raw in ("A long raw report must never cross the facade boundary.", "{not json"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_hermes_run_result(raw)
        value = json.loads(envelope())
        value["details_md"] = "short legacy detail"
        result = parse_hermes_run_result(json.dumps(value))
        self.assertEqual(result.details_md, result.speak)
        value["details_md"] = "x" * 513
        with self.assertRaisesRegex(ValueError, "legacy_details_invalid"):
            parse_hermes_run_result(json.dumps(value))

    def test_facade_presentation_failure_keeps_committed_report_retrievable(self):
        value = json.loads(envelope())
        value.update({
            "speak": "The report is saved; its short presentation needs recovery.",
            "card": {
                "title": "Report saved",
                "lines": ["Short presentation needs recovery."],
            },
            "presentation_status": "failed",
        })
        result = parse_hermes_run_result(json.dumps(value))
        self.assertEqual(
            result.speak,
            "The report is saved; its short presentation needs recovery.",
        )
        self.assertEqual(result.card, {
            "title": "Report saved",
            "lines": ["Short presentation needs recovery."],
        })
        self.assertEqual(result.details_md, result.speak)

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

    def test_prompt_requests_contentful_spoken_findings_for_both_contract_versions(self):
        for version in (1, 2):
            with self.subTest(version=version):
                task = SimpleNamespace(
                    task_kind="compare",
                    task="Compare BLE and SoftAP provisioning.",
                    result_contract_version=version,
                )
                prompt = build_hermes_run_prompt(task)
                self.assertIn("self-contained spoken answer", prompt)
                self.assertIn("key trade-off", prompt)
                self.assertIn("Taiwan Traditional Chinese", prompt)
                self.assertIn("within 240 characters", prompt)
                self.assertIn("Do not expose full reports", prompt)
                self.assertIn(f"schema_version={version}", prompt)

    def test_contentful_speech_instruction_preserves_bounded_relay_contract(self):
        self.assertIn("not just a completion notice", CONTENTFUL_SPEECH_INSTRUCTIONS)
        self.assertIn("meaningful uncertainty", CONTENTFUL_SPEECH_INSTRUCTIONS)
        self.assertEqual(parse_hermes_run_result(envelope()).details_md, "The report is ready.")
        self.assertRaisesRegex(
            ValueError, "speak_invalid",
            parse_hermes_run_result, envelope(speak="x" * 241),
        )

    def test_v2_accepts_optional_and_committed_artifact_envelopes(self):
        base = {
            "schema_version": 2,
            "speak": "I compared the options.",
            "card": {"title": "Comparison", "lines": ["Option A costs less."]},
            "execution_status": "completed",
            "artifact_status": "none",
            "presentation_status": "ready",
            "artifact_ref": None,
        }
        answer = parse_hermes_run_result(json.dumps(base), expected_version=2)
        self.assertEqual(answer.artifact_status, "none")
        self.assertIsNone(answer.artifact_ref)
        self.assertEqual(answer.presentation_status, "ready")

        committed = {**base, "artifact_status": "committed", "artifact_ref": "art_" + "a" * 24}
        report = parse_hermes_run_result(json.dumps(committed), expected_version=2)
        self.assertEqual(report.artifact_status, "committed")
        self.assertEqual(report.artifact_ref, committed["artifact_ref"])
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            parse_hermes_run_result(json.dumps(base))

    def test_v2_failure_envelope_is_bounded_and_never_claims_delivery(self):
        failure = {
            "schema_version": 2,
            "speak": "The task finished, but result delivery needs recovery.",
            "card": {"title": "Result needs recovery", "lines": ["The short result could not be safely delivered."]},
            "execution_status": "completed",
            "artifact_status": "failed",
            "presentation_status": "failed",
            "artifact_ref": None,
        }
        result = parse_hermes_run_result(json.dumps(failure), expected_version=2)
        self.assertEqual(result.presentation_status, "failed")
        self.assertEqual(result.delivery_error, "artifact_write_failed")
        self.assertEqual(result.speak, failure["speak"])
        failure["artifact_status"] = "none"
        failure["speak"] = "The report was saved!"
        with self.assertRaisesRegex(ValueError, "presentation_failure_invalid"):
            parse_hermes_run_result(json.dumps(failure), expected_version=2)

    def test_v2_prompt_instructs_report_ack_and_optional_short_answer(self):
        task = SimpleNamespace(task_kind="research", task="compare options", result_contract_version=2)
        prompt = build_hermes_run_prompt(task)
        self.assertIn("save_report", prompt)
        self.assertIn("committed=true", prompt)
        self.assertIn("may be returned without an Artifact", prompt)
        self.assertIn("schema_version=2", HERMES_RESULT_V2_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
