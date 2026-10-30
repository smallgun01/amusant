# Amusant — Current source, OCI runtime and acceptance

Source HEAD at 2026-10-30 review: [main@96ae859](https://github.com/smallgun01/amusant/commit/96ae8594d11f25f11349420a6979be9387a99630). [#131](https://github.com/smallgun01/amusant/pull/131) Grok adapter, [#133](https://github.com/smallgun01/amusant/pull/133) image release and [#138](https://github.com/smallgun01/amusant/pull/138) optional-import isolation are merged. New main is **not** the installed e615c06 image. Hermes/ACP/DynamoDB compatibility is retained but not part of Grok Compose.

## OCI current evidence — dated gates, not operational acceptance

| Evidence level | Last verified observation | Remaining boundary |
| --- | --- | --- |
| **Merged / Offline PASS** | [#143](https://github.com/smallgun01/amusant/pull/143) Spoken ACK timer, [#144](https://github.com/smallgun01/amusant/pull/144) zh-TW voice, [#147](https://github.com/smallgun01/amusant/pull/147) delegation guidance, [#149](https://github.com/smallgun01/amusant/pull/149) fail-closed Grok diagnostics, [#150](https://github.com/smallgun01/amusant/pull/150) runtime documentation, [#151](https://github.com/smallgun01/amusant/pull/151) FAST/SLOW routing tests merged. | Tests/PR merge do not establish the installed OCI build or natural-language model compliance. |
| **Deployed / Cloud infrastructure** | [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695): Relay + Proxy 2/2 Healthy, SQLite v10, old containers removed without volumes. Last image revision explicitly reported: `e615c06`. | Live SHA/digests on 10/30 unverified; reboot recovery, persistent firewall policy and sustained capacity not accepted. |
| **Physical FAST PASS** | [G4 10/28 OCI FAST physical PASS](https://github.com/smallgun01/amusant/issues/132#issuecomment-6465592941): real StickS3 PTT received audible Gemini reply via OCI target. | Distinct handshake telemetry, repeated reconnection and long-term acceptance remain open. |
| **Cloud Callback + Physical readback (partial PASS)** | [G4 10/28 real callback and physical speech](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361): direct Relay admission → genuine Grok Routine → named-tunnel Callback → SQLite completed/Inbox pending → subsequent physical PTT displayed and audibly read saved result. | **Not Gemini-originated admission**; actual `spoken` ACK **FAIL** (`read` remained). Hardware audio heard is not a server-side ACK. |
| **Operational Accepted** | **NOT ACCEPTED** for full M1. | Need Live-originated valid delegation, post-#143 server `spoken` retest, negative auth/recovery and Owner acceptance. |

Do not conflate an accepted Webhook with Agent completion, `reported_outcome` with native terminal state, or a published source SHA with OCI deployment. Keep the reviewed effective two-file Compose / secret handling; older single-file recipes are not safe restart instructions.

## Product gates

M1 requires StickS3 PTT → **Gemini Live-selected** `delegate_task` → Relay → Grok Routine → authenticated Callback → SQLite Inbox → explicit later PTT speech → server-side `spoken` transition. The direct-Relay test proves the middle and audible readback, but its `spoken` transition failed; other Live-originated trials either made no delegation or entered uncertain admission after invalid configuration. [#145](https://github.com/smallgun01/amusant/issues/145) remains OPEN. #151 clarifies instructions and synthetic evals, not deterministic routing or real device acceptance. The `spoken` state itself is server-side transmission completion, never proof of owner hearing or native Grok termination.

## Work boundaries

[#132](https://github.com/smallgun01/amusant/issues/132) owns remaining G4 physical/operational acceptance; [#135](https://github.com/smallgun01/amusant/issues/135) is CLOSED after docs/H2; [#136](https://github.com/smallgun01/amusant/issues/136) and [#67](https://github.com/smallgun01/amusant/issues/67) retain firmware/LCD physical gates; [#145](https://github.com/smallgun01/amusant/issues/145) owns Live tool choice and [#146](https://github.com/smallgun01/amusant/issues/146) owns agent egress/key compromise boundaries. [#139](https://github.com/smallgun01/amusant/pull/139) template work is separate from installed config. No automatic fallback, no blind resend, no provider/host action authorized by this file. [SPEC](SPEC.md) · [PLAN](PLAN.md) · [WS Index](docs/project-management/workstreams.md).
