"""Packaging pins. Does not build or push an image."""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_dockerfile_uses_pypi_uv_not_ghcr(self):
        text = (ROOT / "Dockerfile").read_text()
        self.assertIn("FROM python:3.12-slim-bookworm", text)
        self.assertIn("uv==0.11.25", text)
        self.assertNotIn("ghcr.io/astral-sh/uv", text)
        self.assertIn("relay.acp_smoke", text)
        self.assertIn("/app/.venv/bin/python", text)
        self.assertNotIn("uv run", text)

    def test_production_lockfile_export_excludes_moto(self):
        import subprocess

        exported = subprocess.check_output(
            ["uv", "export", "--frozen", "--no-dev"],
            cwd=ROOT,
            text=True,
        )
        self.assertIn("boto3", exported)
        self.assertNotIn("moto", exported)

    def test_taskdef_has_placeholders_not_account_ids(self):
        text = (ROOT / "deploy/oab-amusant-relay.taskdef.json").read_text()
        self.assertIn("REPLACE_ACCOUNT_ID", text)
        self.assertIn("REPLACE_ECR_IMAGE_URI", text)
        self.assertIn("REPLACE_ACP_WSS_URL", text)
        self.assertNotIn("361450365446", text)
        self.assertIn("AMUSANT_TASK_STORE", text)
        self.assertNotIn("AMUSANT_ACP_ALLOW_PLAINTEXT", text)


if __name__ == "__main__":
    unittest.main()
