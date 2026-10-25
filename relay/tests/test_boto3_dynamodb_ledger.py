"""moto-backed boto3 ledger tests. Creates a table only inside moto, never AWS."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from botocore.exceptions import ClientError

from relay.boto3_dynamodb_ledger import _CONDITIONS
from relay.dynamodb_task_store import QUERY_ITEM_BOUND, DynamoDbTaskStore
from relay.task_store import (
    AdmissionBusy,
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


def _admit_hermes_request(task_id: str, **changes) -> TaskAdmissionRequest:
    """Build a Hermes fixture with the same v2 pin required of production admissions."""
    return replace(
        _admit_request(task_id),
        executor_kind="hermes_runs",
        result_contract_version=2,
        **changes,
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


class _DeleteAfterGetClient:
    def __init__(self, inner):
        self._inner = inner
        self.delete_after_next_get = False

    def get_item(self, **kwargs):
        response = self._inner.get_item(**kwargs)
        if self.delete_after_next_get:
            self.delete_after_next_get = False
            self._inner.delete_item(TableName=kwargs["TableName"], Key=kwargs["Key"])
        return response

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


class _ExpiryUpdateBarrierClient:
    def __init__(self, inner):
        self._inner = inner
        self.expiry_update_started = threading.Event()
        self.allow_expiry_update = threading.Event()

    def update_item(self, **kwargs):
        values = kwargs.get("ExpressionAttributeValues") or {}
        if any(value == {"S": "slow_queue_expired"} for value in values.values()):
            self.expiry_update_started.set()
            if not self.allow_expiry_update.wait(timeout=5):
                raise TimeoutError("expiry race test barrier timed out")
        return self._inner.update_item(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class Boto3DynamoLedgerTests(unittest.TestCase):
    def test_recovery_conditions_guard_concurrent_stop_intent(self):
        base = (
            "outbox_state = :claimed",
            "attribute_exists(dispatch_started_at)",
            "attribute_exists(backend_run_id)",
        )
        no_stop = _CONDITIONS["claimed_started_run_recovery_no_stop"]
        stop_unaccepted = _CONDITIONS["claimed_started_run_recovery_stop_unaccepted"]
        stop_accepted = _CONDITIONS["claimed_started_run_recovery_stop_accepted"]
        for condition in (no_stop, stop_unaccepted, stop_accepted):
            for fragment in base:
                self.assertIn(fragment, condition)
        self.assertIn("attribute_not_exists(stop_requested_at)", no_stop)
        self.assertIn("attribute_exists(stop_requested_at)", stop_unaccepted)
        self.assertIn("attribute_not_exists(stop_accepted_at)", stop_unaccepted)
        self.assertIn("attribute_exists(stop_requested_at)", stop_accepted)
        self.assertIn("attribute_exists(stop_accepted_at)", stop_accepted)

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

    def test_independent_dynamodb_stores_atomically_admit_only_one_slow_task(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            first_store = DynamoDbTaskStore(Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8)
            second_store = DynamoDbTaskStore(Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8)
            requests = [
                TaskAdmissionRequest(
                    task_id=str(uuid4()),
                    client_id=CLIENT,
                    conversation_id="same-owner",
                    turn_id=index,
                    call_id=f"slot-race-{index}",
                    task=f"Concurrent Slow Task {index}",
                    task_kind="research",
                    backend="server-selected",
                    created_at=NOW,
                    expires_at=NOW + timedelta(minutes=10),
                    executor_kind="hermes_runs",
                )
                for index in (1, 2)
            ]

            def admit(args):
                store, request = args
                try:
                    return ("accepted", store.admit(request).task_id)
                except AdmissionBusy as exc:
                    return ("busy", exc.active_task_id)

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(admit, [(first_store, requests[0]), (second_store, requests[1])]))
            accepted = [value for state, value in results if state == "accepted"]
            busy = [value for state, value in results if state == "busy"]
            self.assertEqual(len(accepted), 1, results)
            self.assertEqual(busy, accepted)
            page = client.query(
                TableName=TABLE,
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": {"S": CLIENT}},
                ConsistentRead=True,
            )
            rows = page.get("Items", [])
            self.assertEqual(sum(item["sk"]["S"].startswith("TASK#") for item in rows), 1)
            self.assertEqual(sum(item["sk"]["S"].startswith("DEDUP#") for item in rows), 1)
            slot = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "SLOT#SLOW"}},
                ConsistentRead=True,
            )["Item"]
            self.assertEqual(slot["active_task_id"]["S"], accepted[0])

    def test_two_independent_moto_stores_expiry_claim_race_keeps_live_slot(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            barrier = _ExpiryUpdateBarrierClient(client)
            expirer = DynamoDbTaskStore(
                Boto3DynamoLedger(barrier, TABLE), client_id=CLIENT, max_pending_tasks=8
            )
            claimer = DynamoDbTaskStore(
                Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8
            )
            task_id = str(uuid4())
            active = TaskAdmissionRequest(
                task_id=task_id,
                client_id=CLIENT,
                conversation_id="expiry-claim-race",
                turn_id=1,
                call_id="expiring-task",
                task="A task at the queue deadline",
                task_kind="research",
                backend="server-selected",
                created_at=NOW,
                expires_at=NOW + timedelta(seconds=1),
                executor_kind="hermes_runs",
            )
            expirer.admit(active)
            next_id = str(uuid4())
            next_request = TaskAdmissionRequest(
                task_id=next_id,
                client_id=CLIENT,
                conversation_id="expiry-claim-race-next",
                turn_id=2,
                call_id="next-task",
                task="A distinct task",
                task_kind="research",
                backend="server-selected",
                created_at=NOW + timedelta(seconds=2),
                expires_at=NOW + timedelta(minutes=10),
                executor_kind="hermes_runs",
            )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(expirer.admit, next_request)
                self.assertTrue(barrier.expiry_update_started.wait(timeout=3))
                claimed = claimer.claim_due(now=NOW, lease_seconds=30, limit=1)
                self.assertEqual([row.task_id for row in claimed], [task_id])
                barrier.allow_expiry_update.set()
                with self.assertRaises(AdmissionBusy):
                    future.result(timeout=3)

            slot = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "SLOT#SLOW"}},
                ConsistentRead=True,
            )["Item"]
            active_item = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "TASK#" + task_id}},
                ConsistentRead=True,
            )["Item"]
            next_item = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "TASK#" + next_id}},
                ConsistentRead=True,
            ).get("Item")
            self.assertEqual(slot["active_task_id"]["S"], task_id)
            self.assertEqual((active_item["status"]["S"], active_item["outbox_state"]["S"]), ("running", "claimed"))
            self.assertIsNone(next_item)

    def test_confirmed_expiry_transition_releases_slot_before_new_admission(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            store = DynamoDbTaskStore(Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8)
            expired_id = str(uuid4())
            expired_request = _admit_hermes_request(
                expired_id,
                expires_at=NOW + timedelta(seconds=1),
            )
            store.admit(expired_request)
            new_id = str(uuid4())
            next_request = _admit_hermes_request(
                new_id,
                conversation_id="after-expiry",
                turn_id=2,
                call_id="after-expiry",
                created_at=NOW + timedelta(seconds=2),
                expires_at=NOW + timedelta(minutes=10),
            )

            receipt = store.admit(next_request)
            self.assertEqual(receipt.task_id, new_id)
            self.assertFalse(receipt.duplicate)
            expired = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "TASK#" + expired_id}},
                ConsistentRead=True,
            )["Item"]
            self.assertEqual((expired["status"]["S"], expired["outbox_state"]["S"], expired["last_error"]["S"]),
                             ("failed", "failed", "slow_queue_expired"))
            slot = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "SLOT#SLOW"}},
                ConsistentRead=True,
            )["Item"]
            self.assertEqual(slot["active_task_id"]["S"], new_id)

    def test_more_than_100_retained_terminal_rows_do_not_block_new_admission(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            for index in range(QUERY_ITEM_BOUND + 7):
                client.put_item(
                    TableName=TABLE,
                    Item={
                        "pk": {"S": CLIENT},
                        "sk": {"S": f"TASK#history-{index:03d}"},
                        "task_id": {"S": f"history-{index:03d}"},
                        "status": {"S": "completed"},
                        "created_at": {"S": (NOW + timedelta(seconds=index)).isoformat()},
                        "retain_until": {"N": str(int(NOW.timestamp()) + 30 * 24 * 60 * 60)},
                    },
                )

            capture = _QueryCaptureClient(client)
            store = DynamoDbTaskStore(
                Boto3DynamoLedger(capture, TABLE),
                client_id=CLIENT,
                max_pending_tasks=8,
            )
            receipt = store.admit(_admit_request(str(uuid4())))

            self.assertFalse(receipt.duplicate)
            self.assertGreaterEqual(len(capture.query_calls), 2)
            slot = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "SLOT#SLOW"}},
                ConsistentRead=True,
            )["Item"]
            self.assertEqual(slot["active_task_id"]["S"], receipt.task_id)

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

    def test_deferred_known_run_releases_claim_and_reclaims_only_at_persisted_due_time(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            task_id = str(uuid4())
            store = DynamoDbTaskStore(
                Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8
            )
            store.admit(TaskAdmissionRequest(
                task_id=task_id,
                client_id=CLIENT,
                conversation_id="conv",
                turn_id=1,
                call_id="deferred-poll-1",
                task="Synthetic deferred Run",
                task_kind="research",
                backend="server-selected",
                created_at=NOW,
                expires_at=NOW + timedelta(milliseconds=10),
                executor_kind="hermes_runs",
            ))
            claimed = store.claim_due(now=NOW, lease_seconds=30, limit=1)[0]
            started = NOW
            deadline = NOW + timedelta(seconds=60)
            store.mark_dispatch_started(task_id, now=started)
            store.save_backend_run_id(
                task_id,
                "run-boto-deferred",
                run_started_at=started,
                run_deadline_at=deadline,
                next_poll_at=NOW,
            )
            next_poll = NOW + timedelta(seconds=5)
            store.defer_hermes_run(
                task_id,
                backend_run_id="run-boto-deferred",
                run_started_at=started,
                run_deadline_at=deadline,
                next_poll_at=next_poll,
                poll_attempts=1,
            )
            row = store.get_by_task_id(task_id)
            self.assertEqual((row["status"], row["dispatch_state"]), ("running", "pending"))
            self.assertEqual(row["run_deadline_at"], deadline.isoformat())
            self.assertEqual(row["next_poll_at"], next_poll.isoformat())
            self.assertEqual(store.claim_due(now=NOW + timedelta(seconds=4), lease_seconds=30), [])
            self.assertEqual(store.next_hermes_wake_at(), next_poll)
            resumed = store.claim_due(now=next_poll, lease_seconds=30, limit=1)
            self.assertEqual(len(resumed), 1)
            self.assertEqual(resumed[0].backend_run_id, "run-boto-deferred")
            self.assertEqual(resumed[0].run_deadline_at, deadline.isoformat())
            self.assertEqual(resumed[0].attempts, claimed.attempts + 1)

    def test_two_independent_stores_atomically_reserve_one_owner_slot(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            first_store = DynamoDbTaskStore(Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8)
            second_store = DynamoDbTaskStore(Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8)
            requests = [
                TaskAdmissionRequest(
                    task_id=str(uuid4()),
                    client_id=CLIENT,
                    conversation_id="single-flight",
                    turn_id=index,
                    call_id=f"concurrent-{index}",
                    task=f"Concurrent request {index}",
                    task_kind="research",
                    backend="server-selected",
                    created_at=NOW,
                    expires_at=NOW + timedelta(minutes=10),
                    executor_kind="hermes_runs",
                )
                for index in (1, 2)
            ]

            def admit(args):
                store, request = args
                try:
                    return ("accepted", store.admit(request).task_id)
                except AdmissionBusy as exc:
                    return ("busy", exc.active_task_id)

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(admit, [(first_store, requests[0]), (second_store, requests[1])]))
            accepted = [value for kind, value in outcomes if kind == "accepted"]
            busy = [value for kind, value in outcomes if kind == "busy"]
            self.assertEqual(len(accepted), 1, outcomes)
            self.assertEqual(busy, accepted)
            page = client.query(
                TableName=TABLE,
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": {"S": CLIENT}},
                ConsistentRead=True,
            )
            task_rows = [item for item in page.get("Items", []) if item["sk"]["S"].startswith("TASK#")]
            dedup_rows = [item for item in page.get("Items", []) if item["sk"]["S"].startswith("DEDUP#")]
            self.assertEqual(len(task_rows), 1)
            self.assertEqual(len(dedup_rows), 1)
            slot = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": "SLOT#SLOW"}},
                ConsistentRead=True,
            )["Item"]
            self.assertEqual(slot["active_task_id"]["S"], accepted[0])

    def test_spoken_marker_does_not_recreate_deleted_task_after_strong_read(self):
        try:
            import boto3
            from moto import mock_aws
        except ImportError:
            self.skipTest("moto/boto3 not installed")
        with mock_aws():
            client = boto3.client("dynamodb", region_name="us-east-1")
            _create_table(client)
            from relay.boto3_dynamodb_ledger import Boto3DynamoLedger

            task_id = str(uuid4())
            writer = DynamoDbTaskStore(
                Boto3DynamoLedger(client, TABLE), client_id=CLIENT, max_pending_tasks=8
            )
            writer.admit(_admit_request(task_id))
            writer.claim_due(now=NOW, lease_seconds=30)
            writer.mark_dispatch_started(task_id, now=NOW)
            writer.complete(
                task_id,
                result=BoundedTaskResult(speak="Done.", card={"title": "Ok"}, details_md="md"),
            )
            writer.lookup_inbox(client_id=CLIENT, task_id=task_id)

            deleting = _DeleteAfterGetClient(client)
            deleting.delete_after_next_get = True
            reader = DynamoDbTaskStore(
                Boto3DynamoLedger(deleting, TABLE), client_id=CLIENT, max_pending_tasks=8
            )
            self.assertFalse(reader.mark_inbox_spoken(client_id=CLIENT, task_id=task_id, now=NOW))
            row = client.get_item(
                TableName=TABLE,
                Key={"pk": {"S": CLIENT}, "sk": {"S": f"TASK#{task_id}"}},
                ConsistentRead=True,
            ).get("Item")
            self.assertIsNone(row)

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
