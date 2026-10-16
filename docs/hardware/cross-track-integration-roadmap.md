# Amusant — Cross-track Hardware / AWS Integration Roadmap

> Active technical integration view refreshed 2026-10-16, subordinate to [WS1–WS4 delivery index](../project-management/workstreams.md), [PLAN](../../PLAN.md), [STATUS](../../STATUS.md), and accepted [SPEC](../../SPEC.md). #31 G0 device FAST functional PASS and #33 avatar physical PASS are **not** the same as formal H0 latency/battery UX acceptance. No Cloud/flash/live authorization is implied.

## Verified baseline vs outstanding gates

| Component | Evidence | Missing acceptance |
|---|---|---|
| **G0/#31** real StickS3 FAST | **2026-10-15 owner-reported 10/10 PASS**: local Amusant Relay + actual Gemini audio, wake, barge-in, reconnect; relay first-wire sample count 12 (p50 1026.5ms, nearest-rank p95 2265ms) | **H0 UX remains OPEN**: physical speaker audible onset and battery/power regression #14 |
| **Avatar #33** | Six Jellii UI states and PTT spoken regression PASS, patch #38 merged | Long battery/sleep wake verification via #14 |
| **Backend OpenAB #26** | One-off AWS RunTask → private TLS WSS/ACP → Jellii/NGINX, after-stop DynamoDB strong readback functional PASS with procedural exceptions, rollback | Persistent ECS Relay A (#35); not 24/7 nor device ingress |
| **Backend Hermes #44/#45** | Local Docker Native Runs fake-provider POC Phase 2A/2B PASS | WS2 **B0** real Agent quality + host/storage/security/cost decision, not selected |
| **Device ingress #27** | Offline read-only research; native Kai TLS-PSK incompatible with ordinary Cloudflare HTTPS WSS termination | G1 route, TLS auth, ownership, cost, isolated canary |
| **Firmware #36** | Stage plan recorded; existing Kai baseline pinned | **WiFi-W1** NVS single profile → **W1.5** official Android BLE PoC → **W2a** up to 5 profiles → **W2b** select secure BLE or SoftAP provisioning |
| **Operations #30** | PR #34 CI, #40 Inbox safety, #41 Dispatcher wake merged | PR-C >100 history + PR-D bounded shutdown/persistent entrypoint/ops **before A2 live** |

## Active dependency graph

```text
WS1 Local device FAST G0 (functional PASS) ---+
                                                +---- G2: StickS3 -> deployed Relay -> Gemini FAST
WS3 G1 device ingress --------------------------+
WS2 B0 backend selection ------+                |
WS4 PR-C / PR-D ---------------+--> WS3 A2 ECS Relay persistent (offline then separately approved live)
                                |               |
                                +--------------> S1 #46: cloud software Fast->Agent->durable Inbox->spoken
G2 + S1 --------------------------------------------------> G3a: physical Slow Inbox
WS1 WiFi-W1 -> W1.5 BLE -> W2a -> W2b (BLE OR SoftAP) ---+
G3a ------------------------------------------------------+--> G3b mobility
```

**Important:** G2 needs **A2 + G1 + one known Wi-Fi**, not W1/W1.5/W2a/W2b phone setup. S1 needs B0/Agent and A2, but **no device**. G3a requires S1+G2, G3b requires G3a+accepted Wi-Fi provisioning. WS1 firmware/FAST and WS3 G1 offline can move while B0 is open.

## Gate acceptance and test isolation

