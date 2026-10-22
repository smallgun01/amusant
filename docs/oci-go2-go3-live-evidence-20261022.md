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

**Next read-only check:** inspect only the existing Hermes Run's sanitized status metadata and persisted session tool call **names/counts** (never prompt, report text, tool args or API key). Avoid expecting the live SSE event queue to provide historical tool events after completion. Do not re-submit the Run, edit the OCI runtime, or release the uncertain owner slot until an operator reconciles confirmed terminal state. Issue #105 optional-artifact v2 is a separate, potential long-term result-contract improvement.

## Runtime configuration / follow-ups

- Owner readback: `AMUSANT_PERSONA_PROFILE=UNSET`, `AMUSANT_GEMINI_GOOGLE_SEARCH=UNSET`, `AMUSANT_SLOW_EXECUTOR=hermes_runs`. Persona absence may affect delegation consistency, but **did not prevent this observed delegation**. No profile change applied.
- Single-flight deliberately retains the uncertain owner slot until remote termination is independently verified. Do **not** clear SQLite, blindly re-submit the task, or claim confirmed cancellation.
- Next: **read-only Facade ledger diagnosis** (`hermes_run_status`, `artifact_status`, `writer_call_count`, `writer_result_code`, `raw_output_bytes`, `presentation_error`) to distinguish absent writer invocation from failed artifact commit; confirm the persisted remote terminal evidence; then, if safely established, use reviewed `relay.operator_reconcile` with `terminal_state=completed` (releases slot as *failed / no result*, does not invent a result). Diagnose poll counter/deadline; choose approved timing policy; re-test bounded physical Slow Task and later explicit PTT Inbox speech.
- Keep `#62` open for OCI acceptance, `#46` open for spoken Inbox, `#58` open for timeout/STOP reliability, and `#27` open for durable public device ingress. `#105` Option D v2 remains a **separate proposal**, not required to explain this failure.
