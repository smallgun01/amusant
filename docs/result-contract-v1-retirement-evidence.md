# Sanitized evidence for retired Hermes Result Contract v1 test tasks

This note preserves the Owner-supplied historical disposition for the two known OCI v1 test records. These facts were provided for the retirement decision and were not independently re-read from OCI during this PR. The records remain historical and are not migrated, deleted, or reinterpreted by the v2 runtime.

| Task identifier | Recorded disposition | Preserved meaning |
| --- | --- | --- |
| `ec4113dc` | Completed; spoken; Artifact committed | Functional acceptance evidence for the former v1 test task. It is not evidence that the v2 runtime is deployed. |
| `67bcf44a` | Failed after remote completion was confirmed; no Artifact | Failure evidence. It must not be recast as a successful v2 result or as a writer failure unless the archived source data supports that conclusion. |

Before retiring any historical data at OCI, preserve an operator-controlled encrypted backup of the Relay SQLite database, Facade SQLite database and artifacts, and Hermes Home/Run state. Preserve sanitized copies of any additional E2E evidence separately from raw logs, complete reports, credentials, Run IDs, and Artifact references. The source rows remain pinned to their recorded contract version; this repository note does not replace a backup or modify the source record.

See [Result Contract v2 cutover and rollback](result-contract-v2-cutover.md) for the read-only precheck and cutover procedure.
