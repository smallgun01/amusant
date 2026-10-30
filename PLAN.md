# Amusant — Current execution plan (2026-10-30)

**Milestone:** Owner-observed **M1 physical SLOW functional E2E PASS**, including one new task with a confirmed SQLite `spoken` ACK and audible StickS3 readback. See [STATUS](STATUS.md) for the dated evidence matrix. This closes the previous *functional* Live-originated delegation/Spoken ACK blockers, **not** the operational acceptance gates under [#132](https://github.com/smallgun01/amusant/issues/132).

## Minimal next sequence (no live authorization implied)

1. **DNS routing side task:** replace the firmware's numeric OCI destination with an Owner-managed device hostname using **DNS-only** (no Cloudflare HTTP proxy) to the OCI public IPv4. Native TLS-PSK must remain opaque end-to-end over TCP/443 to Relay; do not confuse this with the independent Cloudflare Named Tunnel for Grok Callback. Prefer a stable/reserved destination IP if available. Confirm actual firmware build/config mechanism before modifying or flashing.
2. **Source-IP and host firewall policy:** DNS only resolves the **destination**; it does not solve changing client public IPs. The current OCI NSG and Ubuntu host INPUT `/32` canary were separately configured, and the host rule was temporary. Keep a narrow canary until the chosen mobile/dynamic-source policy, negative-auth and connection/resource protections are reviewed. No automatic public `0.0.0.0/0` opening.
3. **Operational acceptance:** validate DNS resolution, TCP443, real TLS-PSK/WSS + Bearer, wrong-credential rejection, Wi-Fi/hotspot reconnect and sleep/wake; then verify host rule persistence, socket-proxyd/systemd reconciliation, reboot recovery and bounded capacity. Keep the tested OCI release and rollback image available.
4. **Small UX polish, after network stability:** improve the repetitive `Grok 回報` voice prefix without hiding provenance, distinguish a brief STATUS answer from an explicit read-full-result request, and collect bounded Live tool-choice evidence under #145. Do not add an agent summarizer, second TaskStore or new runtime service.
5. **Separate maintenance:** check GitHub Actions failures against actual job logs, then address only demonstrated CI defects. Continue #136 firmware and #67 LCD/CJK physical gates; #134/#146 security/reliability remain tracked independently.

## Product and evidence boundaries

The functional path is StickS3 PTT → Relay → **Gemini Live chooses** FAST or DELEGATE → Grok Routine → authenticated Callback → SQLite v10 Inbox → later explicit PTT → Gemini Live spoken readback. Amusant-admitted STATUS and Grok-native historical continuation are distinct. Additional Owner-observed cases include an amended report, a read-only Gmail summary and continuation of older Grok-native work; these do not establish independently verified native file provenance, per-task Spoken ACK or Grok Run terminal state.

Grok-first is the selected SLOW backend; Hermes/ACP/DynamoDB remain explicit legacy compatibility paths, not an automatic chain or fallback. A source merge is not proof of an installed OCI image; the Owner's effective two-file Compose is authoritative. No document/PR grants OCI, DNS, Cloudflare, NSG, firewall, secret, provider, firmware or data-reset authority.

[STATUS](STATUS.md) · [SPEC](SPEC.md) · [WS Index](docs/project-management/workstreams.md) · [G1 ingress decision](deploy/oci-a1/G1-INGRESS-DECISION.md)