| Gate | Execution boundary | Required proof / owner approval |
|---|---|---|
| **H0/G0** #31 | Frozen known local firmware + Amusant Relay | **functional PASS** 10/10; formal real-speaker latency p50/p95 and battery Go/No-Go pending |
| **B0** #44/#48 | Slow product contract, Agent + hosting | Real agentic bake-off, storage/run recovery/permissions, ECS/VM/N100 cost; reviewed SPEC/ADR if switching |
| **PR-C / PR-D** #30 | Durable history and service lifecycle | >100/500 completed history, bounded safe shutdown/restart, Python 3.13, health/non-root; **A2 live blockers** |
| **A2** #35 | From one-off RunTask to persistent Relay Service A | Live canary after B0+PR-C/D, ECS healthy/task replacement, DynamoDB recovery, owner cloud approval |
| **G1** #27 | Device→Relay edge only, fake client then approved canary | Standard cert WSS or native TCP TLS-PSK choice, auth/reject, no plaintext fallback, SG/routes verified; don't alter shared Gateway Tunnel |
| **S1** #46 | Cloud software-only Voice→Slow→Inbox→voice | New authorized Gemini tool-disabled spoken turn, durable lookup, truthful delivery/recovery; no physical StickS3 needed |
| **G2** #27/#35 | StickS3→AWS Relay→Gemini FAST | Known Wi-Fi, real TLS/auth/Opus/reconnect/speaker response, logs and latency with separate live approval |
| **G3a** | StickS3 FAST+SLOW → durable Inbox spoken | S1+G2 prerequisite; separate physical UX/faults |
| **WiFi-W1** #36 | Owned Firmware branch; NVS, USB setup | No baked SSID/password, credential protection, stable local PTT after reboot/deep-sleep and approved flash |
| **WiFi-W1.5** #36 | BLE feasibility PoC, official Android ESP provisioning app | PoP, BLE pairing, Wi-Fi credentials secure, UX/performance/power fit; outcome decides W2b mechanism |
| **WiFi-W2a** #36 | Multi-SSID profiles (up to ~5) | Bound failover, profile-specific cached BSSID/channel, no wake/latency regression |
| **WiFi-W2b** #36 | **Select one** phone setup solution | BLE official/custom app OR time-limited authenticated SoftAP+Captive Portal; no need to implement both |
| **G3b** | Wi-Fi mobility + proven G3a | Home/hotspot switching, recovery, cost/battery/latency, Fast+Slow regression |

**Control variables:** At each gate pin the firmware binary+source SHA, Relay SHA/image digest, TLS mode, Wi-Fi profile scenario, backend revision, and test authorization. Change one main layer at a time, run offline CI after software PRs, and record numerical evidence rather than mark unsupported PASS.

## Device edge decision, not backend ACP

```text
StickS3 --[TBD: cert-verified WSS via Cloudflare OR TCP passthrough/native TLS-PSK]--> ECS A Relay
ECS A   --[existing private TLS/ACP NGINX sidecar, separate credentials]-----> ECS B Jellii
```

#27's 2026-10-15 read-only audit makes reuse of the existing Cloudflare Connector **conditional**, not accepted: it is not a standalone live tunnel ECS Service and its routes/policy could not be verified. Native TLS 1.2 ECDHE-PSK cannot terminate at Cloudflare's ordinary HTTPS/WSS edge. Do not silently change Kai PSK to certificate-verified TLS without testing identity/verification and client adapter compatibility.

## Ownership and next actions — current

- **WS1** #31 G0 is functionally PASS; finish H0 audible-latency/battery/UX evidence (#14), Stage D PR #42 live validation, and only approved firmware WiFi-W1→W1.5→W2a→W2b #36.
- **WS2** #44/#48: time-box Agent selection B0; PR #45 is an isolated PoC, not production authorization.
- **WS3** #27 G1 offline edge selection and #35 A2 design in parallel. After WS4 PR-C/D and owner B0, request separate live A2+S1 and G1+G2 approvals. Open narrow G3a, then G3b execution tickets when prerequisites pass.
- **WS4** #30 PR-C/D are hard A2 live gates; monitor data/security/cost and rollback evidence. #26 remains the historical T4b bounded proof.
- **Out of scope without separate approval:** changes to AWS, shared tunnel/public ingress, firmware flash, live paid Gemini/Agent, 24/7 service, eFuse, or accepted SPEC/ADR architecture.
