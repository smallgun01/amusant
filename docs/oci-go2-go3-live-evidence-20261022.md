# OCI GO 2 / GO 3 — Owner-observed live evidence (2026-10-22)

> Evidence record only; **not** full E3/E4, production ingress, or authorization for more provider runs. Source: owner terminal outputs and physical listening, correlated with current Relay/Facade/Hermes implementation. No secrets, private IPs, raw prompts, transcript or audio are retained here.

## Gate verdicts

| Slice | Verdict | Evidence / limit |
| --- | --- | --- |
| GO 1 OCI static readiness | PASS (owner reported) | Compose, ARM64 pinned images, file secrets, persistent mount and static preflight; historical #62 |
| GO 2 live backend | **Core functional PASS, operational gates OPEN** | Four-service deployment healthy after Relay file-permission fix; native Codex provider smoke returned expected marker; real Relay → Facade → Hermes Run → committed artifact → bounded SQLite Inbox task completed; status later read. This was a backend-driven task, **not** physical speech delivery. |
| GO 3 private edge tunnel | PASS (temporary test) | N100 LAN-facing SSH TCP forwarding to OCI private Relay; no public device port opened; independent TCP + TLS 1.2 ECDHE-PSK + Bearer + WebSocket/PTT control smoke PASS. Temporary tunnel is not a permanent internet ingress design. |
| GO 3 physical FAST | **Functional PASS (owner-observed)** | Actual StickS3 PTT → OCI Relay → Gemini Live → device audio; owner confirms audible playback. OCI logs show provider connection, outgoing audio and completed turns. |
| GO 3 physical SLOW admission | **PASS, partial only** | Device voice caused one `summarize` Slow Task admission, durable dispatch and known Hermes Run ID. |
| GO 3 physical SLOW completion / spoken Inbox | **NO-GO** | Deadline STOP was accepted but not terminal-confirmed within Relay's grace. Later authenticated Facade GET returned HTTP 502, `status=failed`, `error.code=artifact_not_written`: under current Facade code this occurs only after the upstream Hermes Run reports `completed`, while no committed report exists. Relay remains `uncertain` without bounded result or spoken readback. |
| E3/E4, public G1 | OPEN | Off-host encrypted restore, load/FAST-under-SLOW, real STOP/cost, reconnection and long-lived public edge ingress remain unproven. |

## Sanitized FAST timings

Relay logs (UTC, 2026-10-22) for four outgoing-audio turns show PTT-release → first outgoing audio **797, 1914, 1045, 3150 ms**. These are server-side wire events, **not** measured speaker onset or statistically valid p95. Two brief presses (214 ms and 178 ms) did not cross the activation threshold. No raw audio persisted.

## SLOW failure evidence

One owner-observed voice delegation created a durable task at **2026-10-22T14:22:23Z**, kind `summarize`, and recorded a Hermes Run ID. Its persisted deadline was **14:24:23Z** (120 seconds). Stop was requested at deadline with `stop_reason=run_deadline`, `stop_attempts=1`, and `stop_accepted_at` populated. Final `status=uncertain`, dispatch `uncertain`, `last_error=hermes_stop_unconfirmed`, `poll_attempts=662`, and **no result**. The accepted STOP response was **not** terminal proof at deadline; however a **later authenticated Facade GET** (owner terminal evidence) did reach the Hermes-completed branch. This does not establish *when* the Run completed or recover a report, and Relay's own ledger still remains `uncertain`.

Owner-read OCI timing config: Run deadline **120s**, Hermes GET poll interval **2s**, poll cycle **10s**, HTTP timeout **5s**, stop grace **20s**, max stop attempts **3**. `poll_attempts=662` is anomalous relative to a simple 2-second cadence; it is a persisted counter, **not proof of 662 paid model calls or even 662 HTTP GETs**. Investigate scheduler/clock/counter before changing policy.

