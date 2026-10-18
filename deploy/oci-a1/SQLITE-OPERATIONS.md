# OCI SQLite Ledger maintenance — offline additions

These commands are **not** an OCI deployment, do not contact AWS or a model, and do not change the existing DynamoDB table. The Relay's existing SQLite WAL and FULL synchronous transactions remain unchanged.

## Consistent read-only snapshot

With the persistent volume mounted and *a known existing* SQLite Ledger:

```sh
python3 deploy/oci-a1/sqlite_ledger_maintenance.py backup --db /mnt/amusant-data/relay/task-ledger.sqlite3 --output /mnt/amusant-backups/task-ledger-YYYYMMDD.sqlite3
python3 deploy/oci-a1/sqlite_ledger_maintenance.py verify --db /mnt/amusant-backups/task-ledger-YYYYMMDD.sqlite3
python3 deploy/oci-a1/sqlite_ledger_maintenance.py retention-report --db /mnt/amusant-data/relay/task-ledger.sqlite3 --days 30
python3 -m unittest discover -s deploy/oci-a1/tests -p test_sqlite_ledger_maintenance.py -v
```

The backup tool creates a new file mode 0600 with SQLite's online backup API (not a raw WAL copy). It runs `integrity_check` and `foreign_key_check` on the snapshot. **A backup on the same VM is not an off-host backup.** Before canary, arrange encrypted off-host copies and separately verify restore into an isolated empty data root; include the Hermes Home / artifact store in a coordinated recovery plan.

**Retention remains report-only.** The dry-run counts terminal 30+ day entries with acknowledged/read delivery and excludes running/queued/uncertain or stop/reconciled tasks. It intentionally does not delete records: an automatic purge must also prove task IDs, dedup invariants, owner isolation, foreign-key cascades, historical readback and retention agreement. Do not rely on DynamoDB TTL semantics for SQLite.

**Live stop/recovery gate:** Restore a snapshot offline and verify Inbox, run ID/deadline, stop reservation, GET-only recovery and no double execution. A snapshot made during a remote Run is not evidence that Hermes itself was snapshotted at the same instant. Suspend new admissions and reconcile active work for a full-service restore drill.
