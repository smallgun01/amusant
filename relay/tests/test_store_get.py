"""After-stop reader tests. moto table only, never AWS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from relay.dynamodb_task_store import DynamoDbTaskStore
from relay.store_get import EXIT_CONFIG, EXIT_FAILED, EXIT_PASS, run_get
from relay.task_store import BoundedTaskResult, TaskAdmissionRequest

CLIENT = "owner-1"
NOW = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)
TABLE = "amusant-task-ledger"


def _create_table(client) -> None:
    client.create_table(
        TableName=TABLE,
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=TABLE)


def _env(task_id: str) -> dict[str, str]:
    return {
        "AMUSANT_TASK_STORE": "dynamodb",
        "AMUSANT_DDB_TABLE": TABLE,
        "AMUSANT_DDB_REGION": "us-east-1",
        "AMUSANT_CLIENT_ID": CLIENT,
        "AMUSANT_TASK_ID": task_id,
    }


class StoreGetTests(unittest.TestCase):
    def test_completed_row_survives_new_reader_without_lookup(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            writer = DynamoDbTaskStore(
                Boto3DynamoLedger(client, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            task_id = str(uuid4())
            writer.admit(
                TaskAdmissionRequest(
                    task_id=task_id,
                    client_id=CLIENT,
                    conversation_id="conv",
                    turn_id=1,
                    call_id="call-1",
                    task="Compare current options",
                    task_kind="compare",
                    backend="openab",
                    created_at=NOW,
                    expires_at=NOW + timedelta(seconds=600),
                )
            )
            writer.claim_due(now=NOW, lease_seconds=30, limit=1)
            writer.mark_dispatch_started(task_id, now=NOW)
            writer.complete(
                task_id,
                result=BoundedTaskResult(speak="Done.", card={"title": "Ok"}, details_md="md"),
            )
            report = run_get(_env(task_id))
            self.assertEqual(report.exit_code, EXIT_PASS)
            self.assertEqual(report.status, "completed")
            self.assertEqual(report.delivery_state, "pending")
            self.assertTrue(report.has_result)
            self.assertEqual(writer.get_inbox(task_id)["delivery_state"], "pending")

    def test_missing_task_is_failed_not_pass(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            report = run_get(_env(str(uuid4())))
            self.assertEqual(report.exit_code, EXIT_FAILED)
            self.assertEqual(report.error, "task_not_found")
            self.assertFalse(report.has_result)

    def test_gemini_env_is_config_error(self):
        env = _env(str(uuid4()))
        env["AMUSANT_LIVE_PROVIDER"] = "gemini"
        report = run_get(env)
        self.assertEqual(report.exit_code, EXIT_CONFIG)


if __name__ == "__main__":
    unittest.main()
