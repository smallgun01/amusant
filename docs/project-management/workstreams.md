# Amusant WS1–WS4 — Current workstreams (2026-10-30)

[STATUS](../../STATUS.md) owns the dated acceptance matrix; [SPEC](../../SPEC.md) owns contracts; [#132](https://github.com/smallgun01/amusant/issues/132) owns the remaining OCI/ingress/operational gates. Source at review: [main@eecd4c2](https://github.com/smallgun01/amusant/commit/eecd4c20c4e721d7dcac9f0fae96f6d2fc728d91).

| Lane | Current scope | Latest independently separated gate |
| --- | --- | --- |
| **WS1 / #47** | StickS3 physical PTT, Wi-Fi/BLE, LCD and later readback | OCI physical FAST audible PASS (10/28); 10/30 physical SLOW result audible PASS. #136 firmware source/baseline and #67 CJK/LCD physical acceptance remain OPEN. |
| **WS2 / #127, #145** | Gemini Live tool choice, Grok SLOW admission, result contract v2, TaskStore/Inbox | **M1 functional E2E PASS (Owner-observed, 10/30):** Gemini Live selected DELEGATE, real Grok Callback and one confirmed `spoken` ACK. Follow-up report and Grok-native-history continuation functionally observed. #145 remains OPEN for repeatability/telemetry, not because all real delegation fails. |
| **WS3 / #49, #132** | OCI two-service runtime, public Callback, TLS-PSK device ingress, DNS/firewall and recovery | Physical FAST and M1 SLOW functional PASS; DNS-only device hostname, host INPUT persistence, mobile-source policy, negative auth/reconnect, reboot/capacity **NOT YET ACCEPTED**. |
| **WS4 / #50, #134, #146** | Reliability, security, CI, cost, rollback and voice UX | #143 timer fix validated by one real server-side `spoken` result; native Run remains `unknown`. Residual agent/key trust, Owner Abandon, intermittent voice prefix and actual CI failures remain distinct follow-ups. |

The 10/28 [direct-Relay Callback/physical readback](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361) was **partial** (Inbox `read`); it is not the same evidence as the later 10/30 Gemini Live-originated functional PASS. One successful server `spoken` ACK does not prove physical speaker completion, and a Grok-reported completion does not prove native terminal state. Do not publish Owner email contents, personal itinerary details, tokens, raw logs or callback capabilities in public acceptance notes.

No second TaskStore, Hermes→Grok chaining, automatic resend of uncertain Webhooks, new service or automatic fallback. [PLAN](../../PLAN.md) provides the minimal network/UX closeout order.
