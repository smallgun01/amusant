# Amusant — Evidence and Status (documentation aligned 2026-10-25)

> **2026-10-25 documentation alignment (source/acceptance boundaries):** [#106](https://github.com/smallgun01/amusant/pull/106) records OCI GO 2 backend and GO 3 physical FAST functional evidence, not full E3/E4 acceptance. [#107](https://github.com/smallgun01/amusant/pull/107) merged optional-Artifact Result Contract v2 source, but v1 remains the deployment default until separately enabled and verified. [#109](https://github.com/smallgun01/amusant/pull/109) proves G1 ingress **offline only**. [#112](https://github.com/smallgun01/amusant/pull/112), [#113](https://github.com/smallgun01/amusant/pull/113), and [#114](https://github.com/smallgun01/amusant/pull/114) merged fixes for Gemini Live result injection, spoken-session handoff and substantive `speak` guidance, but **post-fix physical new-PTT audio / Inbox `spoken` acceptance is not yet evidenced**. Keep [#46](https://github.com/smallgun01/amusant/issues/46), [#58](https://github.com/smallgun01/amusant/issues/58), [#62](https://github.com/smallgun01/amusant/issues/62) and [#27](https://github.com/smallgun01/amusant/issues/27) open at their actual gates. Source merged ≠ deployed ≠ physical/cloud accepted; no live authorization follows from this note.

> **2026-10-22 OCI live evidence supersedes earlier GO 1-only snapshot below:** GO 2 real backend Relay→Facade→Hermes→Artifact→bounded Inbox functional PASS; GO 3 physical StickS3→private SSH tunnel→OCI Relay→Gemini Live→audible device speech functional PASS. A physical Slow Task was accepted and assigned a Hermes Run ID but hit 120s `run_deadline`; STOP accepted without prompt terminal confirmation, initially leaving `uncertain / hermes_stop_unconfirmed`; later native Hermes GET confirmed `completed`, with zero recorded tools and missing Artifact. On 2026-10-23 Owner operator reconciliation precheck PASS and returned `failed / hermes_operator_reconciled_completed_no_result`, clearing the uncertain slot without inventing a result. `poll_attempts=662` still needs diagnosis. No physical Slow→Inbox→spoken PASS or formal E3/E4. See [dated evidence](docs/oci-go2-go3-live-evidence-20261022.md); older GO 2/3 NOT STARTED text below is historical.

> **Earlier snapshot baseline:** `main@73436c6` and latest issue summaries [#36](https://github.com/smallgun01/amusant/issues/36), [#47](https://github.com/smallgun01/amusant/issues/47), [#62](https://github.com/smallgun01/amusant/issues/62). Later owner-observed hardware/OCI readiness evidence supersedes old PR/CI-only descriptions. No deployment authority is implied.

## Current M1 snapshot (verified repository artifacts vs pending external gates)

| Area | Confirmed source or CI evidence | Still NOT proven |
| --- | --- | --- |
| **WS1 physical FAST** | Owner local FAST 10/10; **#95 W2b-1 and #97 W2b-QR merged, owner E2 PASS** per #36/#47: official Android QR 5/5, credential success, END/DEINIT/setup return without fallback, local Relay two-way voice | **W2a multi-SSID Pending**, #36 OPEN; #67 full CJK/OpenCC, formal H0 UX/heap/negative cases and #27 remote ingress separate |
| **WS2 Hermes** | #45/#60/#65 Native Runs durability/single-flight, #75/#80 Option D PoC, [#87](https://github.com/smallgun01/amusant/pull/87)/[#90](https://github.com/smallgun01/amusant/pull/90) integrated **offline E1 GO**; [#94](https://github.com/smallgun01/amusant/pull/94) H1.2 native ARM64 **effective-toolset Case A/B fake-provider preflight PASS** ([CI](https://github.com/smallgun01/amusant/actions/runs/39426021105)) | Real model/provider/tool quality and permissions, #56 complete typed-input/v2/section-read and artifact lifecycle, #58 approved timing, actual STOP/cost behavior; #94 cannot stand in for a live real-tool security gate |
| **WS3 OCI** | #87/#90 integrated synthetic E1 GO; **#62 owner GO 2 real backend functional PASS / GO 3 tunneled physical FAST PASS**: Docker/Compose, mounted persistent data reboot check, pinned ARM64 images, secrets/operator config, real static preflight PASS | Full physical Slow→new PTT spoken result NOT ACCEPTED; #27 permanent ingress and formal E3/E4 NOT ACCEPTED. No OCI E3; off-host restore, load, STOP, cost/security, #27 pending |
| **WS4 durability/ops** | #78 SQLite online backup/integrity/read-only retention audit; #70 bounded Relay shutdown; #89 general CI pinned Option D regression; #90 path-filtered E1 ARM64; [#94](https://github.com/smallgun01/amusant/pull/94) separate path-filtered ARM64 offline effective-toolset CI | No scheduled encrypted off-host backup, whole-ledger/Facade/Hermes restore, real STOP/billing metrics, unattended monitoring, actual 2-CPU host load or E4 acceptance |
| **Historical AWS** | [#26](https://github.com/smallgun01/amusant/issues/26) 2026-10-13 one-off WSS/ACP + DynamoDB strong after-stop readback: bounded **functional PASS with procedural exceptions** | No accepted always-on AWS ECS A/OCI production system or automatic data cutover |

**Cancellation caveat:** [PR #90](https://github.com/smallgun01/amusant/pull/90) explicitly records `stop_terminal_outcome=interrupt_failed` and `stop_cancel_confirmed=false`; this is an E1 PASS for truthful STOP handling, **not** proof of an active Hermes Run cancellation, tool/work settlement or provider-cost cessation. The E1 restart fixture recreates Relay runtime/Dispatcher on the same SQLite volume, not a full OCI VM/host reboot.

**Evidence taxonomy:** E1 = source merged and offline/fake tests; E2 = owner-observed real local/physical; E3 = bounded real cloud/provider integration with approvals and record; E4 = sustained operations, restore, security and cost acceptance. Passing CI cannot substitute E2–E4.

**Most important next acceptance:** #46/#62 physical Slow→Inbox→new PTT spoken result, including audible output and accurate `spoken` state after approved deployment/testing. Earlier GO 2 backend and GO 3 tunneled physical FAST evidence does not establish completed G3a or formal E3/E4. W2b-1 and QR physical PASS; **W2a Pending**. Formal E3/off-host restore/STOP/cost/load and #27 public ingress still open. No live operation authorized by these docs.

---

# Historical status snapshots — 2026-10-16/18 (non-authoritative)

The prior STATUS log follows **unchanged** for audit provenance. Mentions of #70 open, #45 Draft, ARM64 unverified, B0 undecided, W1.5 unimplemented, or OpenAB/two-ECS as current baseline are dated history, superseded by the snapshot above.

---

## Current architectural decision and evidence scope — 2026-10-18

**Owner-selected implementation target:** [ADR-0003](docs/adr/0003-oci-a1-hermes-sqlite.md): Oracle A1 single-host Relay + Hermes Native Runs, persistent SQLite Task Ledger and independent Hermes Home. PRs #45/#60/#65 are merged offline Hermes foundations; [PR #70](https://github.com/smallgun01/amusant/pull/70) is an **open OCI offline packaging candidate** (CI reported PASS on its referenced SHA), not a completed cloud deployment. The DynamoDB 100-history limitation was an application query issue, not an AWS hard cap (#30); #65 improved 100+ retained-history admission, while #30 comprehensive regression remains open. See #56/#73 for unresolved Hermes-origin small-result/artifact boundary.

**Not evidenced:** OCI ARM64 Hermes image selection, real-provider tool/cost behavior, device ingress/real StickS3-to-OCI, OCI Fast p50/p95 under Slow load, automatic encrypted off-host backups and restore, host-loss recovery or sustained operation. Previous AWS T4b DynamoDB after-stop readback remains E3 bounded **historical AWS proof only**, not OCI/Hermes production acceptance. No cloud mutations, paid calls, merge or state migration are implied by these docs. Older dated tables below record state **as of 2026-10-16**, not the current decision.

# Project Amusant — Status

> 最後更新：2026-10-16（追補既有證據，未新做 Live 測試）
> 規格權威：SPEC.md
> 執行計畫：PLAN.md
> Repo：Private repository; remote is configured locally.


## Workstream status snapshot — 2026-10-16 (documentation reconciliation)

**Index:** [WS1–WS4](docs/project-management/workstreams.md) · [WS1 #47](https://github.com/smallgun01/amusant/issues/47) · [WS2 #48](https://github.com/smallgun01/amusant/issues/48) · [WS3 #49](https://github.com/smallgun01/amusant/issues/49) · [WS4 #50](https://github.com/smallgun01/amusant/issues/50). The dated records below are preserved as historical evidence; this reconciliation **did not perform new live tests**.

| Workstream | Verified evidence | Pending / not claimed |
|---|---|---|
| WS1 Edge/Fast | #31 2026-10-15 **owner-observed physical StickS3 → local Amusant Relay → Gemini FAST 10/10 functional PASS**, wake/barge-in/reconnect observed; 12 Relay-side first-wire-audio latency observations, median 1026.5 ms and nearest-rank p95 2265 ms. #33 Jellii six LCD states, build/flash and audible PTT regression **owner-reported PASS**; #38 merged. | Audible-onset UX timing (wire time is not speaker time), larger latency sample/battery verification; #42 Stage D Jellii/Zephyr/Search **Draft CI PASS but not Live accepted**; WiFi-W1/W1.5 BLE/W2a/W2b unimplemented. |
| WS2 Backend | OpenAB ACP on AWS bounded #26 T4b functional PASS. Hermes Native Runs #44/#45 **local fake-provider Phase 2A/2B POC and CI PASS**, including idempotency, known Run-ID recovery and fail-closed ambiguous submit. | B0 selection/real Agent quality, secure hosting/durable Hermes home, live run model/cost; accepted OpenAB/two-ECS architecture **unchanged**. |
| WS3 E2E | #26 real private backend WSS/ACP + DynamoDB after-STOPPED readback PASS with recorded procedural exceptions. | Persistent Service A #35 **not deployed**, public device ingress #27 **not proven**, software cloud S1 [#46](https://github.com/smallgun01/amusant/issues/46) not run, G2/G3a/G3b not run. Cloudflare shared Gateway-sidecar reuse conditional, not accepted. |
| WS4 Robustness | Offline CI #34 merged; security/Inbox PR #40 and Dispatcher wake PR #41 merged with offline CI PASS. | #30 DynamoDB >100 history PR-C and bounded shutdown/persistent runtime PR-D pending. 24/7 operational, agent sandbox/secret lifecycle, real cloud spend verification **not accepted**. |

**Evidence scope:** E1 = merged/offline; E2 = real local/physical; E3 = bounded live cloud; E4 = sustained operational/security/cost acceptance. A local fake POC or one-off AWS smoke must not be reported as E4. #26 T4b does not need a duplicate identical live prompt merely for bookkeeping. #33 avatar-specific tasks are complete in owner evidence; any power regression remains under #14.

## 歷史 P0–P4 進度與實測證據（各日期之當時狀態，非現行待辦清單）

**P0 — Foundation：完成。P1 — Kai-sim mock 音訊 smoke：PASS。P2 — Fast Voice：PASS（2026-10-09 runtime gate）。P3.0 — authenticated OpenAB ACP/model + one-off Worker feasibility smoke：PASS only under explicitly accepted, task-bounded risk; not production acceptance. P3.1–P3.4 squash-merged. P4-A1 live one-off PASS（2026-10-12）. ADR-0002 accepted. Store-ready Relay squash-merged PR #19 (`69bd7ed6cadaba68176f8fa706c5d8ca880f7376`). Durability envelope squash-merged PR #20 (`ff5868a3bc4e3453e419f97e0ea06f3f5a3b5a9c`). P4 durability live one-off PASS（2026-10-12）：write via Dispatcher/ACP, A STOPPED, then `store_get` still reads completed Inbox. Table kept. Not TLS, not 24/7, not device ingress。

## P4-T4b — Live Private WSS/ACP 功能驗收（2026-10-13）

**Functional Acceptance: PASS；Procedural Conformance: EXCEPTIONS RECORDED。** 根據 [Live record](https://github.com/smallgun01/amusant/issues/26#issuecomment-6148650538)、[after-STOPPED addendum](https://github.com/smallgun01/amusant/issues/26#issuecomment-6149983722) 與 [final review](https://github.com/smallgun01/amusant/issues/26#issuecomment-6150189393)、[Owner monitoring-risk acknowledgement](https://github.com/smallgun01/amusant/issues/26#issuecomment-6150655257)。這是既有 AWS 測試證據整理，不是此次文件更新重新操作 AWS。詳見 [acceptance report](docs/p4-t4b-live-acceptance.md)。

- **Runtime:** Jellii Service 暫時 Revision 10（OpenAB + NGINX），單一 Relay Fargate 1.4.0 RunTask，實際 Jellii ENI/專用 SG TCP 8443 和 Cloud Map current IP 檢查 PASS。
- **Preflight／ACP：** fresh client TASK# partition + DNS/TLS/SAN/Bearer/Path/8080 共 8/8 assertions PASS；一次 ACP prompt 返回完成，Relay STOPPED exit 0、Inbox `status=completed`／`has_result=true`／`delivery_state=pending`。沒有重跑 Task 或 Prompt。
- **Stop 後 Reader：** 由既有 `amusant-store-reader` role 針對同一 task_id 執行 strongly consistent DynamoDB GetItem，得到 exit 0/completed/has_result=true/pending；pending 指 Inbox 尚未被領取，不是失敗。
- **Rollback：** Jellii Revision 9、desired/running/pending `0/0/0`、原 SG／空 service registry，Cloud Map ListInstances 0、無執行中 Relay；Window 2/2 尚未使用、臨時資源保留。
- **程序例外：** 獨立 watchdog/backup 未啟動；Owner 於 Telegram 2026-10-13 20:01 GMT+8 明確接受 manual/no-backup，實際連續 console attendance 未獨立留證。`deploy/t4b_security_group_gate.py` 未直接執行（controller equivalent actual gate PASS）；exact-task StopTask fallback 僅 offline 測試，live 未觸發；Cloud Map OperationId/TTL wait 未記錄。AWS/provider 實際 spend 未獨立量測。
- **仍待驗證：** Persistent Relay ECS Service A、Device/Voice E2E、24/7 恢復與監控、持續 TLS secret lifecycle、完整 Production Acceptance。現有 `oabctl-admin` 仍是 Owner 採用的部署操作者；ECS Runtime Roles 另外以最小權限設定，不新建部署 IAM 管理平台。

## 已確認決策

- Amusant 為獨立維護的 Kai-compatible monorepo；Kai 保持架構與韌體上游。
- 產品範圍只含語音溝通、語音／文字回覆；不含位置、感測器或實體控制。
- 快語音 provider 手動選擇；Gemini Live 為首個 baseline；第二真實 provider 為 optional validation，不是 MVP gate。
- **Architecture history:** PR #5 accepted earlier ADR-0001; PR #9 adopted exactly two target ECS Services (Relay with in-process Dispatcher + OpenAB/Jellii), superseding the previous three-service topology. **PR #9 itself was documentation only**; the bounded P4-T4b live test was separately deployed on 2026-10-13. Persistent Relay Service A is still pending.
- **P3.0 feasibility and risk acceptance:** authenticated ACP/model and a one-off Worker-to-Agent smoke passed under Arthur's explicitly accepted task-boundary risk for that test only. This is not proof of production sandbox restrictions or P4 readiness; Jellii remains scale 0. No N100 main Gateway access.
- **P3.1 implementation status:** PR #8 merged (`e354b92626813267d2b448a6ff340f8bd297f1b6`). PR #10 squash-merged (`39a4b5e0f1820cf0b7a8a5a36cb7a500fd3bf219`) with container-neutral Live composition, BLOCKING admission, refreshable post-tool timeout, and fail-closed CLI wiring. Fast Voice 52/52 offline PASS. Local paced-PTT Live evidence remains handler-level, not ECS deployment.
- **P3.2 implementation status:** PR #12 squash-merged (`44e15ed39f2c860cf45de2b019152cabd06f4451`). In-process Dispatcher claim/recovery is on main.
- **P3.3 implementation status:** PR #13 squash-merged (`d9dce1aef06f9b247a8008056a8769f895c4da91`). The P3.3 tests remain offline-fake `/acp`. Live Jellii ACP is recorded under P4-A1, not as a P3.3 rewrite.
- **P3.4 implementation status:** `check_task_status` looks up this owner's latest or specified task. Queued/running/failed/uncertain/expired never invent `speak`. Completed returns bounded speak/card/details_md. `delivery_state` is independent (`pending`/`read`/`spoken`). Reopen-store lookup works. Offline Relay tests expanded by P4-A1 smoke. Not a new tool-disabled Gemini session, not ECS replacement durability. Real ECS/OpenAB remains separately gated.
- **P4-A1 code status:** PR #16 squash-merged (`7e9543817fdc9899a701fd133d7a9777c15c0259`). `python -m relay.acp_smoke` admits one task through `SQLiteTaskStore` + `AsyncDispatcher` + `OpenAbAcpExecutor` + Inbox. Exit 0 only for completed + bounded Inbox result. Offline Relay 54/54, Fast Voice 52/52.
- **P4-A1 live one-off (2026-10-12):** PASS under Arthur's Telegram envelope of that date: pin image to main `7e95438`, one-off Fargate A, Jellii 1 then 0, one ACP `session/prompt`, no retry, no Gemini/ALB/Worker ECS/DynamoDB/ECS Exec/24/7 service, Jellii awake ≤30 min, ECS A cap <$5, P3.0 Jellii tool/egress risk accepted for this one-off only. Same-SG plaintext `ws://` `/acp` with `AMUSANT_ACP_ALLOW_PLAINTEXT=1` is that envelope's **one-off risk exception**, not production transport or TLS acceptance. One `session/prompt` is not independent proof of one billed model inference. Continue one-off. ADR-0002 is the accepted design. Store-ready Relay squash-merged as `69bd7ed6cadaba68176f8fa706c5d8ca880f7376`. Durability envelope squash-merged as `ff5868a3bc4e3453e419f97e0ea06f3f5a3b5a9c`. The 2026-10-12 durability live one-off (below) is the table/role/write-after-stop proof. 24/7 Relay, TLS cutover, and device ingress remain unapproved.
- **P4-A1 sanitized evidence:** Operator ECS describe during the run plus still-readable CloudWatch. No account IDs, IPs, secret ARNs, bearer, prompt text, result body, ECS task IDs, or log-stream names.
  - Source SHA `7e9543817fdc9899a701fd133d7a9777c15c0259`. Image `amusant-relay@sha256:1f4fe7ef1dd28307b366bd4b647d90a459adb6743b6dc3bdf9a4d2d58c971b40`. A taskdef `oab-amusant-a1-smoke:1` (256 CPU / 512 MiB Fargate, no taskRole).
  - Relay one-off STOPPED, container exitCode=0. Started `2026-10-11T16:31:51.903Z`, stopped `2026-10-11T16:32:20.224Z` (~28 s). Times are from the live describe; later `list-tasks STOPPED` was empty.
  - Relay container log at `2026-10-11T16:31:54.909Z`: `exit_code=0`, `status=completed`, `has_result=true`, `delivery_state=pending`, `error=null`, Inbox `task_id` present.
  - Jellii taskdef `:9`. Preflight and post-cleanup desired/running/pending `0/0/0` (cleanup reconfirmed after the run). Jellii container logs `2026-10-11T16:31:09.256Z`–`2026-10-11T16:32:35.111Z` (~86 s): ACP enabled, client connected, agent initialized, session created, client disconnected.
  - Cost Explorer and provider invoices were not pulled; spend is **not independently verified**. `main` has no version-controlled Dockerfile/taskdef, so the digest pins the artifact but the image is not repo-reproducible.
- **P4 durability live one-off (2026-10-12):** PASS under Arthur's Telegram execute envelope of that date: table `amusant-task-ledger` (PAY_PER_REQUEST, on-demand max 20/20, TTL `retain_until`), dedicated writer/reader roles (not `oab-task-role`), image pinned to main `ff5868a`, one-off Fargate A, Jellii 1 then 0, one ACP `session/prompt`, no retry, no Gemini/ALB/Worker ECS/NAT/ECS Exec/24/7 service, Jellii awake ≤30 min, ECS A cap US$5, DynamoDB test cap US$1, table kept. Same-SG plaintext `ws://` `/acp` with `AMUSANT_ACP_ALLOW_PLAINTEXT=1` is that envelope's **one-off risk exception**, not production transport or TLS acceptance. Functional PASS is A STOPPED then strongly consistent `python -m relay.store_get` `exit_code=0` / `status=completed` / `has_result=true`. One `session/prompt` is not independent proof of one billed model inference. 24/7 Relay and device ingress remain unapproved.
- **P4 durability sanitized evidence:** Operator ECS describe during the run, CloudWatch smoke JSON, and after-stop `store_get` under the reader role. No account IDs, IPs, secret ARNs, bearer, prompt text, result body, ECS task IDs, Inbox UUIDs, or log-stream names.
  - Source SHA `ff5868a3bc4e3453e419f97e0ea06f3f5a3b5a9c`. Image `amusant-relay@sha256:550a7973501e79ee0723f3ccb7bdcedebc87b5e7f75d01f9dbff9721182cb88a`. A taskdef `oab-amusant-durability:1` (256 CPU / 512 MiB Fargate, dedicated ledger task role).
  - Relay one-off STOPPED, container exitCode=0. Started `2026-10-12T05:37:43.806Z`, stopped `2026-10-12T05:38:15.579Z` (~32 s). Times are from the live describe.
  - Relay container log: `exit_code=0`, `status=completed`, `has_result=true`, `delivery_state=pending`, `error=null`, Inbox `task_id` present.
  - After A STOPPED, `python -m relay.store_get` via `amusant-store-reader` reported the same `exit_code=0` / `completed` / `has_result=true` / `delivery_state=pending`.
  - Jellii taskdef `:9`. Preflight and post-cleanup desired/running/pending `0/0/0`. Table retained for later P4.
  - Cost Explorer and provider invoices were not pulled; spend is **not independently verified**.
- **P4-T1 offline WSS PoC (historical phase, 2026-10-12):** PR #23 squash-merged (`bd0b250cd147fa2ff94a0eacffe18007ebd7d97a`). Local fake ACP only; Relay 105/105 and Fast Voice 52/52. This does **not** prove live Jellii WSS, deployed sidecar, or AWS TLS. P4-T2 NGINX sidecar deployment design is review-only; no AWS resources changed.
- **P3.1 Live validation (2026-10-11):** Official Gemini 3.8 Live docs show function behavior `BLOCKING` pauses generation until tool response and cancels a pending blocking call on new user input. The SDK schema now sets `Behavior.BLOCKING` for `delegate_task` only; OpenAB remains asynchronous. Under separate authorization, a local loopback Relay PTT test with paced Flite PCM, the verified project key, `gemini-3.8-live`, and 128 output tokens produced one `delegate_task`, same-call-ID `accepted`, durable SQLite task+pending outbox, 64,322 output-audio bytes, captions whose in-memory check matched `accepted`, and `turn_complete`. This verifies a local tool-call/voice-ack path, not physical Kai-sim or ECS startup. No audio/transcript was saved; no OpenAB/AWS mutation. The subsequent inactivity-refresh/absolute-timeout change has offline coverage only and was not Live-rerun. Usage metadata/billing was unavailable, so exact spend is unverified.
- P0 = Foundation；H0 = Hardware UX Gate。先完成軟體 gate，再進 H0/H1。
- Kai 上游核查基準：v0.12.0，commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f；MIT License。

## P2 — Fast Voice（完成）

- 已新增 `fast_voice/` provider-neutral `LiveProvider` Protocol、provider capability metadata、provider error metadata 與 fail-closed 的明確 provider selection。介面依 SPEC.md §17；不自動選擇／切換，也不做 fallback。
- 已新增 `GeminiLiveProvider` scaffold，使用注入的 client/types 物件呼叫 `connect`、`send_realtime_input`、`receive` 與 `send_tool_response`；另有 `build_live_provider()` 啟動接線，要求明確 `AMUSANT_LIVE_PROVIDER=gemini`，由執行環境注入 `GEMINI_API_KEY`。沒有自動 provider fallback。
- Contract 定義 mono PCM16LE、Kai baseline 16 kHz input／24 kHz output、input/output partial/final transcript、tool call、interruption、turn complete 與 sanitized error events；callback 順序與 cancel 確認語意亦有定義。
- Gemini scaffold 將 PTT barge-in 與 standalone cancellation 分開；基於 Kai source 的 activity-start 行為標示 barge-in，獨立 `cancel_response()` 目前 fail-closed（unsupported），不假稱可取消。
- Amusant 根目錄新增 `pyproject.toml` 與 `uv.lock`：宣告 `google-genai>=2.0`，鎖定 `google-genai==2.29.0`。SDK 只安裝在 N100 的 repo-local `.venv`；全域 Python 與既有 `relay/.venv` 未改。執行前備份：`~/.openclaw/backups/amusant-before-google-genai-2026-10-08.tar.gz`（SHA-256 已核對）。
- Root `uv run --locked python -m unittest discover -s fast_voice/tests -v`：25/25 PASS；含 fake-session、SDK config、PTT bridge fake-provider integration tests。另在 `relay/` 環境執行 P1 suite：4/4 PASS。Fake-provider bridge tests 不是 Kai-sim 實體麥克風或 Live runtime 證據。
- 實際驗證（2026-10-08，使用者核准上限 US$0.50）：從 AWS Secrets Manager 讀取指定 JSON 欄位至程序記憶體，未輸出或保存 key。Models list API 成功認證，首頁列出 62 個 models，包含 Kai baseline `gemini-3.8-live`；這證明 key 可呼叫 metadata endpoint，不代表 Live 權限。
- 前兩次（top-up 前）Live handshakes 在 session 前失敗；一次觀察到 SDK `APIError`／WebSocket close code `1011`。Models list key authentication 通過，但無 request ID 對應後台 429。
- 儲值後第一次 cap-32 text turn 只回 2 bytes，屬無效 audio output；隨後 cap-64 驗收回合 transcript `Ready.`、turn_complete 非取消、48,000 bytes 24 kHz PCM（約 1 秒），符合 text-to-speech smoke 門檻。帳單未獨立查證。
- `relay/live_bridge.py` 將 Kai `/ws` Bearer auth、PTT start/end、16 kHz PCM input、Gemini provider 與 caption/24 kHz PCM/turn_complete 接起；300 ms tap filter、1 MB RAM utterance cap、private/loopback bind、不落盤。25 root tests PASS（含 fake-provider bridge/overflow tests）；另 4 個 P1 relay tests PASS。真實 Kai-sim mic/PTT runtime cap-128 turn 已通過一次；cap-64 早期回合截斷，詳見以下逐回合記錄。
- 後續一次明確驗收（output cap 64，單一文字 prompt `Say exactly the single word READY, in a natural speaking voice, and nothing else.`）：Live session 建立成功；output transcript `Ready.`（SDK 標為非 final），turn_complete 非取消，收到 24 kHz PCM16LE 48,000 bytes（約 1 秒）。依預先訂門檻 transcript contains READY + turn complete + audio >=12,000 bytes，**text-to-speech smoke PASS**。沒有保存音訊或測真實麥克風輸入；實際費用未獨立查證。因 cap 32 與 64 的測試是在 top-up 前後不同時間，不能單獨判定成功是儲值或 token cap 所致。
- 新增 `relay/live_bridge.py`：Kai `/ws` + Bearer token、16 kHz PCM16LE PTT input、300 ms tap filter、RAM-only buffer、Gemini Live adapter、回傳 caption/24 kHz PCM/turn_complete；只允許 loopback/private bind，server tests 使用 fake provider。真實 Kai-sim PTT→Live 已有 partial runtime result；完整播放驗收未通過，見 2026-10-09 記錄。
- 2026-10-09 N100 本機預檢：repo 在 `4ad414a`；`uv 0.11.25`、`google-genai 2.29.0`、`websockets 16.1.1` 可用；8765/TCP 未監聽。未啟動 bridge、未讀取 API secret、未呼叫 Gemini。依使用者 01:08 更正，Kai-sim Windows 端依賴已於昨日安裝，當時測試已成功；不需重裝或重跑 setup。筆電最後回報於 00:09 關機；仍待開機後做真正 PTT → Gemini Live → speaker E2E。
- 2026-10-09 真實 PTT 嘗試：Kai-sim 成功連上 private bridge；使用者回報語音問 `1+1` 後 client 顯示 `1 加 1 等於 2`，但未聽到語音。這是 PTT 路徑得到文字模型回覆的使用者回報；server 沒留下 `turn_complete` 或音訊 byte log，因 bridge 在回合完成前被停止，無法判定音訊未產生或被中斷。停止後確認 8765 關閉。SDK 警告 `GOOGLE_API_KEY` 與 `GEMINI_API_KEY` 同時存在；已查 SDK 2.29.0 source 並用 dummy values 離線測試：Amusant 明確傳入 `api_key` 時此參數優先，警告本身不證明實際用了 ambient key。API key 來源為本次 Secrets Manager 注入；Google account/billing 未獨立查證。單次 Live 嘗試已用，不重試；沒有保存音訊。
- 2026-10-09 第二次明確授權的真實 Kai-sim PTT→Live 回合：process log 記錄 client 連線、`turn_complete`、input 146,432 bytes、output 23,520 bytes；依 mono 24 kHz PCM16LE 約 0.49 秒音訊。使用者回報字幕 `[done] 2加2等于4`，喇叭只播到 `2...` 後截斷。故真實 PTT/input→模型文字與短音訊有 runtime evidence，但完整可聽語音回覆未通過。橋接已停止且 8765 確認關閉；沒有保存音訊。執行時已 unset ambient `GOOGLE_API_KEY`，以 Secrets Manager key 作 explicit client api_key；之前環境警告不再出現。Gemini 帳單未獨立查證。此為新授權的單次請求，不能自動再試。
- 2026-10-09 第三次明確授權的 cap-128 Live 回合：Kai-sim 以語音問 `3+3`；client 字幕 `[done] 3 + 3 等于 6`，使用者確認完整聽到語音。process log 記錄 client 連線、`turn_complete`、input 152,576 bytes、output 92,642 bytes（約 1.93 秒 24 kHz PCM16LE）。相較 cap-64 的 23,520 bytes／0.49 秒，**cap-128 這次完整語音 E2E PASS**；但因輸入語句和模型抽樣有差異，不宣稱已單獨證明 cap-64 是截斷唯一根因。無原始音訊／逐字稿落盤，Gemini 帳單未獨立核對。使用者於 02:17 確認 Kai-sim client 已退出並要求關閉 bridge；程序已停止，8765/TCP 已驗證無 listener。
- 音訊截斷離線分析（無新 Live request）：Amusant bridge 對每個 SDK audio callback 分塊送出 PCM，之後才送 `turn_complete`；Pinned Kai v0.12.0 [`sim_device.py`](https://github.com/zbruceli/kai/blob/1681fb3c11acd28525d8987bba4b9bf4bc2a288f/relay/kai_relay/sim_device.py) 對每個 binary frame 呼叫 `Speaker.feed()`，`turn_complete` 只印 `[done]`、不清空播放 buffer。P1 實體 speaker tone/echo 曾通過；本次 bridge 確認只送出 23,520 bytes（約 0.49 秒），故證據較支持 Live/adapter 端提供的音訊 payload 本身短，而非 client 在 turn_complete 主動截斷；精確成因（模型輸出、64-token cap 或 stream handling）仍未確證。未經新授權不再呼叫 Live。
- 官方 source review：Google Live 文件確認 server-to-server `google-genai` WSS、16 kHz little-endian PCM input、24 kHz PCM output、text/audio `send_realtime_input`、`receive()` audio parts，以及 PTT 可停用 VAD 並送 activity start/end；Kai `main` 與 pinned v0.12.0 使用相同 model/session/audio/PTT 模式，依賴 `google-genai>=2.0`。Amusant source-level flow/config 與官方/Kai 參照一致；這項 source review 本身不是 runtime evidence，實際文字轉語音 smoke 結果另列於上。已安裝 SDK 2.29.0 的 `AsyncLive.connect` 直接執行單次 `ws_connect`，無內建 retry loop。Google troubleshooting 將 429 RESOURCE_EXHAUSTED/503 視為可退避錯誤；402/403 類 client errors 才可能指向 prepaid credit/auth/config 問題。後台兩筆 429 與我們兩次嘗試時間吻合，但缺 request ID/timestamps 無法確證逐筆對應；第二次 client-side 觀察值是 WebSocket 1011。

## P1 — local mock probe

- 原始碼：relay/mock_relay.py；隔離依賴環境：relay/.venv；websockets 15.0.1。
- Network binding：mock 只綁明確指定的 private LAN 位址；文件不保存實際 IP，連線前於本機確認。
- 認證：mock 要求 Bearer test token；執行時值僅由本機環境提供，不放入程式或 repo；只供可信家庭 LAN 測試。
- 協定：Kai-compatible /ws；16 kHz mono PCM16 PTT input；回傳文字卡片、字幕、短 tone + resampled audio echo；單次 audio 暫存 RAM，無 disk log、無模型/API/OpenClaw calls。
- 單元／合成驗證：4 個 unit/protocol tests PASS；private host synthetic WebSocket smoke PASS，收到 state/card/caption/turn_complete 與 6,240 bytes audio。
- 真實 Kai-sim runtime（2026-10-08）：same-LAN client connected；多次 PTT 收到非零 PCM input，mock 回傳 card/caption/turn_complete 與 tone/echo，使用者確認聽到自身語音回聲；一次 Ctrl+C 後重連成功。使用者手動回報 first-audio 與重連均 <1 s，樣本數／計時精度未記。伺服器未保存音訊、未呼叫外部模型/API；不代表語音理解或 Live latency 通過。
- Kai source-level 比對以 pinned v0.12.0 commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f` 為準；本次 runtime 證據只涵蓋 kai-sim 基本 PCM/PTT/card/caption/tone-echo 閉環，不代表完整 Kai relay 或硬體相容性。
- 界線：mock 是 P1 test probe，非 Kai fork、非完整 Kai relay，也非 production Amusant Relay；未實作 Opus/TLS-PSK、Live provider、Hermes/OpenClaw。
- 效能：`capture_ms` 量的是 PTT 按住時間，不是延遲。使用者手動回報 mock 的 PTT release→first audio <1 s；樣本數／精度未記錄，非正式 p50/p95，也不是 Live provider 效能證據。
- 目前 mock 在 PTT down 即送 `thinking`，與 Kai relay 的狀態時序有差異；不是 Kai-sim 音訊路徑已驗證的證據。
- Relay 目前由背景 exec process 執行，非 systemd；沒有修改 firewall、port-forward 或 OpenClaw Gateway 綁定。重開機後需手動重啟。

## P1 smoke outcome

- PASS：same-LAN Kai-sim handshake、PTT capture、card/caption、可聽 tone/echo 與一次斷線後重連。
- 使用者手動估計 release→first audio、重連均 <1 s；樣本數與計時精度未記，不作為 p50/p95。`capture_ms` 是 PTT 按住時間，不是回應延遲。
- P1 是 mock-only transport smoke，不代表 STT、Live provider 或正式 Relay 通過；正式 latency evaluation 留待 P4。

## P1 probe 定位

- **P1 當時**的 mock probe 是可重現的協定／音訊測試工具，不是正式 Relay；**後續** `relay/` 已實作真實 Live／Task Ledger／Dispatcher／ACP 模組，且 #31 真實裝置連上 Amusant Relay functional PASS。請勿以 P1 時期的工具定位描述目前整個 `relay/`。
- P1 mock Kai-sim smoke 已通過；P2 真實 Live 和 #31 本地實機 FAST 已後續通過各自功能驗收；**尚缺**裝置可聽第一聲延遲分布、持續雲端 Relay／端到端、操作可靠性驗收。

## Privacy de-identification

- Current Markdown generalizes user-specific examples, account identifiers, exact private host/IP details, and work/network context while preserving project requirements.
- Published main history now starts from a de-identified root snapshot; earlier commits are no longer in its ancestry. GitHub caches and existing clones may still retain old versions.
