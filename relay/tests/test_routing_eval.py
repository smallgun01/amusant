import asyncio
from pathlib import Path
from types import SimpleNamespace as Obj
import unittest

from relay.evals.routing import classify, compare, error_category, first_decision, load_cases, provider_environment, summarize


class FakeLive:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    async def send_client_content(self, **kwargs):
        self.sent.append(kwargs)

    async def receive(self):
        for message in self.messages:
            yield message


class RoutingEvalTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_executor_observes_only_and_never_dispatches(self):
        call = Obj(tool_call=Obj(function_calls=[Obj(name="delegate_task")]))
        live = FakeLive([call])
        result, latency = await first_decision(live, ["input"], 1)
        self.assertEqual(result, "DELEGATE")
        self.assertGreaterEqual(latency, 0)
        self.assertEqual(len(live.sent), 1)

    async def test_spoken_promise_does_not_count_as_delegation(self):
        live = FakeLive([Obj(tool_call=None, server_content=Obj(turn_complete=True,
                              output_transcription=Obj(text="好，我去處理")))])
        self.assertEqual((await first_decision(live, [], 1))[0], "FAST")
        self.assertEqual((await first_decision(FakeLive([]), [], 1))[0], "ERROR")

    def test_routes_and_multiple_calls(self):
        self.assertEqual(classify(["check_task_status"], False), "STATUS")
        self.assertEqual(classify(["cancel_task"], False), "OTHER")
        self.assertEqual(classify(["delegate_task", "delegate_task"], False), "MULTIPLE")
        self.assertEqual(classify([], False), "ERROR")

    def test_matrix_and_comparison(self):
        rows = [{"expected": "STATUS", "actual": "DELEGATE", "latency_s": 2.0},
                {"expected": "FAST", "actual": "FAST", "latency_s": 1.0}]
        summary = summarize(rows, 1)
        self.assertEqual(summary["accuracy"], 0.5)
        self.assertEqual(summary["confusion_matrix"]["STATUS"]["DELEGATE"], 1)
        self.assertEqual(summary["errors"]["STATUS->DELEGATE"], 1)
        config = {"model": "model", "voice": "Zephyr", "max_output_tokens": "unset", "search": "on", "repeat": 1,
                  "cases_sha256": "hash", "case_ids": ["a"], "sdk_version": "sdk", "source_revision": "before", "timeout_s": 40}
        before = {"config": config, "summary": summary}
        after = {"config": {**config, "source_revision": "after"}, "summary": summary}
        self.assertEqual(compare(before, after)["accuracy_delta"], 0)
        after["config"]["voice"] = "other"
        with self.assertRaises(ValueError):
            compare(before, after)

    def test_safe_error_counts_and_configuration(self):
        self.assertEqual(error_category(TimeoutError("secret")), "timeout")
        self.assertEqual(error_category(ConnectionError("secret")), "connection")
        self.assertEqual(error_category(RuntimeError("secret")), "other")
        error = RuntimeError("secret")
        error.code = 429
        self.assertEqual(error_category(error), "provider_4xx")
        summary = summarize([{"expected": "FAST", "actual": "ERROR", "latency_s": 1,
                              "error_category": "provider_4xx"}], 1)
        self.assertEqual(summary["provider_error_count"], 1)
        self.assertEqual(summary["provider_error_categories"], {"provider_4xx": 1})
        self.assertNotIn("secret", str(summary))
        args = Obj(model="model", voice="Zephyr", max_output_tokens="128", search="on")
        env = provider_environment(args, "fixture-key")
        self.assertEqual(env["AMUSANT_GEMINI_VOICE"], "Zephyr")
        self.assertEqual(env["AMUSANT_GEMINI_MAX_OUTPUT_TOKENS"], "128")
        self.assertEqual(env["AMUSANT_GEMINI_GOOGLE_SEARCH"], "1")
        args.voice = args.max_output_tokens = "unset"
        env = provider_environment(args, "fixture-key")
        self.assertNotIn("AMUSANT_GEMINI_VOICE", env)
        self.assertNotIn("AMUSANT_GEMINI_MAX_OUTPUT_TOKENS", env)

    def test_cases_include_expected_classes_and_context(self):
        cases = load_cases(Path(__file__).parents[1] / "evals" / "routing_cases.jsonl")
        self.assertEqual({case["expect"] for case in cases}, {"FAST", "DELEGATE", "STATUS"})
        self.assertTrue(any(case.get("history") and case["id"] == "accept_offer" for case in cases))

    def test_actual_sdk_relay_declarations_preserve_schema(self):
        try:
            from google.genai import types
        except ImportError:
            self.skipTest("google-genai is not installed")
        from relay.slow_path import build_relay_tools
        declarations = {f.name: f for f in build_relay_tools(types, executor_kind="grok_callback").function_declarations}
        self.assertEqual(set(declarations), {"delegate_task", "check_task_status", "cancel_task"})
        self.assertEqual(declarations["delegate_task"].parameters.required, ["task"])
        for executor in ("grok_callback", "hermes_runs", "openab_acp"):
            status = next(f for f in build_relay_tools(types, executor_kind=executor).function_declarations if f.name == "check_task_status")
            self.assertFalse(status.parameters.required)
            self.assertEqual(set(status.parameters.properties), {"task_id", "speak"})
            self.assertIn("omit task_id for the latest Relay task" if executor == "grok_callback" else "omit task_id for the latest task", status.description)
            if executor == "grok_callback":
                self.assertIn("even for a status question", status.description)
            else:
                self.assertIn("Do not delegate a new task to check progress", status.description)


if __name__ == "__main__":
    unittest.main()
