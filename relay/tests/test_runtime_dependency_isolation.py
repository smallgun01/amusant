"""Grok + SQLite startup does not import optional legacy adapters."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RuntimeDependencyIsolationTests(unittest.TestCase):
    def test_grok_sqlite_runtime_does_not_import_acp_hermes_or_dynamodb(self):
        script = r'''
import sys
import tempfile
from importlib.abc import MetaPathFinder
from pathlib import Path

blocked = {
    "relay.acp_client",
    "relay.hermes_runs_executor",
    "relay.dynamodb_task_store",
    "relay.boto3_dynamodb_ledger",
    "boto3",
}

class BlockLegacyImports(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError(f"unexpected optional import: {fullname}")

sys.meta_path.insert(0, BlockLegacyImports())

class FakeSchemaTypes:
    class Behavior:
        BLOCKING = "BLOCKING"
    class Schema:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class FunctionDeclaration:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Tool:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)

from relay.application import build_supported_relay_runtime

with tempfile.TemporaryDirectory(dir=".") as tmp:
    environ = {
        "AMUSANT_CLIENT_ID": "offline-owner",
        "AMUSANT_BACKEND_ID": "grok_bot",
        "AMUSANT_TASK_STORE": "sqlite",
        "AMUSANT_TASK_DB": str(Path(tmp).resolve() / "tasks.sqlite3"),
        "AMUSANT_SLOW_EXECUTOR": "grok_callback",
        "AMUSANT_GROK_ROUTINE_URL": "https://routine.example.com/webhook",
        "AMUSANT_GROK_ROUTINE_TOKEN": "offline-routine-token-123",
        "AMUSANT_GROK_CALLBACK_URL": "https://callback.example.com/callback/results",
        "HTTPS_PROXY": "http://egress_proxy:3128",
    }
    handler, dispatcher, store = build_supported_relay_runtime(
        expected_device_token="device-token-12",
        environ=environ,
        sdk_types=FakeSchemaTypes,
    )
    assert handler is not None and dispatcher is not None and store is not None
assert blocked.isdisjoint(sys.modules)
'''
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
