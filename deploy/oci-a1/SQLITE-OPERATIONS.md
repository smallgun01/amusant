# OCI SQLite Ledger maintenance

These commands are offline: they do not contact OCI, AWS, Hermes, or a model, and do not change a running service. The SQLite Ledger remains the OCI deployment's task store; its WAL and `FULL` synchronous settings are unchanged.

## Supported schemas and pre-upgrade backup

The standalone maintenance utility accepts only verified Ledger schema v8 and v9. It checks the schema version, required columns, and the `task_kind` constraint (`research/compare/summarize` in v8; those values plus `agent` in v9). Unknown versions or incomplete schemas fail closed. Use v8 support to make and verify a rollback point before the v8-to-v9 application upgrade.

Before an upgrade, suspend new admissions, drain or explicitly reconcile active tasks, and stop Relay so the application state does not move during the release snapshot. SQLite's online backup API can take a consistent snapshot while WAL is enabled, but it does not snapshot a remote Hermes Run, Hermes Home, or the artifact store at the same instant.

From the repository root, create a new backup and independently verify it:

```sh
python3 deploy/oci-a1/sqlite_ledger_maintenance.py backup \
  --db /mnt/amusant-data/relay/task-ledger.sqlite3 \
  --output /mnt/amusant-backups/task-ledger-pre-v9-YYYYMMDD.sqlite3
python3 deploy/oci-a1/sqlite_ledger_maintenance.py verify \
  --db /mnt/amusant-backups/task-ledger-pre-v9-YYYYMMDD.sqlite3
```

`backup` requires a new destination, reads the source without changing it, and validates both source and snapshot using `integrity_check`, `foreign_key_check`, and the version-specific Ledger schema guard. `verify` repeats those checks. The destination is created with mode 0600 on POSIX systems. Keep an encrypted off-host copy; a backup on the same VM is not an off-host recovery copy.

## Isolated restore and migration drill

Never use the production database as a migration fixture. Restore the verified backup into a new, empty scratch directory and run the v9 Relay code against that copy. Opening it with `SQLiteTaskStore` performs the normal v8-to-v9 migration; no Provider or OCI service is involved.

```sh
install -d -m 0700 /mnt/amusant-restore-drill
cp --no-clobber \
  /mnt/amusant-backups/task-ledger-pre-v9-YYYYMMDD.sqlite3 \
  /mnt/amusant-restore-drill/task-ledger.sqlite3
PYTHONPATH=. python3 - <<'PY'
from pathlib import Path
from relay.task_store import SQLiteTaskStore

SQLiteTaskStore(
    database_path=Path("/mnt/amusant-restore-drill/task-ledger.sqlite3"),
    max_pending_tasks=1,
)
PY
python3 deploy/oci-a1/sqlite_ledger_maintenance.py verify \
  --db /mnt/amusant-restore-drill/task-ledger.sqlite3
python3 -m unittest discover -s relay/tests -p test_task_store_migration.py -v
```

The migration regression uses an isolated historical v8 fixture and checks the full sequence: backup and pre-migration validation, migration of a restored copy, v9 integrity and foreign keys, replay of legacy calls without changing their stored fingerprint, and preservation of bounded results, Artifact metadata, and Inbox delivery state. It also verifies that the original and backup remain v8. A release drill should additionally compare the task/run state needed by the service owner before directing traffic to the upgraded data root.

Keep the original database and verified backup until the upgraded release is accepted. Do not overwrite a database or remove its WAL/SHM files while Relay is running. Before canary, separately verify an off-host restore and include Hermes Home and the artifact store in the coordinated recovery plan.

## Recovery and code rollback

If the v9 release fails before any generic `agent` task is admitted, stop Relay and restore the pre-v9 backup into a separate, empty data root; verify it as v8 before starting a v8-compatible release against that root. Do not copy a backup over an active database. The restore returns to the backup's point in time, so reconcile any work or delivery recorded after that snapshot before using it.

Once a v9 `agent` task exists, reverting only the application code is not a rollback. The database remains at schema v9, whose `slow_tasks.task_kind` constraint admits `agent`; a v8 reader neither migrates that schema backward nor understands generic admission/replay, and the v8 constraint cannot store new `agent` tasks. The generic task's ID, fingerprint, Run/STOP state, result, Artifact, and Inbox state must remain consistent for retries and recovery. Keep a v9-capable reader, stop new admissions and reconcile active generic tasks, or use a separately reviewed state-preserving data migration. Restoring the older backup can discard post-snapshot tasks and results. Do not relabel `agent` rows or rewrite fingerprints as a shortcut.

## Report-only retention

Retention does not delete or update records. `potential_terminal_rows` counts only `completed` or `failed` tasks whose creation time, last Inbox delivery/read activity (`task_inbox.updated_at`), and any result creation time are all older than the cutoff; the outbox must be `sent` or `failed`, Inbox delivery must be acknowledged (`read` or `spoken`), and there must be no stop request or manual reconciliation marker. Recent reads, speech delivery, or result completion keep an old task protected. `protected_rows` counts every other task, including unread or missing Inbox entries, queued/running/uncertain/stopping/reconciled work, incomplete dispatch, and recent records. The two counts partition all `slow_tasks` rows from one read snapshot. `expires_at` is not deletion authority.

An automatic purge still requires a separately reviewed policy that proves task IDs, deduplication invariants, owner isolation, foreign-key cascades, historical readback, and retention agreement. Do not rely on DynamoDB TTL semantics for SQLite.

## Live stop/recovery gate

Restore a snapshot offline and verify Inbox, Run ID/deadline, stop reservation, GET-only recovery, and no double execution. A database snapshot made during a remote Run is not evidence that Hermes itself was snapshotted at the same instant. Suspend new admissions and reconcile active work for a full-service restore drill.
