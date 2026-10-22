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
| GO 3 physical SLOW completion / spoken Inbox | **NO-GO** | Run deadline reached; remote stop accepted but terminal state unconfirmed; no bounded result saved, no Inbox spoken readback. |
| E3/E4, public G1 | OPEN | Off-host encrypted restore, load/FAST-under-SLOW, real STOP/cost, reconnection and long-lived public edge ingress remain unproven. |

## Sanitized FAST timings

Relay logs (UTC, 2026-10-22) for four outgoing-audio turns show PTT-release → first outgoing audio **797, 1914, 1045, 3150 ms**. These are server-side wire events, **not** measured speaker onset or statistically valid p95. Two brief presses (214 ms and 178 ms) did not cross the activation threshold. No raw audio persisted.

## SLOW failure evidence

One owner-observed voice delegation created a durable task at **2026-10-22T14:22:23Z**, kind `summarize`, and recorded a Hermes Run ID. Its persisted deadline was **14:24:23Z** (120 seconds). Stop was requested at deadline with `stop_reason=run_deadline`, `stop_attempts=1`, and `stop_accepted_at` populated. Final `status=uncertain`, dispatch `uncertain`, `last_error=hermes_stop_unconfirmed`, `poll_attempts=662`, and **no result**. The accepted STOP response is **not** evidence that the remote run terminated; operator reconciliation must wait for independent terminal proof.

Owner-read OCI timing config: Run deadline **120s**, Hermes GET poll interval **2s**, poll cycle **10s**, HTTP timeout **5s**, stop grace **20s**, max stop attempts **3**. `poll_attempts=662` is anomalous relative to a simple 2-second cadence; it is a persisted counter, **not proof of 662 paid model calls or even 662 HTTP GETs**. Investigate scheduler/clock/counter before changing policy.

The first read-only Run status query failed **before issuing GET** because a standalone `docker exec` process lacked the in-process `AMUSANT_FACADE_API_KEY` env var. A corrected file-secret-based GET was supplied but **no successful response is yet recorded**. Do not infer `completed`, `cancelled`, or `running`.

## Runtime configuration / follow-ups

- Owner readback: `AMUSANT_PERSONA_PROFILE=UNSET`, `AMUSANT_GEMINI_GOOGLE_SEARCH=UNSET`, `AMUSANT_SLOW_EXECUTOR=hermes_runs`. Persona absence may affect delegation consistency, but **did not prevent this observed delegation**. No profile change applied.
- Single-flight deliberately retains the uncertain owner slot until remote termination is independently verified. Do **not** clear SQLite, blindly re-submit the task, or claim confirmed cancellation.
- Next: read-only remote Run status and Facade/Hermes state; if independently terminal, use reviewed `relay.operator_reconcile` with recorded evidence; diagnose poll counter and deadline; choose approved timeout policy; re-test a short, bounded physical Slow Task and explicit later PTT status/speech.
- Keep `#62` open for OCI acceptance, `#46` open for spoken Inbox, `#58` open for timeout/STOP reliability, and `#27` open for durable public device ingress. `#105` Option D v2 remains a **separate proposal**, not required to explain this failure.
