"""Measure Gemini Live's first function decision without executing any tools.

See README.md before invoking --run-live. Importing this module makes no calls.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
LABELS = ("FAST", "DELEGATE", "STATUS", "OTHER", "MULTIPLE", "ERROR")


def classify(names: list[str], completed: bool) -> str:
    """A verbal promise is never a function call; multiple calls are failures."""
    if len(names) > 1:
        return "MULTIPLE"
    if names:
        return {"delegate_task": "DELEGATE", "check_task_status": "STATUS"}.get(names[0], "OTHER")
    return "FAST" if completed else "ERROR"


def load_cases(path: Path) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["id"] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("cases must be nonempty with unique IDs")
    for case in cases:
        if case["expect"] not in ("FAST", "DELEGATE", "STATUS") or not case["say"]:
            raise ValueError("invalid expected route or empty utterance")
        if any(role not in ("user", "model") or not text for role, text in case.get("history", [])):
            raise ValueError("invalid history")
    return cases


async def first_decision(live, turns, timeout: float) -> tuple[str, float]:
    """Fake executor: observe the first call batch, then close without any tool execution."""
    start = time.monotonic()
    async with asyncio.timeout(timeout):
        await live.send_client_content(turns=turns, turn_complete=True)
        async for message in live.receive():
            call = getattr(message, "tool_call", None)
            calls = getattr(call, "function_calls", None) or []
            if calls:
                return classify([getattr(fc, "name", "") for fc in calls], False), time.monotonic() - start
            server = getattr(message, "server_content", None)
            if server and getattr(server, "turn_complete", False):
                return "FAST", time.monotonic() - start
    return "ERROR", time.monotonic() - start


def summarize(rows: list[dict], repeat: int) -> dict:
    matrix = {expected: {actual: 0 for actual in LABELS} for expected in LABELS[:3]}
    for row in rows:
        matrix[row["expected"]][row["actual"]] += 1
    total = len(rows)
    correct = sum(matrix[k][k] for k in LABELS[:3])
    errors = Counter(f'{r["expected"]}->{r["actual"]}' for r in rows if r["expected"] != r["actual"])
    latencies = [r["latency_s"] for r in rows if r["latency_s"] is not None]
    provider_errors = Counter(r.get("error_category") for r in rows if r["actual"] == "ERROR")
    return {"trials": total, "repeat": repeat, "correct": correct,
            "accuracy": correct / total if total else None, "confusion_matrix": matrix,
            "errors": dict(sorted(errors.items())), "provider_error_count": sum(provider_errors.values()),
            "provider_error_categories": dict(sorted(provider_errors.items())),
            "mean_latency_s": sum(latencies) / len(latencies) if latencies else None}


def compare(before: dict, after: dict) -> dict:
    keys = ("model", "voice", "max_output_tokens", "search", "repeat", "cases_sha256", "case_ids", "sdk_version", "timeout_s")
    if any(before["config"][k] != after["config"][k] for k in keys):
        raise ValueError("comparison requires identical model, voice, output limit, SDK, Search, timeout, repetitions and cases")
    return {"before_source": before["config"]["source_revision"],
            "after_source": after["config"]["source_revision"],
            "before": before["summary"], "after": after["summary"],
            "accuracy_delta": after["summary"]["accuracy"] - before["summary"]["accuracy"]}


def error_category(exc: Exception) -> str:
    """Return a fixed safe label, never the exception message or provider payload."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "connection"
    code = getattr(exc, "code", None)
    if type(code) is int and 400 <= code <= 499:
        return "provider_4xx"
    if type(code) is int and 500 <= code <= 599:
        return "provider_5xx"
    return "other"


def provider_environment(args, api_key: str) -> dict:
    env = {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": api_key,
           "AMUSANT_GEMINI_MODEL": args.model, "AMUSANT_GEMINI_GOOGLE_SEARCH": "1" if args.search == "on" else "0"}
    if args.voice != "unset":
        env["AMUSANT_GEMINI_VOICE"] = args.voice
    if args.max_output_tokens != "unset":
        env["AMUSANT_GEMINI_MAX_OUTPUT_TOKENS"] = args.max_output_tokens
    return env


