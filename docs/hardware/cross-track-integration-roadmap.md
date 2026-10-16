# Amusant — Cross-track Hardware / AWS Integration Roadmap

> **Superseded for active sequencing by the [WS1–WS4 project index](../project-management/workstreams.md) (2026-10-16).** Preserve this 2026-10-15 roadmap as a historical technical planning baseline; it predates #31 physical functional PASS, #33 Jellii physical PASS, #36 WiFi-W1.5 BLE decision, and #46 S1/G3a/G3b split. Use new index + PLAN for current gates. This is not a change to accepted SPEC/ADR or an execution permit.


> Planning decision: 2026-10-15. **Sequence and acceptance map, not an execution permit.**
> [SPEC.md](../../SPEC.md) governs requirements; [PLAN.md](../../PLAN.md) governs sequencing; [STATUS.md](../../STATUS.md) records demonstrated evidence only. Nothing here changes the accepted two-ECS architecture.

## Baseline versus target

| Workstream | Verified / merged baseline | Still unverified |
|---|---|---|
| Device baseline | Owner H0.2: physical StickS3 with pinned Kai v0.12.0 → local Kai Relay → Gemini Live, battery-only ~8 short turns. Latency informal. | H0 product UX Go/No-Go |
| Amusant device edge | #31 Stage B / PR #32 merged: opt-in Opus + TLS-PSK adapter, fake-provider tests; PR #34 offline CI merged | **G0** physical StickS3 → local Amusant Relay, audio/wake/reconnect/latency |
| AWS backend | #26 P4-T4b one-off RunTask → private ACP WSS → Jellii + durable DynamoDB readback; reverted | **A2** persistent ECS Relay Service A, restart, monitoring, cost, production hardening |
| Public device ingress | #27 read-only audit: shared cloudflared is Gateway sidecar; actual service stopped; Cloudflare control-plane read 403; native Kai TLS-PSK is incompatible with Cloudflare HTTPS WSS termination | **G1** safe standard-WSS client ingress or alternative TCP TLS-PSK transport; Cloudflare routing/identity not proven |
| Firmware ownership/config | #11 firmware research, unmodified upstream Kai on tested StickS3 | **W1/W2** NVS dynamic profiles + SoftAP; any fork/flash separately authorized |

## Work graph (two parallel tracks, then join)

```text
G0  #31  Physical StickS3 -> local Amusant Relay (PINNED KAI FIRMWARE)
 |
 +-- Firmware (new H1 branch; local Relay stays frozen) --+
 |   W1   #36  Dynamic SSID/password in NVS via USB      |
 |   W2a  #36  Up to 5 Wi-Fi profiles / fallback          |
 |   W2b  #36  SoftAP + captive-portal phone setup        |
 |                                                        +--> G3 final multi-network product E2E
 +-- AWS/backend/edge (do not modify tested firmware) ----+
     A2   #35  Persistent ECS Service A, private ACP     |
     #27       Choose device edge (TLS-PSK TCP or standard WSS)
     G1   #27  Offline fake-client WSS, then isolated/cloud canary
          |
          +--> G2   Real StickS3 -> AWS Relay (known single Wi-Fi)
                         |
                         +-------------------------------> G3
```

G0 should be finished before an H1 *firmware flashing* decision. A2 read-only planning and G1 offline transport research can run in parallel with G0. **G2 does not require W1, W2a or W2b.** If the chosen edge is standard WSS, a *separate transport firmware change* is required before G2; do not combine it with provisioning changes.

## Gates and test isolation

