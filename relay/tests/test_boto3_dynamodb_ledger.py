"""moto-backed boto3 ledger tests. Creates a table only inside moto, never AWS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from relay.dynamodb_task_store import DynamoDbTaskStore
from relay.task_store import BoundedTaskResult, TaskAdmissionRequest

CLIENT = "owner-1"
NOW = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)
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


class Boto3DynamoLedgerTests(unittest.TestCase):
    def test_completed_item_round_trip_through_moto(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            store = DynamoDbTaskStore(
                Boto3DynamoLedger(client, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            task_id = str(uuid4())
            store.admit(
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
            store.claim_due(now=NOW, lease_seconds=30, limit=1)
            store.mark_dispatch_started(task_id, now=NOW)
            store.complete(
                task_id,
                result=BoundedTaskResult(speak="Done.", card={"title": "Ok"}, details_md="md"),
            )
            reader = DynamoDbTaskStore(
                Boto3DynamoLedger(client, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            inbox = reader.lookup_inbox(client_id=CLIENT, task_id=task_id)
            self.assertEqual(inbox["status"], "completed")
            self.assertEqual(inbox["speak"], "Done.")


if __name__ == "__main__":
    unittest.main()
