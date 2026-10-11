"""ADR-0002 fake-ledger tests. No AWS table or boto3 session."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from relay.dynamodb_task_store import DynamoDbTaskStore, InMemoryDynamoLedger
from relay.task_store import (
    AdmissionCapacity,
    AdmissionConflict,
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
        store.admit(
            _request(newer_id, created_at=NOW + timedelta(seconds=5), call_id="call-new", turn_id=2)
        )
        inbox = store.lookup_inbox(client_id=CLIENT)
        self.assertEqual(inbox["task_id"], newer_id)

    def test_claim_due_picks_oldest_created_at(self):
        ledger, store = _store()
        later_id = str(uuid4())
        earlier_id = str(uuid4())
        store.admit(_request(later_id, created_at=NOW + timedelta(seconds=10), call_id="c-late", turn_id=2))
        store.admit(_request(earlier_id, created_at=NOW, call_id="c-early", turn_id=1))
        claimed = store.claim_due(now=NOW + timedelta(seconds=1), lease_seconds=30, limit=1)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].task_id, earlier_id)

    def test_pending_capacity(self):
        ledger, store = _store(max_pending_tasks=1)
        store.admit(_request(str(uuid4()), call_id="only"))
        with self.assertRaises(AdmissionCapacity):
            store.admit(_request(str(uuid4()), call_id="two", turn_id=2))

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

    def test_recovery_requeues_unsent_and_uncertains_started(self):
        ledger, store = _store()
        unsent = str(uuid4())
        started = str(uuid4())
        store.admit(_request(unsent, call_id="unsent", turn_id=1))
        store.admit(_request(started, call_id="started", turn_id=2))
        claimed = store.claim_due(now=NOW, lease_seconds=30, limit=2)
        ids = {item.task_id for item in claimed}
        self.assertEqual(ids, {unsent, started})
        store.mark_dispatch_started(started, now=NOW)
        counts = store.recover_after_restart()
        self.assertEqual(counts.requeued, 1)
        self.assertEqual(counts.uncertain, 1)
        self.assertEqual(store.get_by_task_id(unsent)["status"], "queued")
        self.assertEqual(store.get_by_task_id(started)["status"], "uncertain")
        self.assertTrue(store.get_by_task_id(started)["dispatch_started"])

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


if __name__ == "__main__":
    unittest.main()
