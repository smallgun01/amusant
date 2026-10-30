"""Synthetic-only first-call diagnostic. Never executes a function call."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import time

from google.genai import types
from fast_voice.factory import build_live_provider
from relay.companion_persona import JELLII_SYSTEM_INSTRUCTION
from relay.slow_path import build_relay_tools
from relay.evals.routing import load_cases

CASES = tuple(c for c in load_cases(Path(__file__).with_name("respec_cases.jsonl")) if c["id"] in {"F01", "F02", "D01", "D03", "H01", "H02"}) + (
    {"id": "C01", "say": "幫我處理一下。", "expect": "CLARIFY"},
    {"id": "C02", "say": "上次的事情後來怎麼了？", "expect": "CLARIFY"},
)


def capture_call(call):
    name = getattr(call, "name", None)
    args = getattr(call, "args", None)
    task = args.get("task") if isinstance(args, dict) else None
    valid = isinstance(task, str) and bool(task.strip())
    return {"name": name if name in ("delegate_task", "check_task_status", "cancel_task") else "OTHER",
            "task": task[:2000] if name == "delegate_task" and valid else None,
            "task_status": ("valid" if valid else "invalid_or_missing") if name == "delegate_task" else None}


async def observe(live, turns, timeout):
    text = []
    async with asyncio.timeout(timeout):
        await live.send_client_content(turns=turns, turn_complete=True)
        async for message in live.receive():
            calls = getattr(getattr(message, "tool_call", None), "function_calls", None) or []
            if calls:
                # Only synthetic fixtures reach this path. Never send a tool response.
                return {"calls": [capture_call(c) for c in calls], "transcript": "".join(text) or None, "transcript_status": "PARTIAL" if text else "UNKNOWN"}
            server = getattr(message, "server_content", None)
            out = getattr(getattr(server, "output_transcription", None), "text", None)
            if isinstance(out, str):
                text.append(out[:max(0, 4096 - sum(map(len, text)))])
            if getattr(server, "turn_complete", False):
                return {"calls": [], "transcript": "".join(text) or None, "transcript_status": "COMPLETE" if text and sum(map(len, text)) < 4096 else "PARTIAL" if text else "UNKNOWN"}
    return {"calls": [], "transcript": None, "transcript_status": "UNKNOWN"}


async def run(args):
    env = {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": os.environ["GEMINI_API_KEY"], "AMUSANT_GEMINI_MODEL": "gemini-3.8-live", "AMUSANT_GEMINI_VOICE": "Zephyr", "AMUSANT_GEMINI_GOOGLE_SEARCH": "1", "AMUSANT_GEMINI_MAX_OUTPUT_TOKENS": "1024"}
    provider = build_live_provider(env, tool_declarations=[build_relay_tools(types, executor_kind="grok_callback")], system_instruction=JELLII_SYSTEM_INSTRUCTION)
    rows = []
    for case in CASES:
        for iteration in range(1, args.repeat + 1):
            turns = [types.Content(role=role, parts=[types.Part(text=text)]) for role, text in [*case.get("history", []), ["user", case["say"]]]]
            start = time.monotonic()
            try:
                async with asyncio.timeout(40):
                    async with provider._client.aio.live.connect(model=provider._model, config=provider._config) as live:
                        result = await observe(live, turns, 40)
                result["error"] = None
            except Exception:
                result = {"calls": [], "transcript": None, "transcript_status": "UNKNOWN", "error": "PROVIDER_ERROR"}
            rows.append({"id": case["id"], "expected": case["expect"], "iteration": iteration, "seconds": round(time.monotonic() - start, 3), **result})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run_live or args.repeat < 1 or not os.environ.get("GEMINI_API_KEY"):
        parser.error("explicit --run-live, positive --repeat and GEMINI_API_KEY required")
    args.output.write_text(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