async def run_live(args, cases: list[dict]) -> dict:
    # Select a checkout before importing Amusant so a baseline and candidate can
    # use the same harness/cases, but their own persona, declarations and factory.
    if "relay" in sys.modules or "fast_voice" in sys.modules:
        raise RuntimeError("run as a script so --source-root selects both source packages")
    sys.path.insert(0, str(args.source_root.resolve()))
    from google import genai
    from google.genai import types
    from fast_voice.factory import build_live_provider
    from relay.companion_persona import JELLII_SYSTEM_INSTRUCTION
    from relay.slow_path import build_relay_tools

    env = provider_environment(args, os.environ["GEMINI_API_KEY"])
    provider = build_live_provider(env, tool_declarations=[build_relay_tools(types, executor_kind="grok_callback")],
                                   system_instruction=JELLII_SYSTEM_INSTRUCTION)
    rows = []
    for case in cases:
        for iteration in range(1, args.repeat + 1):
            turns = [types.Content(role=role, parts=[types.Part(text=text)])
                     for role, text in [*case.get("history", []), ["user", case["say"]]]]
            start = time.monotonic()
            category = None
            try:
                async with asyncio.timeout(args.timeout):
                    async with provider._client.aio.live.connect(model=provider._model, config=provider._config) as live:
                        actual, latency = await first_decision(live, turns, args.timeout)
                if actual == "ERROR":
                    category = "incomplete"
            except Exception as exc:
                # Do not print provider errors, transcripts, function arguments or credentials.
                actual, latency, category = "ERROR", time.monotonic() - start, error_category(exc)
            rows.append({"id": case["id"], "iteration": iteration, "expected": case["expect"],
                         "actual": actual, "latency_s": round(latency, 3), "error_category": category})
    return {"config": {"model": args.model, "voice": args.voice, "max_output_tokens": args.max_output_tokens,
                       "search": args.search, "repeat": args.repeat, "case_ids": [case["id"] for case in cases],
                       "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                       "source_revision": args.source_revision, "sdk_version": genai.__version__,
                       "timeout_s": args.timeout},
            "summary": summarize(rows, args.repeat), "results": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--run-live", action="store_true", help="acknowledge that this will incur provider charges")
    parser.add_argument("--model")
    parser.add_argument("--voice", help="explicit voice name, or unset if not configured")
    parser.add_argument("--max-output-tokens", help="explicit output token limit, or unset if not configured")
    parser.add_argument("--search", choices=("on", "off"), default="on")
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--source-root", type=Path, default=HERE.parent.parent)
    parser.add_argument("--source-revision")
    parser.add_argument("--cases", type=Path, default=HERE / "routing_cases.jsonl")
    parser.add_argument("--only", help="comma-separated case IDs for a separately approved smoke test")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=40)
    args = parser.parse_args()
    if args.compare:
        print(json.dumps(compare(*(json.loads(p.read_text(encoding="utf-8")) for p in args.compare)), indent=2))
        return 0
    if not args.run_live or not args.model or not args.voice or not args.max_output_tokens or not args.repeat or args.repeat < 1 or not args.output or not args.source_revision or args.timeout <= 0:
        parser.error("live runs require --run-live, --model, --voice, --max-output-tokens, --repeat, --output and --source-revision")
    if args.max_output_tokens != "unset" and (not args.max_output_tokens.isdecimal() or not 1 <= int(args.max_output_tokens) <= 8192):
        parser.error("--max-output-tokens must be unset or an integer from 1 to 8192")
    if not os.environ.get("GEMINI_API_KEY"):
        parser.error("GEMINI_API_KEY is required")
    cases = load_cases(args.cases)
    if args.only:
        selected = set(args.only.split(","))
        cases = [case for case in cases if case["id"] in selected]
        if not cases or {case["id"] for case in cases} != selected:
            parser.error("--only contains unknown or empty case IDs")
    result = asyncio.run(run_live(args, cases))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
