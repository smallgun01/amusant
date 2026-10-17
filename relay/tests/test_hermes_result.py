from __future__ import annotations

import json
import unittest

from relay.hermes_result import (
    MAX_INPUT_CHARS,
    build_hermes_run_prompt,
    parse_hermes_run_result,
)
from relay.task_store import ClaimedTask, MAX_TASK_CHARS


def make_task(task: str, task_kind: str = "research") -> ClaimedTask:
    return ClaimedTask(
        task_id="task-01",
        client_id="client-a",
        conversation_id="conversation-01",
        turn_id=1,
        call_id="call-01",
        task=task,
        task_kind=task_kind,
        backend="server-selected",
        attempts=1,
        executor_kind="hermes_runs",
    )


class HermesRunResultTests(unittest.TestCase):
    def test_v1_result_maps_only_spoken_card_and_details_fields(self):
        raw = json.dumps({
            "schema_version": 1,
            "speak": "  First sentence.\nSecond sentence. ",
            "card": {"title": "Summary", "lines": ["One", "Two"]},
            "details_md": "# Details\nMore information.",
            "status": "failed",
            "task_id": "another-owner-task",
            "owner": "another-owner",
            "permissions": ["shell"],
            "tool_call": {"name": "delegate_task"},
        })
        result = parse_hermes_run_result(raw)
        self.assertEqual(result.speak, "First sentence. Second sentence.")
        self.assertEqual(result.card, {"title": "Summary", "lines": ["One", "Two"]})
        self.assertEqual(result.details_md, "# Details\nMore information.")
        self.assertFalse(hasattr(result, "status"))
        self.assertFalse(hasattr(result, "task_id"))
        self.assertFalse(hasattr(result, "permissions"))

    def test_plain_and_malformed_json_use_bounded_text_fallback(self):
        plain = parse_hermes_run_result("A plain deterministic answer.")
        self.assertEqual(plain.speak, "A plain deterministic answer.")
        malformed = parse_hermes_run_result('{"schema_version":1,"speak":"unfinished')
        self.assertIn("schema_version", malformed.details_md)
        self.assertLessEqual(len(malformed.speak), 240)
        self.assertLessEqual(len(malformed.details_md), 4096)

    def test_unsupported_schema_uses_only_known_text_fallback(self):
        result = parse_hermes_run_result(json.dumps({
            "schema_version": 2,
            "text": "Legacy result text.",
            "status": "completed",
            "task_id": "untrusted-id",
        }))
        self.assertEqual(result.speak, "Legacy result text.")
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            parse_hermes_run_result(json.dumps({"schema_version": 2, "status": "completed", "task_id": "x"}))

    def test_empty_or_missing_spoken_result_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            parse_hermes_run_result("\x00\t ")
        with self.assertRaisesRegex(ValueError, "speak_missing"):
            parse_hermes_run_result(json.dumps({
                "schema_version": 1,
                "speak": " ",
                "card": {"title": "", "lines": []},
                "details_md": "",
            }))

    def test_structured_fields_are_sanitized_and_bounded(self):
        result = parse_hermes_run_result(json.dumps({
            "schema_version": 1,
            "speak": "S" * 250 + "\x00",
            "card": {"title": "T" * 120, "lines": ["L" * 200] * 7},
            "details_md": "D" * 5000,
        }))
        self.assertEqual(len(result.speak), 240)
        self.assertEqual(len(result.card["title"]), 80)
        self.assertEqual(len(result.card["lines"]), 4)
        self.assertTrue(all(len(line) <= 120 for line in result.card["lines"]))
        self.assertEqual(len(result.details_md), 4096)

    def test_prompt_preserves_task_bound_and_reserves_schema_instructions(self):
        task = make_task("Q" * MAX_TASK_CHARS)
        prompt = build_hermes_run_prompt(task)
        self.assertLessEqual(len(prompt), MAX_INPUT_CHARS)
        self.assertIn(task.task, prompt)
        self.assertIn("schema_version=1", prompt)
        self.assertIn("untrusted data", prompt)
        with self.assertRaisesRegex(ValueError, "task_invalid"):
            build_hermes_run_prompt(make_task("Q" * (MAX_TASK_CHARS + 1)))


if __name__ == "__main__":
    unittest.main()
