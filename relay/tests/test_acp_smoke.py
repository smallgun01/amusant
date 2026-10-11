import json
from pathlib import Path
import tempfile
import unittest

from websockets.asyncio.server import serve

from relay.acp_smoke import EXIT_CONFIG, EXIT_FAILED, EXIT_PASS, EXIT_UNCERTAIN, run_smoke
from relay.task_store import SQLiteTaskStore

try:
    from test_acp_client import ACP_KEY, FakeAcpAgent
except ImportError:
    from relay.tests.test_acp_client import ACP_KEY, FakeAcpAgent


class AcpSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, agent):
        server = await serve(agent.handler, "127.0.0.1", 0, max_size=64 * 1024)
        port = server.sockets[0].getsockname()[1]
        return server, f"ws://127.0.0.1:{port}/acp"

    def _env(self, url, db_path, **extra):
        env = {
            "AMUSANT_CLIENT_ID": "authenticated-client-1",
            "AMUSANT_BACKEND_ID": "openab",
            "AMUSANT_TASK_DB": str(db_path),
            "AMUSANT_ACP_URL": url,
            "AMUSANT_ACP_AUTH_KEY": ACP_KEY,
            "AMUSANT_ACP_CWD": "/openab/work",
            "AMUSANT_SMOKE_TASK": "Compare two local laptop options.",
        }
        env.update(extra)
        return env

    async def test_pass_requires_completed_inbox_result(self):
        agent = FakeAcpAgent()
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            try:
                report = await run_smoke(self._env(url, db_path))
            finally:
                server.close()
                await server.wait_closed()
            self.assertEqual(report.exit_code, EXIT_PASS)
            self.assertEqual(report.status, "completed")
            self.assertTrue(report.has_result)
            store = SQLiteTaskStore(database_path=db_path, max_pending_tasks=1)
            result = store.get_result(report.task_id)
            self.assertIn("Option B", result["speak"])
            self.assertEqual(store.get_inbox(report.task_id)["delivery_state"], "pending")
            self.assertEqual(agent.methods[:3], ["initialize", "session/new", "session/prompt"])
            prompt = agent.prompts[0]["prompt"]
            self.assertEqual(prompt[0]["type"], "text")
            self.assertNotIn("audio", json.dumps(prompt))

    async def test_overload_is_failed_not_pass(self):
        agent = FakeAcpAgent(mode="overload")
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            try:
                report = await run_smoke(self._env(url, db_path))
            finally:
                server.close()
                await server.wait_closed()
            self.assertEqual(report.exit_code, EXIT_FAILED)
            self.assertEqual(report.status, "failed")
            self.assertEqual(report.error, "acp_overloaded")
            self.assertFalse(report.has_result)

    async def test_disconnect_after_prompt_is_uncertain_not_pass(self):
        agent = FakeAcpAgent(mode="drop")
        server, url = await self._serve(agent)
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            try:
                report = await run_smoke(self._env(url, db_path))
            finally:
                server.close()
                await server.wait_closed()
            self.assertEqual(report.exit_code, EXIT_UNCERTAIN)
            self.assertEqual(report.status, "uncertain")

    async def test_gemini_env_is_rejected_and_missing_config_is_config_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "tasks.sqlite3"
            blocked = await run_smoke(self._env("ws://127.0.0.1:9/acp", db_path, AMUSANT_LIVE_PROVIDER="gemini"))
            self.assertEqual(blocked.exit_code, EXIT_CONFIG)
            missing = await run_smoke({"AMUSANT_CLIENT_ID": "authenticated-client-1"})
            self.assertEqual(missing.exit_code, EXIT_CONFIG)


if __name__ == "__main__":
    unittest.main()
