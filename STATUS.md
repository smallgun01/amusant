# Amusant — Current source, OCI runtime and acceptance

**Evidence date: 2026-10-30 (Asia/Taipei).** Source HEAD at review: [main@eecd4c2](https://github.com/smallgun01/amusant/commit/eecd4c20c4e721d7dcac9f0fae96f6d2fc728d91). The Owner reports the post-#151 OCI Relay image deployed with Relay + Egress Proxy healthy and SQLite v10. Installed configuration, firmware image and host rules are Owner-controlled; a source merge alone is not a deployment.

## Dated acceptance matrix

| Gate | Evidence and result | Boundary |
| --- | --- | --- |
| Source / offline | [#143](https://github.com/smallgun01/amusant/pull/143) Spoken ACK timeout, [#144](https://github.com/smallgun01/amusant/pull/144) zh-TW readback, [#149](https://github.com/smallgun01/amusant/pull/149) fail-closed diagnostics, [#150](https://github.com/smallgun01/amusant/pull/150) runtime docs and [#151](https://github.com/smallgun01/amusant/pull/151) FAST/SLOW policy are merged. | Offline tests and merged source are not independent OCI or physical evidence. |
| OCI runtime | [#132 second cutover](https://github.com/smallgun01/amusant/issues/132#issuecomment-6444738695): two-service Grok profile and SQLite v10. Owner later reported deployment of the post-#151 Relay release. | Reboot recovery, persistent firewall rules, complete negative-auth and sustained capacity are not accepted. |
| Physical FAST | [2026-10-28 real StickS3 PTT](https://github.com/smallgun01/amusant/issues/132#issuecomment-6465592941) returned audible Gemini speech over the OCI-targeted TLS-PSK/WSS device path. | Independent handshake telemetry and cross-network reconnect remain separate gates. |
| Earlier SLOW baseline | [2026-10-28 direct Relay admission](https://github.com/smallgun01/amusant/issues/132#issuecomment-6473721361) → real Grok Callback → later audible PTT readback PASS; Inbox stayed `read` (Spoken ACK FAIL). | Not Gemini-originated; superseded by the functional test below, but preserved as historical evidence. |
| **M1 physical SLOW functional E2E — PASS (2026-10-30)** | Owner observed real StickS3 PTT → Gemini Live-chosen `delegate_task` → Relay → Grok Routine → HTTPS Callback → SQLite Inbox → later explicit PTT Gemini speech. For one new task, read-only SQLite returned `status=completed`, `reported_outcome=completed`, callback present, saved speech present, `delivery_state=spoken`. Owner heard the reply. | One verified server-side `spoken` transition is not a physical speaker ACK or proof of native Grok Run terminal state. Operational M1 remains OPEN. |
| Additional Agent scenarios — functional PASS | Owner observed a follow-up amendment to an Amusant-originated report, a read-only Gmail-summary request, and an amendment to an older Grok-native itinerary through Jellii; each returned a Grok Callback and an audible short readback on request. | Native file/history provenance, mail permission scope and per-task `spoken` were not independently audited. No private message contents or task identifiers are reproduced here. |
| Operational acceptance | **NOT YET ACCEPTED** under [#132](https://github.com/smallgun01/amusant/issues/132). | Device DNS-only hostname remains pending; firmware still targets numeric OCI IP; temporary host INPUT `/32` canary is not reboot-persistent. NSG and host firewall are distinct. Negative auth, mobility, recovery and capacity gates remain. |

## Current product boundary

Gemini Live selects FAST, DELEGATE, STATUS or CLARIFY from user intent. FAST answers directly. DELEGATE admits one durable task via Relay and the selected Grok Routine; authenticated Callback writes the existing SQLite v10 TaskStore/Inbox. STATUS reads **Amusant-admitted** task history; Grok-native historical work requires delegation rather than an Inbox lookup. Every spoken result returns via Gemini Live, only after a later Owner PTT. No unsolicited device speech or automatic Webhook retry.

Callback `accepted` and `reported_outcome=completed` are channel/report facts. Grok `native_state` remains `unknown` without independent terminal evidence. A server `spoken` transition confirms completed server-side audio transmission, not actual hardware playout. The first successful physical M1 task now provides both a server `spoken` observation and Owner-heard audio.

## Remaining focused work

- **#132 WS3 ingress/operations:** finish device DNS-only routing, choose a deliberate dynamic-source firewall policy, make required host rules survive reboot, test reconnect, negative PSK/Bearer and recovery. Do not broaden TCP/443 exposure merely to make DNS work.
- **#145 routing reliability:** real successful Live-selected delegation supersedes the previous all-FAST blocker; keep the issue open for repeatability and redacted tool-choice evidence, not a router rewrite.
- **#136 / #67 WS1:** production firmware baseline and LCD/CJK acceptance remain separate.
- **#134 / #146 WS4:** callback trust, key exposure and failure/Owner Abandon remain separate from functional M1; no fabricated native STOP.
- **Voice UX:** short readback sometimes starts with `Grok 回報` (server-side prefix) and sometimes is naturally paraphrased by Live; status and read-full-result intent can be refined without changing architecture.
- **CI:** diagnose any failing workflows from GitHub Actions evidence; do not infer a quota cause solely from an Agent's email summary.

[README](README.md) · [SPEC](SPEC.md) · [PLAN](PLAN.md) · [WS Index](docs/project-management/workstreams.md) · [#132](https://github.com/smallgun01/amusant/issues/132)
