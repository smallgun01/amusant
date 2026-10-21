# Project Amusant — Current M1 execution plan

> **Reviewed 2026-10-21 against `main@6de2f08`.** Read with [SPEC v0.5](SPEC.md), [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md), [STATUS](STATUS.md), [WS1–WS4](docs/project-management/workstreams.md), and [OCI host-readiness handoff](deploy/oci-a1/HOST-READINESS.md). OCI/Hermes/SQLite is the **accepted implementation target**, not an accepted live deployment. The archived AWS/OpenAB plan below is non-authoritative.

## Active plan and dependencies

| Order | WS | Work | Exit / next gate |
| --- | --- | --- | --- |
| 1 (**E1 complete, 2026-10-19**) | WS2+WS3 | [#85](https://github.com/smallgun01/amusant/issues/85) B1 [PR #87](https://github.com/smallgun01/amusant/pull/87) and B2 [PR #90](https://github.com/smallgun01/amusant/pull/90) merged | Native ARM64 fake-provider Relay→Facade→Hermes→Artifact→SQLite/Inbox, routing/auth negatives, restart/ambiguous POST and STOP **handling**: [native ARM64 E2E #39240814687](https://github.com/smallgun01/amusant/actions/runs/39240814687) PASS; **no OCI/paid-provider proof** |
| 2 (**active H2 inventory; no deployment approval**) | WS3+WS4 | [#62](https://github.com/smallgun01/amusant/issues/62) OCI A1 host readiness: owner-reported N100/OpenClaw SSH access **works**, but Docker/Compose, volume design, secrets, encrypted off-host backup/restore and canary preflight remain to verify. [#27](https://github.com/smallgun01/amusant/issues/27) G1 ingress design runs separately | [H2 handoff](deploy/oci-a1/HOST-READINESS.md) describes the **proposed read-only host inventory**; ask the Owner to initiate even that step, then separately approve any install; any install, sudo grant, volume/network/security change, provider use or running service requires an explicit scope/approval |
| 3 (**requires separate Owner-approved E3 window**) | WS3 | Bounded OCI live canary with fake or separately consented real provider; representative Hermes tool load concurrent with FAST | SQLite/Facade/Hermes durable readback, encrypted off-host restore, CPU/RSS/FAST p50/p95, secrets isolation, cost controls and rollback; an E1 CI PASS or SSH login is not this gate |
| 4 | WS2+WS3 | [#46](https://github.com/smallgun01/amusant/issues/46) S1 cloud software E2E | Gemini FAST → admit → Hermes result → Inbox → **tool-disabled** spoken retrieval; restart/uncertain/stop evidence, actual cost |
| 5 | WS1+WS3 | G2 device cloud FAST then G3a physical Slow with #27 ingress | Known Wi-Fi StickS3 authenticated connection, physical PTT, then later owner-initiated retrieval of SLOW result |
| Parallel WS1 | WS1 | [#67](https://github.com/smallgun01/amusant/issues/67) CJK/OpenCC physical regression and H0 audible latency/battery; [#92](https://github.com/smallgun01/amusant/pull/92) W2b-1 merged offline, [#95](https://github.com/smallgun01/amusant/pull/95) BLE teardown and [#97](https://github.com/smallgun01/amusant/pull/97) QR are **open Drafts** | #91 real Android/BLE teardown failure not closed; QR UI and teardown need independent real-device tests/approval, neither blocks the separate M1 G2 gate |

**Product constraints:** one owner/Relay; one outstanding SLOW task (busy rejection), no automatic result voice, no second Agent backend, no queue/third Worker, no direct Relay→Hermes Runs route after Facade integration. [#56](https://github.com/smallgun01/amusant/issues/56) result contract and [#58](https://github.com/smallgun01/amusant/issues/58) timing policy must be resolved to accept live Slow behavior. Completion cues, memory/reminders, W2a, unfinished W2b hardware acceptance and wider Kai parity stay outside M1 critical-path acceptance unless explicitly approved.

**Evidence:** E1 offline/merged ≠ E2 physical ≠ E3 bounded cloud ≠ E4 sustained. #70/#83 and [PR #87](https://github.com/smallgun01/amusant/pull/87)/[PR #90](https://github.com/smallgun01/amusant/pull/90) are E1; B2 23/23 offline contract tests, [native ARM64 E2E #39240814687](https://github.com/smallgun01/amusant/actions/runs/39240814687) and [regular CI #39240814701](https://github.com/smallgun01/amusant/actions/runs/39240814701) passed. Its observed STOP terminal state was `failed / interrupt`, correctly surfaced as `hermes_failed`, **not** confirmed cancellation or provider billing cessation. Prior AWS T4b #26 is a bounded historical AWS proof. Owner separately authorizes OCI/AWS changes, public ingress, paid calls, firmware flashing and data migration; this plan grants none.

**Engineering WIP discipline:** at most one major build per WS1–WS3 plus a bounded WS4 remediation; record blocked-by and acceptance links on the corresponding issue; no new extra hosting/Agent feature branch before G3a. [#94](https://github.com/smallgun01/amusant/pull/94) merged **native ARM64 fake-provider toolset preflight** (Case A/B PASS, 2026-10-20) but does not close H1.2 typed-input/v2, permit real tools or replace #56/#58 and separate E3 consent.

---

# Archived plan snapshot — 2026-10-16/18 (non-authoritative)

All following references to B0 undecided, PR #45 Draft, AWS Service A as the target, PR-C/D hard blocking OCI, W1 unimplemented or eight-item SLOW queues are **superseded historical statements**, not an alternate active plan.

---

## Current target decision overlay — 2026-10-18

- **Target, not deployment:** [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md) selects OCI A1 co-hosted Relay/Hermes with persistent SQLite Task Ledger/Inbox; [#62](https://github.com/smallgun01/amusant/issues/62) and [OCI PR #70](https://github.com/smallgun01/amusant/pull/70) own offline packaging. DynamoDB/AWS RunTask evidence remains valid **historical** proof only; never delete the resources automatically. No OCI service has passed live acceptance.
- **WS3/WS4 next:** review ARM64 image/run compatibility, backup/restore drill, conservative SQLite retention, stop/restart GET-only and Hermes Home continuity; then owner-approved OCI canary after edge ingress/security (#27) is resolved. **WS2 parallel:** Hermes-side small-result boundary #73, not an extra Relay summarizer.
- Older dated AWS/ECS/OpenAB/B0/queue assumptions below are historical planning notes superseded for the new target; do not rerun old AWS gates solely for OCI or claim production cutover.

# Project Amusant — Execution Plan

> 更新：2026-10-16（進度對齊；無新增 live 測試）
> GitHub：Private repository; remote is configured locally.
> 本機目錄：projects/amusant


## Active execution model — WS1–WS4 (2026-10-16)

**Current authoritative delivery/decision sequence:** [four-workstream execution index](docs/project-management/workstreams.md). The older P0–P4/H0/H1 phase table and cross-track gate table below are historical/planning baselines, not a mandate to redo completed gates; in any sequencing conflict, use the WS1–WS4 index pending a SPEC/ADR-approved architecture decision. The four workstream indexes are [WS1 #47](https://github.com/smallgun01/amusant/issues/47), [WS2/B0 #48](https://github.com/smallgun01/amusant/issues/48), [WS3 #49](https://github.com/smallgun01/amusant/issues/49), and [WS4 #50](https://github.com/smallgun01/amusant/issues/50).

| Execution lane | Immediate work | Blocking scope |
|---|---|---|
| WS1 Edge Device & Fast UX | #42 Jellii persona/Zephyr/Search real test; #31 H0 latency/power; #36 WiFi-W1→W1.5 BLE→W2a→W2b | W1/W1.5 limited PoC Owner GO (2026-10-17); H0 UX remains open; **not** blocked by Agent selection |
| WS2 Backend Agent | #44/#45 Hermes-vs-OpenAB capability/hosting bake-off; B0 Slow contract and choice | **B0 blocks final selected Agent production deployment and S1 live**, not local FAST/G1 offline |
| WS3 Placement/E2E | #27 G1 offline edge transport, #35 A2 design, #46 S1; then G2 (known Wi-Fi) → G3a physical Slow → G3b mobility | A2 live requires WS4 PR-C/D and B0; G2 requires A2+G1; **G2 does not require Wi-Fi phone provisioning** |
| WS4 Reliability/Operations | #30 PR-C (>100 history) and PR-D (bounded shutdown/persistent service packaging), least privilege/secrets/cost | Hard gates for A2 live; not blockers for local WS1 functional work |

**Acceptance language:** #31 real local StickS3 FAST 10/10 is **functional PASS**, but formal audible-onset p95/H0 UX remains pending; #33 six animations and local PTT regression are owner-confirmed PASS; #26 T4b is bounded **functional PASS**, not persistent/production acceptance; #45 Hermes Docker fake-provider POC passed locally, not selected or production-proven. New [S1 #46](https://github.com/smallgun01/amusant/issues/46) verifies cloud software Fast→Slow→Inbox→tool-disabled spoken result **before** physical G3a. Distinguish program **WS1** from firmware **WiFi-W1**.

**Architecture and authorization:** Accepted SPEC v0.4 / ADR-0001 currently remains OpenAB/two ECS; switching backend or moving it to VM/N100 needs owner-approved ADR/SPEC update first. No AWS, paid Gemini/Agent calls, flashing or long-lived deployment is authorized by this planning change.

## 文件權責

- SPEC.md：唯一需求與驗收規格來源。
- PLAN.md：執行順序、里程碑與估時，不重新定義需求。
- STATUS.md：經證據確認的目前狀態。
- SPEC-P0-DELTA.md：P0 歷史決策紀錄，非規格來源。

## 專案邊界

- Amusant 是獨立維護的單一 monorepo，尊重 zbruceli/kai 的架構、kai-sim 協定與後續硬體韌體上游。
- 第一階段只做語音輸入與語音／文字回覆；沒有位置、感測器或實體控制。
- 快語音 provider 由使用者手動選擇；Gemini Live 是首個 baseline。第二個真實 Live provider 是 optional validation，不是 MVP gate。
- **已接受的 P3/P4 基線**：OpenAB ACP + 受限 Agent，見 [ADR-0001](docs/adr/0001-openab-acp-slow-path.md)。#44 / Draft PR #45 Hermes Runs 尚屬本地 fake-provider PoC；WS2/B0 未選型，未授權 cutover／VM/N100 遷移。
- 軟體可行性與 StickS3 **本地 Fast functional gate 已通過**；H0 正式延遲／電池／相較手機的 UX Go/No-Go 仍待完成，並不阻擋獨立 WS2/WS3 離線工作。

## P0 / H0 命名

- **P0 — Foundation**：repo、上游、文件權責與範圍定義；已完成。
- **H0 — Hardware UX Gate**：軟體驗收後，決定專用硬體是否值得繼續；取代原規格中會與 P0 衝突的硬體「Phase 0」名稱。

## Upstream 與 repo

- Kai baseline：v0.12.0，commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f；LICENSE 為 MIT，重用程式碼需保留授權與著作權聲明。
- Amusant repo：private monorepo；初始文件與 P0 reconciliation 已提交至 main。Kai firmware 不在本 repo 中；本計畫不修改上游 Kai。
- **歷史 P1**：Kai kai-sim 同 LAN 對接私有 mock probe；後續已有 #31/#33 **真實 StickS3 → 本地 Amusant Relay → Gemini FAST PASS**，不要將 P1 測試配置視為目前唯一系統組態。

## 舊 P0–P4/H0/H1 里程碑（歷史紀錄，非當前排程）

初版的「4–7 工程週」是初期概估，**不是 2026-10-16 的剩餘工期**。現行執行順序由 [WS1–WS4 index](docs/project-management/workstreams.md) 和本文件的 Active execution model 管理。

| 歷史階段 | 已驗證（時間與範圍） | 仍未通過的 Gate |
|---|---|---|
| P0/P1/P2 | P0/P1 PASS；2026-10-09 P2 真實 Kai-sim → Gemini → 可聽語音 PASS | 雲端入口與生產 SLA 不在這些 Gate 範圍 |
| P3.0–P3.4 | Admission、in-process Dispatcher、ACP、Inbox 已 merge；PR #40 的 tool-disabled Inbox speech safety 及 #41 Dispatcher wakeup 已 **離線 CI PASS 且 merge** | 新 Gemini Session 真正 `check_task_status` 並朗讀的 live E2E（#46 S1） |
| P4 | P4-A1、DynamoDB durability、2026-10-13 T4b WSS/ACP one-offs **功能 PASS**（#26） | #35 持續 ECS Relay、restart、安全與成本；完整端到端未驗 |
| H0/G0 實體本地 | 2026-10-15 #31 真實 StickS3 → Amusant Relay FAST **10/10 PASS**；PTT、barge-in、wake/reconnect 觀察 PASS；#33 Jellii 六狀態+PTT audio regression PASS | H0 可聽第一聲分布、電池與正式 UX Go/No-Go；12 筆僅 Relay first-wire，nearest-rank p95 **2265ms** |
| H1 Firmware | 已有 Kai v0.12.0 pinned reference 與 Jellii UI patch | WiFi-W1 NVS、W1.5 BLE PoC、W2a multi-SSID、W2b **BLE 或 SoftAP**，均尚未實作／須獨立核准 flashing |

## P4 雲端歷史實證與目前下一步（2026-10-16）

- **#26 T4b 已完成 bounded functional acceptance**：一次 Fargate Relay RunTask → 私有 TLS WSS → Jellii/NGINX/OpenAB → DynamoDB after-STOPPED 強一致讀回 PASS，回滾已記錄；程序例外及成本未獨立核驗，見 [T4b acceptance](docs/p4-t4b-live-acceptance.md)。不因 PM 文件同步而重跑相同付費 Prompt。
- **WS2 B0（#44/#48）**：Hermes PR #45 fake-provider Docker POC 成功；真正 Agent 能力、Run retention/host persistence、ECS/VM/N100 placement 未驗證；OpenAB SPEC/ADR 仍為已接受基線。
- **WS4 PR-C/D → WS3 A2（#30/#35）**：先修 DynamoDB >100 history、bounded Dispatcher stop、Python 3.13 real persistent entrypoint/health/non-root；A2.0/A2.1 可離線先做，live 要 B0＋PR-C/D＋獨立授權。**不可把 `relay.wss_preflight_smoke` 當常駐 CMD**。
- **S1 #46**：軟體 client 先完成 Fast→Slow→durable Inbox→新 Live **tool-disabled spoken result**，再與裝置網路路徑合流。
- **G1 #27 → G2 → G3a → G3b**：先認證/隔離外部 device ingress；G2 使用單一已知 Wi-Fi 測實體 FAST；S1+G2 後實體 Slow；WiFi 配網與 Mobility 留給 G3b。
- **WS1 #42/#36**：Jellii Persona/Zephyr/Google Search 仍為 Draft/offline CI，需 real-device 測試；配網 W1→W1.5 BLE→W2a→W2b 需分開驗證。

## 歷史 P1 連線方案（當時測試已通過）

- Kai-sim client 與 mock probe 使用同一個可信 LAN；測試時再確認主機位址與作業系統。
- Mock 僅綁明確指定的私有 LAN 位址；不新增防火牆規則、不做 port-forward、不公開 Gateway。
- P1 不使用跨網路 tunnel；若未來需要外網存取，另行評估核准的私有連線或雲端架構。

## 歷史 P1 exit criteria（原始測試 Gate）

- LAN 主機上的 Kai simulator 使用 sounddevice 找到麥克風／喇叭；實際 Kai-sim client 的音訊與連線結果有可保存證據。
- PTT start/end 與 Kai WebSocket wire protocol 對接成功。
- private host mock probe 回傳文字卡片、字幕、tone/echo PCM；不含外部模型、OpenClaw action、AWS、hardware 或 provider credentials。以實際 kai-sim 跨主機執行結果作為 P1 runtime 證據；synthetic test 不代替此 gate。
- Kai 協定 source-level 比對以 pinned commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f 為基準；不因協定子集測通就宣稱完整相容。
- 測試不保存原始音訊；錯誤／斷線可觀察且不洩漏憑證。

## 安全與治理 gates

- P3 不接 N100 OpenClaw。OpenAB `/acp` 必須具備已編入的 `acp` feature、bearer 認證、單一受限 agent、最小工具權限、獨立模型憑證；ACP cancel 不保證真正停止執行，P3 單次上限 120 秒。
- Local SQLite 和 in-process Dispatcher 已實作；**DynamoDB 在 2026-10-12 durability 與 10-13 T4b 之 bounded live readback PASS**，不再只是候選。Persistent ECS A 與完整兩 ECS 生產服務仍未部署驗收；目前不需 SQS/第三個 Worker ECS。
- 任何 AWS 費用、硬體購買、私有主機設定變更或 Kai 上游改動，另行核准後才執行。
- 目前此排程是專案里程碑，非 cron/提醒。

## 現行跨軌 Gate Matrix（2026-10-16）

執行權威為 [WS1–WS4 index](docs/project-management/workstreams.md)，技術測試隔離見 [cross-track roadmap](docs/hardware/cross-track-integration-roadmap.md)。

| Gate | 截至 10/16 狀態 | 進入條件／下一步 |
|---|---|---|
| H0/G0 #31 | **本地實機 FAST 功能 PASS**；正式 UX 門檻仍 OPEN | 補可聽第一聲 p50/p95、續航和 Owner UX Go/No-Go；#33 六狀態+PTT PASS |
| B0 #44 / PR #45 | **OPEN**：Hermes 本地 fake-provider PoC PASS，未完成選型 | 真實 Agent Task Quality、長任務/上下文、工具權限、成本、部署 ECS/VM/N100；變更需 SPEC/ADR |
| WS4 PR-C/D #30 | **OPEN**；#40/#41 已 merge | >100 retained TASK rows per #30 A1; optional 500-row stress characterization; bounded shutdown、non-smoke Python 3.13 entrypoint；**A2 live hard gates** |
| A2 #35 | **持續 ECS A 未部署** | A2.0 read-only/A2.1 offline 可先做；live 須 B0 + PR-C/D + Owner 雲端授權 |
| G1 #27 | **外部 Device ingress 未證明** | 標準 cert-WSS/Cloudflare 或 TLS-PSK/TCP，先 fake client/auth/TLS/rollback |
| S1 #46 | **雲端軟體 FAST→SLOW→Inbox spoken 未驗** | 選型＋A2 後在核准的 live 環境執行；與實體 G2 分開 |
| G2 #27/#35 | **實體 S3→AWS FAST 未驗** | A2+G1，**單一已知 Wi-Fi** 即可（不依賴 WiFi-W2b） |
| G3a | **實體 S3→Agent→Inbox spoken 未驗** | S1 + G2 |
| WiFi-W1→W1.5→W2a→W2b #36 | **均未實作** | W1 已獲 limited implementation GO（#31 G0 functional PASS）；H0 UX 不阻擋 W1，Flash 仍須逐次核准；W1.5 安全 BLE 官方 App PoC 後，W2b **BLE 或 SoftAP** 二擇一 |
| G3b | **多網路場景未驗** | G3a + 已驗證 provisioning 與 Wi-Fi switching |

**隔離規則：** 一個 Gate 只變動一層；WiFi 開發固定經 G0 驗證的 local Relay；S1 不需實體裝置；G2 不需手機配網。裝置 Edge TLS 與 Relay→Agent private ACP WSS 是獨立 trust boundaries。

**獨立核准：** 此次文件調整不授權 Firmware 匯入/Flash、付費 Gemini/Agent、Cloudflare/AWS 修改、公開入口、24/7 或 Agent cutover。PR #34 offline CI 已 merge，但不能替代實機／雲端 live Gate。
