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
        self.assertIn("relay.wss_preflight_smoke", text)
        self.assertNotIn("relay.acp_smoke", text)
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

    def test_durability_templates_are_placeholders_and_least_privilege(self):
        table = (ROOT / "deploy/amusant-task-ledger.table.json").read_text()
        writer = (ROOT / "deploy/amusant-relay-task-role.iam.json").read_text()
        reader = (ROOT / "deploy/amusant-store-reader.iam.json").read_text()
        writer_trust = (ROOT / "deploy/amusant-relay-task-role.trust.json").read_text()
        reader_trust = (ROOT / "deploy/amusant-store-reader.trust.json").read_text()
        taskdef = (ROOT / "deploy/oab-amusant-relay-dynamodb.taskdef.json").read_text()
        envelope = (ROOT / "docs/p4-durability-one-off.md").read_text()
        get_src = (ROOT / "relay/store_get.py").read_text()
        self.assertIn("REPLACE_TABLE_NAME", table)
        self.assertIn("PAY_PER_REQUEST", table)
        self.assertIn("REPLACE_MAX_READ_RRU", table)
        self.assertIn("REPLACE_MAX_WRITE_WRU", table)
        self.assertNotIn("CreateTable", get_src)
        self.assertIn("dynamodb:PutItem", writer)
        self.assertNotIn("dynamodb:TransactWriteItems", writer)
        self.assertNotIn("dynamodb:CreateTable", writer)
        self.assertNotIn('"*"', writer)
        self.assertIn("ecs-tasks.amazonaws.com", writer_trust)
        self.assertIn("arn:aws:ecs:us-east-1:REPLACE_ACCOUNT_ID:*", writer_trust)
        self.assertNotIn("task-definition/", writer_trust)
        self.assertIn("REPLACE_READER_PRINCIPAL_ARN", reader_trust)
        self.assertIn("dynamodb:GetItem", reader)
        self.assertNotIn("dynamodb:PutItem", reader)
        self.assertNotIn("dynamodb:TransactWriteItems", reader)
        self.assertIn("REPLACE_RELAY_TASK_ROLE", taskdef)
        self.assertIn("AMUSANT_TASK_STORE", taskdef)
        self.assertIn("REPLACE_RUN_CLIENT_ID", taskdef)
        self.assertNotIn("amusant-a1", taskdef)
        self.assertIn("dynamodb", taskdef)
        self.assertNotIn("AMUSANT_ACP_ALLOW_PLAINTEXT", taskdef)
        self.assertIn("AMUSANT_TASK_STORE must be dynamodb", get_src)
        self.assertIn("AMUSANT_DDB_ENDPOINT", get_src)
        self.assertIn("AWS_ENDPOINT_URL_DYNAMODB", get_src)
        self.assertIn("ignore_configured_endpoint_urls", get_src)
        self.assertIn("cannot certify that the writer stopped", envelope)
        bundle = table + writer + reader + writer_trust + reader_trust + taskdef
        self.assertNotIn("361450365446", bundle)


if __name__ == "__main__":
    unittest.main()
