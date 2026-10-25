# C2 Draft plan — generic Agent delegation

This Draft is based on Owner-merged #118 (`73397323412e9c27c6caacc5adbbd1a2b5dd9962`) and the accepted SPEC v0.6 / ADR-0004 boundary. It adds no per-tool routing, permission policy, classifier, or provider call.

## Proposed representation and files

- Expose `delegate_task(task=...)` as the sole new-call shape. Hermes task-only admissions always use one neutral internal kind, `agent`, regardless of a legacy `AMUSANT_DEFAULT_TASK_KIND` value. Keep `research`, `compare`, and `summarize` as compatibility metadata: an old Live schema may explicitly send one on a first admission or a replay, but it never selects tools, capabilities, credentials, or authorization. Retain the configured task-only default for the legacy ACP path.
- Update `relay/slow_path.py`, `relay/companion_persona.py`, `relay/application.py`, and `relay/hermes_result.py` for generic task wording/defaults and truthful research/action summaries. Keep the Result Contract v2 fields and limits unchanged.
- Update `relay/task_store.py` to SQLite schema v9. Rebuild only `slow_tasks` so its `task_kind` CHECK accepts `agent`; copy every existing column and leave `dispatch_outbox`, `task_results`, and `task_inbox` rows and references intact. Do not rewrite historical task kinds or `payload_sha256` values.
- Keep the existing fingerprint serializer and old-kind digests unchanged. New generic requests get the `agent` digest; when an existing call ID is found, allow the matching legacy-kind digest for the same task/backend/result-contract pin without rewriting the stored digest. Different task text, backend, or result-contract pin still conflicts.
- DynamoDB has no `task_kind` enum/schema constraint, so no table migration is planned. Extend `DynamoDbTaskStore` validation and duplicate matching with the same fingerprint compatibility rule; existing item attributes remain untouched.

## Regression plan

- Migrate a temporary schema-v8 SQLite fixture; assert historical rows, fingerprints, known Run/stop state, results, Inbox, and foreign-key links are unchanged. Reopen it and exercise generic admission plus duplicate replay.
- Exercise a Hermes task-only call under an old configured default, an explicit stale-schema legacy hint on first admission, exact legacy replay and task-only replay; retain conflict behavior for changed payloads and v1/ACP pins in SQLite and the in-memory DynamoDB ledger.
- Exercise one `delegate_task` path with fake research, action-success, and denied/missing-tool receipts, all with optional Artifact absent. Assert v2 bounds, honest outcome text, no tool/permission selector, and no dedicated frontend branch.

## Rollback/release constraint

An older reader cannot safely update an `agent` row. Before rolling back to a reader without this kind, stop new admissions and drain/reconcile active generic tasks; SQLite has no automatic row expiry, so retain an `agent`-capable reader unless a separately reviewed, state-preserving migration/archive handles every generic row. Preserve task rows, known Run bindings, Artifact and Inbox results, and payload digests. No production database will be opened or migrated in this PR.
