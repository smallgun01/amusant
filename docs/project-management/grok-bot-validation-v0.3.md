# Amusant Grok Bot 驗證提案 v0.3 — 階段 gate 與 Owner evidence 更新

日期：2026-10-25（Asia/Taipei）
Owner：Arthur
基準：[`grok-bot-validation-v0.2.md`](grok-bot-validation-v0.2.md)；v0.2 的契約、primary transport、安全與離線測試要求持續有效，除本文件明確更新的階段 gate 與證據外不變。

## Owner review 與當前授權

Owner 報告已完成原生產品 G1 四項驗證，並接受 G1：

| 案例 | Owner-reported 結果 |
| --- | --- |
| G1-01 Issue 查詢 | PASS |
| G1-02 重複委派 | PASS |
| G1-03 完整旅行文件 | PASS |
| G1-04 修改同一成果 | PASS |

**G1 ACCEPTED（Owner-reported 4/4 PASS）。** Owner observation：無異常。這是 Owner 提供的驗收摘要，不附加未提供的執行細節或獨立重驗聲稱。

Owner 亦接受 OpenClaw Grok-O0。當前授權為 Codex Grok-C1／G2 **offline-only** 實作與本地離線驗證。G3 live deployment、真實 Grok Provider 呼叫、公開入口、production 修改與 G4 接入均未啟動。

## 階段依存更新

| 階段 | v0.3 gate |
| --- | --- |
| G0 — Hermes 實體 E2E 基線 | 繼續記錄實際 bundle、Slow 結果、Inbox、新 PTT 回讀與通道清空狀態。G0 不再是 G1、G2 或 G3 隔離驗證的必要前置。 |
| G1 — Grok 原生產品驗證 | Owner-reported 四項 PASS，已接受。 |
| G2 — 隔離程式與離線驗證 | G1 有價值且 Owner 給予具體 repo/offline GO 後可進行；不等待 Hermes G0。當前 Codex C1/G2 offline-only GO 已給予。 |
| G3 — Grok 雙向驗證 | 隔離驗證不以 G0 PASS 為前置。任何 live deployment 仍須先審查獨立實驗環境、入口、Owner／OAuth 權限 scope、案例、用量與 rollback，並有 reviewed G2 commit 及明確啟動授權。 |
| G4 — Relay／StickS3 接入 | 仍要求既有 G0 實體基線 PASS、相關 G3 成果證據，以及先行核准的 backend identity、通道完成、取消、Single-flight、migration 與 rollback 決策。 |
| G5 — 路線決策 | Owner 根據產品效益、成本、設定與救援負擔決定。 |

G0 原有實體 gate 定義不變：核准 bundle 下 FAST 可用、實體 Slow 有實質短結果入 Inbox、新 PTT 明確要求後正確回讀，且沒有未處理測試任務卡住通道。v0.3 僅調整其對前段隔離驗證的依存；G4 仍須 G0 與 G3 的相關證據。

## G2 當前交付範圍

Codex 在獨立分支與 `spikes/005-grok-bot-validation/` 完成一個最小、獨立的 spike，包含獨立鎖定依賴、dry-run 預設 CLI、單一 `submit_result` MCP 工具、獨立 SQLite、去識別 evidence、Windows／Ubuntu 操作說明與 focused offline tests。

Golden Path 必須透過實際 MCP SDK 協定完成：**Prepare → Mock Webhook → MCP `submit_result` → SQLite durable receipt → CLI readback**。Mock Routine 執行一筆固定業務 fixture；相同 `submit_result` 重播及 CLI readback 不可再次 POST Webhook 或再次執行 fixture。commit 後 ACK 遺失的重播須取得同一 receipt，且仍只有一份 durable report。

G2 僅使用 loopback、mock transport、假 Owner token 與暫存資料庫。真實 Routine POST 的程式路徑若存在，必須顯式 `--live`、停用所有 redirect／自動 retry；本階段不執行它。Remote HTTPS MCP/OAuth 仍是 G3 primary；per-task HTTPS callback 仍只在 v0.2 所列 blocker 經實測與 Owner 核准後作 fallback。

G2 不修改 production Relay、Facade、Hermes、Compose、既有資料庫或 CI live workflow；不重做 PR #120 generic delegation／SQLite v9；不使用 Grok Desktop credential、Keychain 或 private API。Draft CI 若跳過 heavy jobs，不作為測試 PASS 證據。

## G3、G4 與證據邊界

OpenClaw O0 已由 Owner 接受。此接受不等於 G3 live setup GO。G3 啟動前需提供具體獨立環境／HTTPS route／OAuth scope／Owner 操作與撤銷方式、測試案例及限額；驗證時以平台 connector 可達性與 Routine context 實測為準，不以本機 curl 代替。

G4 Relay／StickS3 接入仍依賴 G0 與 G3 的相關證據。Grok 原生 App 內容不是 Facade committed Artifact；收到 Grok receipt 不代表 Amusant channel 已完成或 native Run 已終止。

## 參考

- [v0.2 完整契約與安全範圍](grok-bot-validation-v0.2.md)
- [Codex handoff](../handoffs/20261025-grok-codex.md)
- [OpenClaw handoff](../handoffs/20261025-grok-openclaw.md)
