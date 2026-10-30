# Amusant WS1–WS4 — Current workstreams (2026-10-30 evidence snapshot)

[STATUS](../../STATUS.md) owns evidence, [SPEC](../../SPEC.md) owns contracts and [#132](https://github.com/smallgun01/amusant/issues/132) owns OCI/ingress gates.

| Lane | Current scope | Latest independent gate |
| --- | --- | --- |
| **WS1 / #47** | StickS3 physical, BLE/Wi-Fi, FAST, LCD and later PTT readback | [G4 10/28 OCI FAST physical PASS](https://github.com/smallgun01/amusant/issues/132#issuecomment-6465592941): OCI audible FAST PASS. [G4 10/28 real callback and physical speech](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361): saved result physically spoken; complete `spoken` ACK FAILED. [#136](https://github.com/smallgun01/amusant/issues/136) consolidated firmware source merged in #142 but remaining production/physical gate OPEN; [#67](https://github.com/smallgun01/amusant/issues/67) LCD/CJK OPEN. |
| **WS2 / #127, #145** | Grok SLOW admission, result contract v2, TaskStore/Inbox, Live tool choice | Direct-Relay Routine + authenticated Callback + Inbox PASS; #151 synthetic routing guidance merged; Live-originated full chain NOT ACCEPTED. Historical Hermes WS2 index [#48](https://github.com/smallgun01/amusant/issues/48) CLOSED as superseded. |
| **WS3 / #49, #132** | OCI runtime, public Callback, TLS-PSK device ingress and E2E | Grok 2/2 Healthy, real public Callback and actual physical FAST/partial SLOW PASS. Reboot/firewall persistence, post-patch ACK and complete device E2E NOT ACCEPTED. |
| **WS4 / #50, #134, #146** | Security, reliability, CI, cost, rollback | #149 fail-closed diagnostics merged, #143 timer fix merged but actual deployment/retest unverified. Agent outbound data exfiltration and durable recovery remain open; avoid multiplying services. |


Today's reviewed source HEAD: [main@96ae859](https://github.com/smallgun01/amusant/commit/96ae8594d11f25f11349420a6979be9387a99630). Installed OCI image was last explicitly documented at e615c06; no later deployment digest was independently inspected. [G4 10/28 real callback and physical speech](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361) supersedes the earlier 10/27 statement that public Callback and physical saved-result readback had never been tested: both occurred, **but full M1 and server-side `spoken` acceptance remain open**. [STATUS](../../STATUS.md) is the single dated acceptance matrix.

Do not create a second TaskStore, run Hermes and Grok as an automatic chain, resend ambiguous Webhooks, relocate legacy modules merely for tidiness, or treat H1/H2 docs/CI as device acceptance. [PLAN](../../PLAN.md) gives the minimal next sequence.
