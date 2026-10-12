"""moto-backed boto3 ledger tests. Creates a table only inside moto, never AWS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from botocore.exceptions import ClientError

from relay.dynamodb_task_store import DynamoDbTaskStore
from relay.task_store import (
    AdmissionUnavailable,
    AdmissionUnknown,
    BoundedTaskResult,
    TaskAdmissionRequest,
)

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


def _admit_request(task_id: str) -> TaskAdmissionRequest:
    return TaskAdmissionRequest(
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


class _TransactFaultClient:
    def __init__(self, inner, *, commit: bool, get_fails: bool = False, code: str = "InternalServerError", status: int = 500):
        self._inner = inner
        self.commit = commit
        self.get_fails = get_fails
        self.code = code
        self.status = status
        self.transact_calls = 0

    def transact_write_items(self, **kwargs):
        self.transact_calls += 1
        if self.commit:
            self._inner.transact_write_items(**kwargs)
        raise ClientError(
            {
                "Error": {"Code": self.code, "Message": "simulated"},
                "ResponseMetadata": {"HTTPStatusCode": self.status},
            },
            "TransactWriteItems",
        )

    def get_item(self, **kwargs):
        if self.get_fails and self.transact_calls > 0:
            raise ClientError(
                {
                    "Error": {"Code": "InternalServerError", "Message": "simulated"},
                    "ResponseMetadata": {"HTTPStatusCode": 500},
                },
                "GetItem",
            )
        return self._inner.get_item(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _QueryCaptureClient:
    def __init__(self, inner):
        self._inner = inner
        self.query_calls = []

    def query(self, **kwargs):
        self.query_calls.append(dict(kwargs))
        return self._inner.query(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class Boto3DynamoLedgerTests(unittest.TestCase):
    def test_has_client_tasks_uses_strong_partition_query(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            capture = _QueryCaptureClient(client)
            store = DynamoDbTaskStore(
                Boto3DynamoLedger(capture, TABLE),
                client_id=CLIENT,
                max_pending_tasks=1,
            )
            self.assertFalse(store.has_client_tasks(CLIENT))
            self.assertEqual(len(capture.query_calls), 1)
            self.assertTrue(capture.query_calls[-1]["ConsistentRead"])
            self.assertEqual(capture.query_calls[-1]["Limit"], 1)

            store.admit(_admit_request(str(uuid4())))
            self.assertTrue(store.has_client_tasks(CLIENT))
            self.assertTrue(capture.query_calls[-1]["ConsistentRead"])
            self.assertEqual(capture.query_calls[-1]["Limit"], 1)

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

    def test_internal_server_error_after_commit_returns_one_receipt(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            fault = _TransactFaultClient(client, commit=True)
            store = DynamoDbTaskStore(
                Boto3DynamoLedger(fault, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            task_id = str(uuid4())
            receipt = store.admit(_admit_request(task_id))
            self.assertEqual(receipt.task_id, task_id)
            self.assertEqual(fault.transact_calls, 1)
            self.assertEqual(store.pending_dispatch_count(), 1)
            again = store.admit(_admit_request(task_id))
            self.assertTrue(again.duplicate)
            self.assertEqual(again.task_id, task_id)
            self.assertEqual(fault.transact_calls, 1)

    def test_internal_server_error_without_commit_is_unknown(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            fault = _TransactFaultClient(client, commit=False)
            store = DynamoDbTaskStore(
                Boto3DynamoLedger(fault, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            with self.assertRaises(AdmissionUnknown):
                store.admit(_admit_request(str(uuid4())))
            self.assertEqual(fault.transact_calls, 1)
            self.assertEqual(store.pending_dispatch_count(), 0)

    def test_internal_server_error_then_failed_get_stays_unknown(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            fault = _TransactFaultClient(client, commit=True, get_fails=True)
            store = DynamoDbTaskStore(
                Boto3DynamoLedger(fault, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            with self.assertRaises(AdmissionUnknown):
                store.admit(_admit_request(str(uuid4())))
            self.assertEqual(fault.transact_calls, 1)

    def test_access_denied_stays_unavailable(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            fault = _TransactFaultClient(
                client, commit=False, code="AccessDeniedException", status=400
            )
            store = DynamoDbTaskStore(
                Boto3DynamoLedger(fault, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            with self.assertRaises(AdmissionUnavailable):
                store.admit(_admit_request(str(uuid4())))
            self.assertEqual(fault.transact_calls, 1)
            self.assertEqual(store.pending_dispatch_count(), 0)


if __name__ == "__main__":
    unittest.main()