| Gate | Only moving piece | Acceptance evidence | Owner/approval boundaries |
|---|---|---|---|
| **G0**, #31 Stage C | Replace local Kai Relay with local Amusant, same pinned firmware, private LAN credentials/port | Real battery-only PTT ≥10 turns, audible completion, interruption, wake/reconnect, measured release→first-audio p50/p95; rollback to Kai | Owner-supervised physical/live Gemini use with separate authorization; no AWS or reflashing |
| **A2**, #35 | Replace one-off smoke with persistent AWS Relay ECS Service A, keep proven private ACP backend | desired/running/healthy=1, restart/task replacement, DynamoDB durable recovery, per-service isolation, monitor/cost/rollback | Read-only and offline first; AWS creation/update/spend separately approved |
| **G1**, #27 | Device-edge *software transport only*, using fake client/provider and then isolated canary | WSS/cert/client-auth or TCP TLS-PSK validity; wrong-key/unauthorized reject; reconnect; no plaintext downgrade; cloud routing/SG/Cloud Map verified if live | Separate cloud/Tunnel approval, do not modify shared Gateway or its LINE/Kasm routes |
| **W1**, #36 | Dynamic NVS Wi-Fi on own H1 firmware branch, single profile | No baked SSID/password; USB setup survives reboot/deep sleep and reaches same local Relay | No upstream edits or flash without explicit owner approval |
| **W2a**, #36 | Network profile manager only | Multiple remembered 2.4 GHz SSIDs; bounded failover; RTC BSSID/channel cache profile-bound; local PTT wake latency regression | Same frozen local Relay and transport |
| **W2b**, #36 | SoftAP / captive portal phone setup only | Add/edit/delete via phone, AP access control, timeout/low power, failure/recovery preserving valid profile | No mobile app/cloud required; no saved-secret disclosure |
| **G2**, #27+#35 | Physical device to already-verified AWS edge; single *known* Wi-Fi | Device TLS/auth, Opus streaming, Gemini audible reply, reconnect and latency, with the fixed upstream baseline or an explicitly pinned transport-only firmware | Separate owner permission for paid Live, physical firmware flash and cloud canary |
| **G3**, final | Join proven firmware provisioning + cloud product paths | Home/hotspot switch → AWS FAST PTT/response; separate `delegate_task` → ACP/Jellii → DynamoDB Inbox → next-session retrieval/spoken result; recovery/latency/UX and cost | No automatic production/24x7 approval; record component versions and rollback |

**Control variables:** At each gate pin the firmware binary+source SHA, Relay SHA/image digest, TLS mode, Wi-Fi profile scenario, backend revision, and test authorization. Change one main layer at a time, run offline CI after software PRs, and record numerical evidence rather than mark unsupported PASS.

## Device edge decision, not backend ACP

```text
StickS3 --[TBD: cert-verified WSS via Cloudflare OR TCP passthrough/native TLS-PSK]--> ECS A Relay
ECS A   --[existing private TLS/ACP NGINX sidecar, separate credentials]-----> ECS B Jellii
```

#27's 2026-10-15 read-only audit makes reuse of the existing Cloudflare Connector **conditional**, not accepted: it is not a standalone live tunnel ECS Service and its routes/policy could not be verified. Native TLS 1.2 ECDHE-PSK cannot terminate at Cloudflare's ordinary HTTPS/WSS edge. Do not silently change Kai PSK to certificate-verified TLS without testing identity/verification and client adapter compatibility.

## Ownership and next actions

- **First:** owner/agent execute #31 G0 local preflight then supervised device test, maintaining original Kai rollback. Keep #31 open until physical results.
- **Parallel design (no AWS mutations):** #35 A2.0 and #27 routing/standard-WSS offline feasibility. Decide TCP PSK vs certificate WSS only after cost/security evidence.
- **After G0 + H0 Go:** #11 selects the H1 firmware basis; #36 W1 → W2a → W2b as separate PRs, with flash approval at each new binary.
- **Join:** #35 A2 + #27 G1 before #27 G2; W2b is not a G2 blocker. Open a narrow G3 execution ticket only when upstream gates pass.
- **Out of scope:** firmware vendor/import, app/cloud pairing, paid provider or AWS live executions, persistent service deployment, shared Tunnel edits, real device flash, eFuse/flash-encryption changes, main merges, or adding ECS/SQS infrastructure without explicit later approval.

References: [#31](https://github.com/smallgun01/amusant/issues/31), [#35](https://github.com/smallgun01/amusant/issues/35), [#36](https://github.com/smallgun01/amusant/issues/36), [#27](https://github.com/smallgun01/amusant/issues/27), [#11](https://github.com/smallgun01/amusant/issues/11), [#26](https://github.com/smallgun01/amusant/issues/26), [#14](https://github.com/smallgun01/amusant/issues/14), [#30](https://github.com/smallgun01/amusant/issues/30).
