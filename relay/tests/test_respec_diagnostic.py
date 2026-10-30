from types import SimpleNamespace as Obj
import unittest
from pathlib import Path

from relay.evals.respec_diagnostic import CASES, observe
from relay.evals.routing import load_cases


class FakeLive:
    def __init__(self, messages):
        self.messages = messages

    async def send_client_content(self, **kwargs):
        pass

    async def receive(self):
        for message in self.messages:
            yield message


class RespecDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_task_without_dispatch(self):
        message = Obj(tool_call=Obj(function_calls=[Obj(name="delegate_task", args={"task": "找之前的虛構旅行計畫"})]))
        result = await observe(FakeLive([message]), [], 1)
        self.assertEqual(result["calls"][0]["task"], "找之前的虛構旅行計畫")
        self.assertEqual(result["transcript_status"], "UNKNOWN")

    async def test_ambiguous_response_is_left_for_human_review(self):
        message = Obj(tool_call=None, server_content=Obj(output_transcription=Obj(text="你是指哪件事？"), turn_complete=True))
        result = await observe(FakeLive([message]), [], 1)
        self.assertEqual(result["transcript"], "你是指哪件事？")
        self.assertEqual(result["calls"], [])
        self.assertNotIn("actual", result)

    async def test_malformed_task_preserves_call(self):
        for args in ({"task": 123}, {}, None, {"task": ""}):
            with self.subTest(args=args):
                message = Obj(tool_call=Obj(function_calls=[Obj(name="delegate_task", args=args)]))
                result = await observe(FakeLive([message]), [], 1)
                self.assertEqual(result["calls"][0]["name"], "delegate_task")
                self.assertEqual(result["calls"][0]["task_status"], "invalid_or_missing")

    def test_cases_include_separate_sources_and_clarification(self):
        self.assertEqual({c["expect"] for c in CASES}, {"FAST", "DELEGATE", "STATUS", "CLARIFY"})
        self.assertIn("Grok", next(c["say"] for c in CASES if c["id"] == "H01"))
        self.assertTrue(next(c["history"] for c in CASES if c["id"] == "H02"))

    def test_all_respec_cases_are_valid_and_status_cases_have_context(self):
        cases = load_cases(Path(__file__).parents[1] / "evals" / "respec_cases.jsonl")
        self.assertEqual(len(cases), 16)
        self.assertTrue(all(c.get("history") for c in cases if c["expect"] == "STATUS"))
        self.assertEqual({c["id"]: c for c in CASES if c["id"] not in ("C01", "C02")},
                         {c["id"]: c for c in cases if c["id"] in {"F01", "F02", "D01", "D03", "H01", "H02"}})

    def test_grok_status_description_distinguishes_sources(self):
        from google.genai import types
        from relay.slow_path import build_relay_tools
        declarations = {f.name: f.description for f in build_relay_tools(types, executor_kind="grok_callback").function_declarations}
        self.assertIn("even for a status question", declarations["check_task_status"])
        self.assertIn("another Agent interface", declarations["delegate_task"])
        self.assertIn("source is unclear", declarations["check_task_status"])
