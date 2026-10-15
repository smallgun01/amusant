# Project Amusant — Execution Plan

> 更新：2026-10-15
> GitHub：Private repository; remote is configured locally.
> 本機目錄：projects/amusant

## 文件權責

- SPEC.md：唯一需求與驗收規格來源。
- PLAN.md：執行順序、里程碑與估時，不重新定義需求。
- STATUS.md：經證據確認的目前狀態。
- SPEC-P0-DELTA.md：P0 歷史決策紀錄，非規格來源。

## 專案邊界

- Amusant 是獨立維護的單一 monorepo，尊重 zbruceli/kai 的架構、kai-sim 協定與後續硬體韌體上游。
- 第一階段只做語音輸入與語音／文字回覆；沒有位置、感測器或實體控制。
- 快語音 provider 由使用者手動選擇；Gemini Live 是首個 baseline。第二個真實 Live provider 是 optional validation，不是 MVP gate。
- **新 P3 提案**：OpenAB native ACP WebSocket + 一個受限 agent；詳見 [ADR-0001](docs/adr/0001-openab-acp-slow-path.md)。舊 OpenClaw Home Bridge 路線暫停，只有隔離的 OpenClaw adapter 可於未來評估。
- 先通過軟體 gate，再進入硬體 UX gate；M5StickS3 是候選，不是專案本身。

## P0 / H0 命名

- **P0 — Foundation**：repo、上游、文件權責與範圍定義；已完成。
- **H0 — Hardware UX Gate**：軟體驗收後，決定專用硬體是否值得繼續；取代原規格中會與 P0 衝突的硬體「Phase 0」名稱。

## Upstream 與 repo

- Kai baseline：v0.12.0，commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f；LICENSE 為 MIT，重用程式碼需保留授權與著作權聲明。
- Amusant repo：private monorepo；初始文件與 P0 reconciliation 已提交至 main。Kai firmware 不在本 repo 中；本計畫不修改上游 Kai。
- P1 音訊客戶端：同一 LAN 上的主機跑 Kai kai-sim；作業系統待確認。另一個私有網路端點跑 Amusant mock probe。

## 相對排程

估算為單一開發者主動工程時間，不含等待外部帳號、網路或硬體；目前約 4–7 工程週。日曆日期待每週投入時間確認後再排。

| 次序 | 階段 | 粗估 | 交付與通過條件 |
|---|---|---:|---|
| P0 | Foundation | 已完成 | SPEC/PLAN/STATUS 權責、Amusant scope、Kai upstream baseline 與 private repo 已定；本次 spec reconciliation 完成後視為關閉。 |
| P1 | kai-sim software smoke | 3–5 工作日 | LAN 主機列舉麥克風／喇叭並以 Kai kai-sim 連到 private host mock probe，完成 PTT PCM 收音、文字卡片及 tone/echo 播放。無需 M5、Live provider、OpenClaw 或 AWS。此 probe 是測試工具，不是完整 Kai relay／正式 Amusant Relay；4 個單元測試、真實跨 LAN 音訊閉環與一次重連 smoke PASS。使用者回報 release-to-first-audio 及重連均 <1 s（手動估計、樣本數未記）；正式 p50/p95 latency 留待 P4。 |
| P2 | Fast voice | 1–2 工程週 | Gemini Live baseline、手動 provider selection、adapter contract 通過；local Kai `/ws`/PTT→Live bridge、fake-provider tests、TTS smoke 均 PASS。**2026-10-09 P2 runtime exit PASS**：same-LAN Kai-sim 收到真實 PTT PCM、Live turn 完成、輸出 PCM，使用者確認喇叭完整播放。此 gate 不含 AWS deployment、OpenClaw slow lane 或硬體。第二 Live provider 可選，只作 abstraction validation。 |
| P3 | OpenAB ACP slow path（local） | 待重新估時 | P3.1–P3.4 squash-merged. Offline Fast Voice 52/52 and Relay 54/54 PASS. Spoken Gemini tool-disabled delivery remains unproven. |
| P4 | Two-ECS software integration / AWS staging | T4b 功能驗收 PASS；A2 另估 | A1 與 DynamoDB durability one-offs PASS（2026-10-12）。**P4-T4b 真實 AWS private WSS/ACP live smoke PASS（2026-10-13）**：8/8 preflight、單次 ACP 任務、停止後 DynamoDB Reader 驗證及回滾。程序例外與限制見 [T4b acceptance](docs/p4-t4b-live-acceptance.md)。Persistent Relay ECS Service A、Device/Voice E2E、24/7 尚未驗證。 |
| H0 | Hardware UX Gate | P4 後估算 | 比較專用裝置與最佳化手機流程；只有明顯降低摩擦才繼續。 |
| H1 | Hardware integration | H0 通過後估算 | 使用 Kai-derived M5StickS3 firmware 驗證裝置音訊、PTT、網路與可靠性；不修改 Kai 上游，除非另行核准。 |

## P4 已完成與下一階段（2026-10-14）

