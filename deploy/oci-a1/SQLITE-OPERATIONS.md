# OCI SQLite Ledger maintenance

These commands are offline: they do not contact OCI, AWS, or a model, and do not change the running service. The SQLite Ledger remains the task store used by the OCI deployment; the existing WAL and FULL synchronous transaction settings are left intact.

## Consistent read-only snapshot

With the persistent volume mounted and a known existing SQLite Ledger:

```sh
python3 deploy/oci-a1/sqlite_ledger_maintenance.py backup --db /mnt/amusant-data/relay/task-ledger.sqlite3 --output /mnt/amusant-backups/task-ledger-YYYYMMDD.sqlite3
python3 deploy/oci-a1/sqlite_ledger_maintenance.py verify --db /mnt/amusant-backups/task-ledger-YYYYMMDD.sqlite3
python3 deploy/oci-a1/sqlite_ledger_maintenance.py retention-report --db /mnt/amusant-data/relay/task-ledger.sqlite3 --days 30
python3 -m unittest discover -s deploy/oci-a1/tests -p test_sqlite_ledger_maintenance.py -v
```

The backup tool requires a new destination and uses SQLite's online backup API, so it can take a consistent snapshot while WAL is enabled. It validates the snapshot with `integrity_check` and `foreign_key_check`, and creates the destination with mode 0600 on POSIX systems. A backup on the same VM is not an off-host backup. Before canary, arrange encrypted off-host copies and separately verify restore into an isolated empty data root; include the Hermes Home and artifact store in a coordinated recovery plan.

## Report-only retention

Retention does not delete or update records. `potential_terminal_rows` counts only old `completed` or `failed` tasks whose outbox is `sent` or `failed`, whose Inbox delivery is acknowledged (`read` or `spoken`), and which have no stop request or manual reconciliation marker. `protected_rows` counts every other task, including unread or missing Inbox entries, queued/running/uncertain/stopping/reconciled work, incomplete dispatch, and recent records. The two counts partition all `slow_tasks` rows from one read snapshot. `expires_at` is not deletion authority.

An automatic purge still requires a separately reviewed policy that proves task IDs, deduplication invariants, owner isolation, foreign-key cascades, historical readback, and retention agreement. Do not rely on DynamoDB TTL semantics for SQLite.

## Live stop/recovery gate

Restore a snapshot offline and verify Inbox, run ID/deadline, stop reservation, GET-only recovery, and no double execution. A snapshot made during a remote Run is not evidence that Hermes itself was snapshotted at the same instant. Suspend new admissions and reconcile active work for a full-service restore drill.
