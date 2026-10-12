"""Store factory fail-closed tests. No live AWS."""
from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from relay.application import load_relay_process_config
from relay.dynamodb_task_store import DynamoDbTaskStore
from relay.store_factory import build_task_store
from relay.task_store import SQLiteTaskStore


TOKEN = "device-token-12"


class StoreFactoryTests(unittest.TestCase):
    def test_default_sqlite_requires_task_db(self):
        with self.assertRaises(ValueError):
            build_task_store({"AMUSANT_CLIENT_ID": "owner"})

    def test_sqlite_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "tasks.sqlite3"
        store = build_task_store(
            {
                "AMUSANT_CLIENT_ID": "owner",
                "AMUSANT_TASK_DB": str(path),
            }
        )
        self.assertIsInstance(store, SQLiteTaskStore)

    def test_dynamodb_requires_table_and_region(self):
        with self.assertRaises(ValueError):
            build_task_store({"AMUSANT_TASK_STORE": "dynamodb", "AMUSANT_CLIENT_ID": "owner"})
        with self.assertRaises(ValueError):
            build_task_store(
                {
                    "AMUSANT_TASK_STORE": "dynamodb",
                    "AMUSANT_CLIENT_ID": "owner",
                    "AMUSANT_DDB_TABLE": "amusant-task-ledger",
                }
            )

    def test_dynamodb_does_not_require_sqlite_path(self):
        store = build_task_store(
            {
                "AMUSANT_TASK_STORE": "dynamodb",
                "AMUSANT_CLIENT_ID": "owner-1",
                "AMUSANT_DDB_TABLE": "amusant-task-ledger",
                "AMUSANT_DDB_REGION": "us-east-1",
                "AMUSANT_DDB_ENDPOINT": "http://127.0.0.1:1",
            }
        )
        self.assertIsInstance(store, DynamoDbTaskStore)

    def test_unknown_store_kind_fails(self):
        with self.assertRaises(ValueError):
            build_task_store({"AMUSANT_TASK_STORE": "s3", "AMUSANT_CLIENT_ID": "owner"})

    def test_relay_config_dynamodb_skips_sqlite(self):
        config = load_relay_process_config(
            {
                "AMUSANT_CLIENT_ID": "owner-1",
                "AMUSANT_BACKEND_ID": "openab",
                "AMUSANT_TASK_STORE": "dynamodb",
                "AMUSANT_DDB_TABLE": "amusant-task-ledger",
            },
            expected_device_token=TOKEN,
        )
        self.assertEqual(config.task_store_kind, "dynamodb")
        self.assertIsNone(config.database_path)


if __name__ == "__main__":
    unittest.main()