- **已驗證：** Relay 一次性 Fargate Task → Private WSS → Jellii/NGINX/OpenAB，任務完成後 DynamoDB Inbox 持久化，並回復原 Jellii 設定。參考 [Live evidence](docs/p4-t4b-live-acceptance.md) 與 [Issue #26](https://github.com/smallgun01/amusant/issues/26)。
- **下一步 P4-A2：** 將 Relay 配置為可長期運作的 ECS Service A，驗證單一 active task、重啟/恢復、監測/費用、憑證/密鑰更新；**不得**直接把 T4b 測試用 `relay.wss_preflight_smoke` 當成永久服務主迴圈。
- **產品 E2E：** Kai-sim/Device PTT → Gemini Live → `delegate_task` → durable Dispatcher/ACP → Inbox → 新 Live 回合領取／語音回覆，尚未整條一起驗證。
- **Edge/HW：** Issues #27、#11、#14；Device→Relay Transport 不等於 Relay→Jellii WSS。

## P1 連線方案

- Kai-sim client 與 mock probe 使用同一個可信 LAN；測試時再確認主機位址與作業系統。
- Mock 僅綁明確指定的私有 LAN 位址；不新增防火牆規則、不做 port-forward、不公開 Gateway。
- P1 不使用跨網路 tunnel；若未來需要外網存取，另行評估核准的私有連線或雲端架構。

## P1 exit criteria

- LAN 主機上的 Kai simulator 使用 sounddevice 找到麥克風／喇叭；實際 Kai-sim client 的音訊與連線結果有可保存證據。
- PTT start/end 與 Kai WebSocket wire protocol 對接成功。
- private host mock probe 回傳文字卡片、字幕、tone/echo PCM；不含外部模型、OpenClaw action、AWS、hardware 或 provider credentials。以實際 kai-sim 跨主機執行結果作為 P1 runtime 證據；synthetic test 不代替此 gate。
- Kai 協定 source-level 比對以 pinned commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f 為基準；不因協定子集測通就宣稱完整相容。
- 測試不保存原始音訊；錯誤／斷線可觀察且不洩漏憑證。

## 安全與治理 gates

- P3 不接 N100 OpenClaw。OpenAB `/acp` 必須具備已編入的 `acp` feature、bearer 認證、單一受限 agent、最小工具權限、獨立模型憑證；ACP cancel 不保證真正停止執行，P3 單次上限 120 秒。
- P3 local durability uses SQLite task + dispatch intent and an application-scoped Async Dispatcher inside Relay; no standalone Worker ECS. AWS ECS-task replacement requires an approved external durable store (DynamoDB is a candidate); SQS is not an MVP requirement. The two-ECS amendment is accepted as the architecture baseline, not deployed; AWS changes/costs remain separately gated.
- 任何 AWS 費用、硬體購買、私有主機設定變更或 Kai 上游改動，另行核准後才執行。
- 目前此排程是專案里程碑，非 cron/提醒。

## 跨軌 Hardware / AWS 整合測試計畫（2026-10-15 決策）

執行順序與 Gate 矩陣以 [Cross-track Integration Roadmap](docs/hardware/cross-track-integration-roadmap.md) 為準。本段是**執行排程**而非修改 SPEC 需求或宣稱驗收通過。

| 先後／可平行 | Gate | 範圍 | Tracking | 尚未通過的驗收 |
|---|---|---|---|---|
| **優先** | **G0 / H0.3 Stage C** | 原版 Kai StickS3 → 本地 Amusant Relay（Opus + TLS-PSK） | [#31](https://github.com/smallgun01/amusant/issues/31) | 真實電池 PTT、喚醒/打斷/重連與延遲；#32 是離線程式碼 PASS，不是 G0 PASS |
| **與 G0 可平行的 read-only/offline** | **P4-A2** | 現有一次性 ECS RunTask → 持久 ECS Service A，保留已驗證 ACP 私網 | [#35](https://github.com/smallgun01/amusant/issues/35)、[#26](https://github.com/smallgun01/amusant/issues/26) | Service 運行、重啟/恢復、成本/安全與回滾；任何 AWS 變更需獨立授權 |
| **與 G0 可平行的 read-only/offline** | **G1 / Edge** | 評估標準憑證 WSS/Cloudflare 與 TCP TLS-PSK，先軟體假客戶端 | [#27](https://github.com/smallgun01/amusant/issues/27) | 不可把共享 Tunnel sidecar 當已可用；Route/SG/身分/費用需證明 |
| **G0/H0 通過後** | **H1-W1** | Amusant-owned firmware 的 NVS 動態 Wi-Fi（USB 設定） | [#36](https://github.com/smallgun01/amusant/issues/36)、[#11](https://github.com/smallgun01/amusant/issues/11) | 不再預燒 SSID/密碼；保留本地 PTT/Deep Sleep Baseline |
| W1 後 | **H1-W2a** | 多 Wi-Fi profiles 與快取感知切換 | [#36](https://github.com/smallgun01/amusant/issues/36) | 多網路失敗切換、本地 Relay 回歸 |
| W2a 後 | **H1-W2b** | SoftAP + Captive Portal（手機免 USB 配網） | [#36](https://github.com/smallgun01/amusant/issues/36) | 手機配網/安全/逾時/恢復/續航 |
| **A2 + G1 後** | **G2** | 實體 StickS3，**單一已知 Wi-Fi** → AWS Relay → Gemini | [#27](https://github.com/smallgun01/amusant/issues/27)、[#35](https://github.com/smallgun01/amusant/issues/35) | 若改標準 WSS，應先用單獨 Firmware Transport PR；**不必等 W2b** |
| **G2 + W2b 後** | **G3** | Wi-Fi 切換 + AWS Fast Voice + Slow Task/ACP/DynamoDB Inbox 整條產品 E2E | 後續於 Gate 前另開追蹤 Issue | 已測的 end-to-end、延遲、復原、費用及版本追溯 |

**隔離原則：** 一個 Gate 只動一個主要層次。Firmware W1/W2 使用同一個經 G0 驗證的本地 Relay；AWS A2/G1 先用軟體 Client，不因 Firmware 配網未完成而等待；G2 使用已知 Wi-Fi 單獨驗證跨網路 Device Edge。

**獨立核准：** 這次文件/Issue 規劃沒有核准 Firmware 匯入/Flash、付費 Gemini、Cloudflare/AWS 變更、對外入口或永久 24/7 服務。PR #34 的 offline CI 用於各軟體 PR 回歸，但不能代替真實硬體 / AWS live gate。