The first read-only Run query failed **before issuing GET** because standalone `docker exec` lacked the Relay entrypoint's in-process API key env var. The owner then executed a corrected file-secret-based authenticated GET and observed: `HTTP_STATUS=502`, Facade response `RUN_STATUS=failed`, `ERROR_CODE=artifact_not_written`, `OUTPUT_PRESENT=False`. **Important:** `status=failed` here is the Facade's fail-closed **response wrapper**, not Hermes' native status. In current `deploy/oci-a1/facade.py`, `artifact_not_written` is reachable on a successful upstream GET only after Hermes status is exactly `completed`, when the required `save_report` artifact is missing. The output is suppressed by Facade; `OUTPUT_PRESENT=False` does not prove Hermes generated no text.

## 2026-10-23 Facade ledger root-cause narrowing (owner GET + read-only SQLite)

After the authenticated GET reported `HTTP 502 / status=failed / error=artifact_not_written`, the owner performed a **read-only Facade SQLite query** for the same task:

| Facade request field | Observed |
| --- | --- |
| `submit_state` | `accepted` |
| `hermes_run_status` | `completed` (upstream native status) |
| `artifact_status` | `pending` |
| `writer_call_count` | **0** |
| `writer_result_code` / `writer_report_bytes` | NULL / NULL |
| `presentation_error` | `artifact_not_written` |
| `raw_output_bytes` | **747** |
| `terminal_seen_at` | UNIX 1792687749.0673895 (timestamp of Facade seeing terminal via GET, **not proven actual Run completion time**) |

**Interpretation:** Hermes returned a terminal-completed Run and had 747 bytes of native output, but **no report write was recorded by Facade**. This favors a missing or pre-commit `save_report` integration step, *not* a recorded artifact storage failure. Zero writer calls means no **accounted report commit attempt** reached the appropriate Facade ledger path: it cannot alone prove that the model never requested the tool, since plugin argument/session validation or HTTP authentication could reject before that counter increments.

**Source inspection:** `deploy/oci-a1/hermes_plugin/__init__.py` registers `save_report` in `amusant_option_d`, and `deploy/oci-a1/hermes-config.fragment.yaml` requires enabling plugin `amusant-option-d` plus `platform_toolsets.api_server: [amusant_option_d]`. The current `relay/hermes_result.py` instructions specify bounded JSON but **do not explicitly tell Hermes to call `save_report` before its final answer**. Candidate causes (unconfirmed): plugin or toolset not effective at runtime; missing/weak instruction to invoke `save_report`; invalid trusted session or writer request rejected before accounting. Compare the earlier completed backend task before assuming plugin unavailable globally.

**2026-10-23 owner read-only Hermes config/path check:** `CONFIG_EXISTS=True`, `PLUGIN_EXISTS=True`, `PLUGIN_ENABLED=True`, `TOOLSET_ENABLED=True`. The mounted `/opt/data/config.yaml` contains the required Option D declarations and plugin file exists at `/opt/data/plugins/amusant-option-d/__init__.py`. These facts rule out **missing declarations/mount** but **do not prove live registry/tool exposure for that completed Run or that the model invoked `save_report`**.

**2026-10-23 runtime toolset GET:** Owner queried authenticated Hermes `GET /v1/toolsets`; returned HTTP 200 and `TOOLSET_FOUND=True`, `ENABLED=True`, `CONFIGURED=True`, `SAVE_REPORT_REGISTERED=True`. Hence *current* API-server effective toolset discovery includes the registered `save_report` tool. This does **not** establish the completed Run's actual invocation history, plugin handler success or its prior in-flight tool choices. The still-missing Facade writer record must be traced at the Run/session layer; the v1 prompt currently requests output JSON without explicitly directing a `save_report` call.

