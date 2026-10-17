"""ADR-0002 fake-ledger tests. No AWS table or boto3 session."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import unittest
from dataclasses import replace
from uuid import uuid4

from relay.dynamodb_task_store import DynamoDbTaskStore, InMemoryDynamoLedger
from relay.task_store import (
    AdmissionBusy,
    AdmissionCapacity,
    AdmissionConflict,
    AdmissionUnavailable,
    AdmissionUnknown,
    BoundedTaskResult,
    TaskAdmissionRequest,
)

CLIENT = "amusant-owner"
CONVERSATION = "conv-1"
NOW = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)


def _request(
    task_id: str,
    *,
    created_at: datetime = NOW,
    call_id: str = "call-1",
    task: str = "Compare current options",
    turn_id: int = 1,
) -> TaskAdmissionRequest:
    return TaskAdmissionRequest(
        task_id=task_id,
        client_id=CLIENT,
        conversation_id=CONVERSATION,
        turn_id=turn_id,
        call_id=call_id,
        task=task,
        task_kind="compare",
        backend="openab",
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=600),
    )


def _store(ledger=None, max_pending_tasks=8):
    backend = ledger or InMemoryDynamoLedger()
    return backend, DynamoDbTaskStore(backend, client_id=CLIENT, max_pending_tasks=max_pending_tasks)


class DynamoDbTaskStoreTests(unittest.TestCase):
    def test_latest_lookup_uses_created_at_not_task_id(self):
        ledger, store = _store()
        older_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        newer_id = "00000000-0000-4000-8000-000000000001"
        store.admit(_request(older_id, created_at=NOW, call_id="call-old", turn_id=1))
        store.claim_due(now=NOW, lease_seconds=30, limit=1)
        store.complete(older_id, result=BoundedTaskResult(speak="Old.", card={"title": "Old"}, details_md="old"))
        store.admit(
            _request(newer_id, created_at=NOW + timedelta(seconds=5), call_id="call-new", turn_id=2)
        )
        inbox = store.lookup_inbox(client_id=CLIENT)
        self.assertEqual(inbox["task_id"], newer_id)

    def test_second_same_owner_task_is_busy_not_queued_by_created_order(self):
        ledger, store = _store()
        later_id = str(uuid4())
        earlier_id = str(uuid4())
        store.admit(_request(later_id, created_at=NOW + timedelta(seconds=10), call_id="c-late", turn_id=2))
        with self.assertRaises(AdmissionBusy) as raised:
            store.admit(_request(earlier_id, created_at=NOW, call_id="c-early", turn_id=1))
        self.assertEqual(raised.exception.active_task_id, later_id)
        self.assertEqual(store.pending_dispatch_count(), 1)

    def test_pending_capacity_is_replaced_by_single_flight_busy(self):
        ledger, store = _store(max_pending_tasks=1)
        active_id = str(uuid4())
        store.admit(_request(active_id, call_id="only"))
        with self.assertRaises(AdmissionBusy) as raised:
            store.admit(_request(str(uuid4()), call_id="two", turn_id=2))
        self.assertEqual(raised.exception.active_task_id, active_id)

    def test_has_client_tasks_is_strongly_scoped_to_owner_partition(self):
        ledger, store = _store()
        isolated = DynamoDbTaskStore(ledger, client_id="isolated-run", max_pending_tasks=1)
        self.assertFalse(store.has_client_tasks(CLIENT))
        self.assertFalse(isolated.has_client_tasks("isolated-run"))

        store.admit(_request(str(uuid4())))
        self.assertTrue(store.has_client_tasks(CLIENT))
        self.assertFalse(isolated.has_client_tasks("isolated-run"))
        with self.assertRaises(AdmissionUnavailable):
            store.has_client_tasks("different-owner")

    def test_duplicate_and_conflict(self):
        ledger, store = _store()
        task_id = str(uuid4())
        first = store.admit(_request(task_id))
        again = store.admit(_request(task_id))
        self.assertTrue(again.duplicate)
        self.assertEqual(again.task_id, first.task_id)
        with self.assertRaises(AdmissionConflict):
            store.admit(_request(str(uuid4()), task="Different payload"))

    def test_ambiguous_transact_is_unknown_when_unread(self):
        ledger, store = _store()
        ledger.ambiguous_next = True
        with self.assertRaises(AdmissionUnknown):
            store.admit(_request(str(uuid4())))
        self.assertEqual(store.pending_dispatch_count(), 0)

    def test_ambiguous_after_commit_returns_matching_receipt(self):
        ledger, store = _store()
        task_id = str(uuid4())
        ledger.ambiguous_after_commit = True
        receipt = store.admit(_request(task_id))
        self.assertEqual(receipt.task_id, task_id)
        self.assertTrue(receipt.duplicate)
        self.assertEqual(store.pending_dispatch_count(), 1)
        again = store.admit(_request(task_id))
        self.assertTrue(again.duplicate)
        self.assertEqual(again.task_id, task_id)
        self.assertEqual(store.pending_dispatch_count(), 1)

    def test_ambiguous_reconcile_read_failure_stays_unknown(self):
        ledger, store = _store()
        task_id = str(uuid4())
        ledger.ambiguous_after_commit = True
        original = ledger.get_item
        calls = {"n": 0}

        def selective(pk, sk, *, consistent=True):
            calls["n"] += 1
            if calls["n"] >= 4:
                raise AdmissionUnavailable("task_store_unavailable")
            return original(pk, sk, consistent=consistent)

        ledger.get_item = selective  # type: ignore[method-assign]
        try:
            with self.assertRaises(AdmissionUnknown):
                store.admit(_request(task_id))
        finally:
            ledger.get_item = original  # type: ignore[method-assign]
        self.assertEqual(store.pending_dispatch_count(), 1)

    def test_recovery_requeues_unsent_and_uncertains_started(self):
        ledger, unsent_store = _store()
        started_owner = "started-owner"
        started_store = DynamoDbTaskStore(ledger, client_id=started_owner, max_pending_tasks=8)
        unsent = str(uuid4())
        started = str(uuid4())
        unsent_store.admit(_request(unsent, call_id="unsent", turn_id=1))
        started_store.admit(replace(_request(started, call_id="started", turn_id=2), client_id=started_owner))
        claimed_unsent = unsent_store.claim_due(now=NOW, lease_seconds=30, limit=1)
        claimed_started = started_store.claim_due(now=NOW, lease_seconds=30, limit=1)
        self.assertEqual(claimed_unsent[0].task_id, unsent)
        self.assertEqual(claimed_started[0].task_id, started)
        started_store.mark_dispatch_started(started, now=NOW)
        unsent_counts = unsent_store.recover_after_restart()
        started_counts = started_store.recover_after_restart()
        self.assertEqual((unsent_counts.requeued, unsent_counts.uncertain), (1, 0))
        self.assertEqual((started_counts.requeued, started_counts.uncertain), (0, 1))
        self.assertEqual(unsent_store.get_by_task_id(unsent)["status"], "queued")
        self.assertEqual(started_store.get_by_task_id(started)["status"], "uncertain")
        self.assertTrue(started_store.get_by_task_id(started)["dispatch_started"])
        task = ledger.get_item(started_owner, f"TASK#{started}")
        dedup = ledger.get_item(started_owner, f"DEDUP#{CONVERSATION}#2#started")
        self.assertEqual(task["retain_until"], dedup["retain_until"])
        self.assertGreater(task["retain_until"], int(NOW.timestamp()) + 86400)

    def test_expired_lease_does_not_resend_after_dispatch_started(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        store.claim_due(now=NOW, lease_seconds=30, limit=1)
        store.mark_dispatch_started(task_id, now=NOW)
        later = store.recover_after_restart()
        self.assertEqual(later.requeued, 0)
        self.assertEqual(later.uncertain, 1)
        self.assertEqual(store.claim_due(now=NOW + timedelta(seconds=120), lease_seconds=30), [])

    def test_completed_survives_new_store_instance(self):
        ledger, writer = _store()
        task_id = str(uuid4())
        writer.admit(_request(task_id))
        writer.claim_due(now=NOW, lease_seconds=30, limit=1)
        writer.mark_dispatch_started(task_id, now=NOW)
        writer.complete(
            task_id,
            result=BoundedTaskResult(speak="Done.", card={"title": "Result"}, details_md="ok"),
        )
        reader = DynamoDbTaskStore(ledger, client_id=CLIENT, max_pending_tasks=8)
        inbox = reader.lookup_inbox(client_id=CLIENT, task_id=task_id)
        self.assertEqual(inbox["status"], "completed")
        self.assertEqual(inbox["speak"], "Done.")
        self.assertEqual(inbox["delivery_state"], "read")

    def test_failed_is_not_completed_pass(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        store.claim_due(now=NOW, lease_seconds=30, limit=1)
        store.fail(task_id, code="acp_failed")
        inbox = store.lookup_inbox(client_id=CLIENT, task_id=task_id)
        self.assertEqual(inbox["status"], "failed")
        self.assertNotIn("speak", inbox)

    def test_retain_until_is_epoch_number_aligned_on_dedup(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        task = ledger.get_item(CLIENT, f"TASK#{task_id}")
        dedup = ledger.get_item(CLIENT, f"DEDUP#{CONVERSATION}#1#call-1")
        self.assertIsInstance(task["retain_until"], int)
        self.assertEqual(task["retain_until"], dedup["retain_until"])
        self.assertGreater(task["retain_until"], int(NOW.timestamp()))

    def test_failed_aligns_dedup_ttl(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        store.claim_due(now=NOW, lease_seconds=30, limit=1)
        store.fail(task_id, code="acp_failed")
        task = ledger.get_item(CLIENT, f"TASK#{task_id}")
        dedup = ledger.get_item(CLIENT, f"DEDUP#{CONVERSATION}#1#call-1")
        self.assertEqual(task["retain_until"], dedup["retain_until"])
        self.assertEqual(task["status"], "failed")

    def test_spoken_is_not_downgraded_to_read(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        store.claim_due(now=NOW, lease_seconds=30, limit=1)
        store.complete(
            task_id,
            result=BoundedTaskResult(speak="Done.", card={"title": "Ok"}, details_md="md"),
        )
        requested = store.lookup_inbox(client_id=CLIENT, task_id=task_id, speak=True)
        self.assertEqual(requested["delivery_state"], "read")
        self.assertEqual(store.get_inbox(task_id)["delivery_state"], "read")
        self.assertTrue(store.mark_inbox_spoken(client_id=CLIENT, task_id=task_id, now=NOW))
        later = store.lookup_inbox(client_id=CLIENT, task_id=task_id, speak=False)
        self.assertEqual(later["delivery_state"], "spoken")
        self.assertEqual(store.get_inbox(task_id)["delivery_state"], "spoken")
        self.assertTrue(store.mark_inbox_spoken(client_id=CLIENT, task_id=task_id, now=NOW))

    def test_mark_spoken_requires_completed_result_and_authenticated_owner(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        self.assertFalse(store.mark_inbox_spoken(client_id=CLIENT, task_id=task_id, now=NOW))
        with self.assertRaises(AdmissionUnavailable):
            store.mark_inbox_spoken(client_id="other-owner", task_id=task_id, now=NOW)
        row = ledger.get_item(CLIENT, f"TASK#{task_id}", consistent=True)
        self.assertIsNone(row.get("delivery_state"))

    def test_spoken_condition_rechecks_completed_result_after_read_race(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        claimed = store.claim_due(now=NOW, lease_seconds=30)
        store.mark_dispatch_started(task_id, now=NOW)
        store.complete(
            task_id,
            result=BoundedTaskResult(speak="Done.", card={"title": "Ok"}, details_md="md"),
        )
        store.lookup_inbox(client_id=CLIENT, task_id=task_id)
        original_update = ledger.update_item

        def status_changes_before_conditional_update(pk, sk, patch, *, condition):
            row = ledger.items[(pk, sk)]
            row["status"] = "running"
            row.pop("speak", None)
            return original_update(pk, sk, patch, condition=condition)

        ledger.update_item = status_changes_before_conditional_update
        self.assertFalse(store.mark_inbox_spoken(client_id=CLIENT, task_id=task_id, now=NOW))
        row = ledger.get_item(CLIENT, f"TASK#{task_id}", consistent=True)
        self.assertEqual(row["status"], "running")
        self.assertNotEqual(row.get("delivery_state"), "spoken")

    def test_capacity_one_serializes_two_threads(self):
        ledger, store = _store(max_pending_tasks=1)
        errors: list[BaseException] = []

        def worker(call_id: str, turn_id: int) -> None:
            try:
                store.admit(_request(str(uuid4()), call_id=call_id, turn_id=turn_id))
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=worker, args=("c-a", 1))
        second = threading.Thread(target=worker, args=("c-b", 2))
        first.start()
        second.start()
        first.join()
        second.join()
        self.assertEqual(sum(isinstance(item, AdmissionBusy) for item in errors), 1)
        self.assertEqual(store.pending_dispatch_count(), 1)

    def test_complete_inbox_stays_pending_without_lookup(self):
        ledger, store = _store()
        task_id = str(uuid4())
        store.admit(_request(task_id))
        store.claim_due(now=NOW, lease_seconds=30, limit=1)
        store.complete(
            task_id,
            result=BoundedTaskResult(speak="Done.", card={"title": "Ok"}, details_md="md"),
        )
        self.assertEqual(store.get_inbox(task_id)["delivery_state"], "pending")
        self.assertEqual(store.get_result(task_id)["speak"], "Done.")


if __name__ == "__main__":
    unittest.main()