**2026-10-23 completed Run/session history (owner GET-only):** Authenticated native Hermes `GET /v1/runs/{run_id}` returned **HTTP 200, `status=completed`, `completed=true`**, with a valid `session_id`. Authenticated `GET /api/sessions/{session_id}/messages?limit=500&order=latest` returned **HTTP 200, two messages**, `tool_calls={}`, `tool_responses={}`, and **`SAVE_REPORT_CALLS=0`**. Since the persisted session contains only two messages, there is no pagination-truncation signal for this run. Together with `writer_call_count=0`, this is strong evidence **Hermes completed without invoking any recorded tools**, including `save_report`. Do not equate this with Hermes failing to register `save_report`: live tool registry GET had already returned PASS. Whether a per-run restriction or provider/tool-choice behavior suppressed tool availability remains unproven; inspect if needed before asserting exact cause.

**Engineering conclusion / targeted fix proposal (not implemented):** The Option D **v1 contract** requires a committed report Artifact to release a bounded result, while `relay/hermes_result.py` instructs only final compact JSON and never explicitly asks for a `save_report` tool call. First fix should be a small, audited **instruction/contract and regression-test PR** that requires the report tool call before the short final JSON, checks realistic Hermes model/tool behavior and fails closed if omitted. A prompt alone is probabilistic, not a durable guarantee, so add an observability/test gate and keep missing-artifact classification truthful. Alternative `#105` **v2 universal bounded-result contract** permits Artifact-free ordinary queries; plan it separately with rollout/rollback, not as retroactive reinterpretation of this v1 Run. Deadline 120s, STOP, 662 poll counter remain independent issues.

**Safe slot recovery:** Independent native Hermes GET and Facade GET both confirm terminal `completed`; `relay.operator_reconcile` (after operator review) supports `--terminal-state completed` + verified exact Run ID and evidence reference, yielding `failed / hermes_operator_reconciled_completed_no_result` and releasing the uncertain single-flight slot. This **does not** save, fabricate, or announce a successful result. No remote Run resubmit; no direct SQLite edits. Reconcile only by owner-executed operator action with audit evidence.

## 2026-10-23 owner manual reconciliation — PASS

The Owner executed the reviewed `relay.operator_reconcile` against the exact persisted Task ID and independently verified Hermes Run ID, after a read-only precheck of `slow_tasks.status=uncertain`, matching `backend_run_id`, and `dispatch_outbox.state=uncertain`. Sanitized terminal receipt:

```text
PRECHECK: PASS
{"status":"failed","task_id":"14425650-7a16-42ee-a258-48bfe9348198","code":"hermes_operator_reconciled_completed_no_result"}
```

**Verdict:** The old Relay task has been durably closed **failed/no result** and the uncertain single-flight reservation released; no new task was admitted in this reconciliation and physical Slow→Inbox→spoken remains **NO-GO**. This is operator-assisted recovery, **not** proof that #58 automatic terminal recovery or #107 result-contract v2 is implemented/deployed.

## Runtime configuration / follow-ups

- Owner readback: `AMUSANT_PERSONA_PROFILE=UNSET`, `AMUSANT_GEMINI_GOOGLE_SEARCH=UNSET`, `AMUSANT_SLOW_EXECUTOR=hermes_runs`. Persona absence may affect delegation consistency, but **did not prevent this observed delegation**. No profile change applied.
- Single-flight was correctly held while remote termination was unknown and was **released by Owner's verified operator reconciliation on 2026-10-23**. Do not clear SQLite or blindly re-submit the old task; no confirmed cancellation or recovered result is claimed.
- Next: owner-reviewed `relay.operator_reconcile` with confirmed native `completed` and matching Run ID to release blocked single-flight as **failed/no result**; then investigate omitted `save_report` behavior and fix the v1 task instructions plus regression tests or separately roll out #105 v2. Diagnose 662 poll counter/120s deadline independently; rerun a bounded authorized physical Slow task and test explicit PTT Inbox spoken retrieval.
- Keep `#62` open for OCI acceptance, `#46` open for spoken Inbox, `#58` open for timeout/STOP reliability, and `#27` open for durable public device ingress. `#105` Option D v2 remains a **separate proposal**, not required to explain this failure.
