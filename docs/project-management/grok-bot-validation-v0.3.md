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

Owner 亦接受 OpenClaw Grok-O0。G2 spike 已合併，G2 acceptance 裁決仍依其原有 review 記錄，不由本次 G3 工作重新推定。2026-10-26 Owner 對 Issue #123 明確授權 Codex G3-P1 **offline-only** Callback Adapter 實作與本地離線驗證。G3-P2 live deployment、真實 Grok Provider 呼叫、公開入口、production 修改與 G4 接入均未啟動。

## 階段依存更新

| 階段 | v0.3 gate |
| --- | --- |
| G0 — Hermes 實體 E2E 基線 | 繼續記錄實際 bundle、Slow 結果、Inbox、新 PTT 回讀與通道清空狀態。G0 不再是 G1、G2 或 G3 隔離驗證的必要前置。 |
| G1 — Grok 原生產品驗證 | Owner-reported 四項 PASS，已接受。 |
| G2 — 隔離程式與離線驗證 | G2 spike 已合併，作為 G3-P1 的 validator／SQLite receipt baseline；其 acceptance 記錄沿用原 review，不由後續工作重新推定。G2 隔離驗證不等待 Hermes G0。 |
| G3 — Grok 雙向驗證 | P1 隔離驗證不以 G0 PASS 為前置；目前只授權離線 Callback Adapter、TTL、SQLite 原子驗證與 mock tests。P2 live deployment 仍須另行審查獨立環境、入口、Owner 權限、案例、用量與 rollback，並取得明確啟動授權。 |
| G4 — Relay／StickS3 接入 | 仍要求既有 G0 實體基線 PASS、相關 G3 成果證據，以及先行核准的 backend identity、通道完成、取消、Single-flight、migration 與 rollback 決策。 |
| G5 — 路線決策 | Owner 根據產品效益、成本、設定與救援負擔決定。 |

G0 原有實體 gate 定義不變：核准 bundle 下 FAST 可用、實體 Slow 有實質短結果入 Inbox、新 PTT 明確要求後正確回讀，且沒有未處理測試任務卡住通道。v0.3 僅調整其對前段隔離驗證的依存；G4 仍須 G0 與 G3 的相關證據。

## G2 baseline（保持既有契約）

G2 的既有獨立 spike 位於 `spikes/005-grok-bot-validation/`，包含鎖定依賴、dry-run 預設 CLI、單一 `submit_result` MCP 工具、獨立 SQLite、去識別 evidence、Windows／Ubuntu 操作說明與 focused offline tests。G3-P1 保留其 MCP contract、store、receipt 與 regression tests。

Golden Path 必須透過實際 MCP SDK 協定完成：**Prepare → Mock Webhook → MCP `submit_result` → SQLite durable receipt → CLI readback**。Mock Routine 執行一筆固定業務 fixture；相同 `submit_result` 重播及 CLI readback 不可再次 POST Webhook 或再次執行 fixture。commit 後 ACK 遺失的重播須取得同一 receipt，且仍只有一份 durable report。

G2 僅使用 loopback、mock transport、假 Owner token 與暫存資料庫。真實 Routine POST 的程式路徑若存在，必須顯式 `--live`、停用所有 redirect／自動 retry；本階段不執行它。

## G3-P1 Callback-first offline scope（2026-10-26）

Owner 已接受 Callback Adapter 設計並明確給予 G3-P1 offline coding GO。此工作以合併的 G2 Spike 為基礎，只修改 `spikes/005-grok-bot-validation/` 與必要設計文件，不修改 production Relay／Facade／Hermes，也不啟動 Routine、公開 listener、Tunnel、OAuth 或真實 Provider。

驗證順序先測 callback，是 **feasibility/test order**，不代表最終架構已選定，也不表示 Remote MCP/OAuth 已淘汰。Remote HTTPS MCP/OAuth 保留為獨立 gated candidate／conditional Plan B；後續是否採用須按其本身的 Owner scope 與驗收證據審查。

離線 callback contract 固定為 `POST /callback/results`、token-free JSON body 加 task-scoped `Authorization: Bearer`、固定可信設定的 canonical Owner mapping，並重用 G2 的 `PilotStore`、validator 與不可變 receipt。不得建立第二套 Task Ledger。token TTL 必須由 prepare 明確提供，無預設值，限定 1–86,400 秒；對舊 G2 row 的 NULL expiry，callback fail closed。Owner／hash／revocation／expiry 檢查與首次 insert／duplicate 分支都在同一 SQLite `BEGIN IMMEDIATE` transaction 內。已提交 receipt 在過期或撤銷後仍可 CLI readback。

Offline Golden Path：**Prepare → Mock Webhook → `POST /callback/results` → SQLite durable receipt → CLI readback**。Mock business fixture 與 Mock Webhook 各執行一次；同報告 callback 重播必須回相同 receipt，不能觸發第二次 webhook 或業務執行。測試使用 ASGI transport、暫存 SQLite 與固定 fixture，不代表公開入口或 Routine reachability 已驗證。

callback app 只接受精確 Host allowlist、POST 路徑、最多 16 KiB JSON body、單一 Bearer header，並使用必填且每個 ASGI app instance 有界的 in-flight cap；ASGI factory 本身不啟動 listener。若未來 public URL 與 `/callback/results` 不同，經審查的 Proxy 規則必須明確映射至此 canonical path，保留 Authorization 並遮蔽 proxy/app logs 中的秘密。現階段未選定 host、route rewrite、TLS、proxy 或 secret-delivery 機制；task-scoped capability 可能可見於 Bot execution context，header 不代表 Bot 看不到。長期 Owner／ingress credentials 不得放入 prompt/task body。這些仍須 G3-P2 Owner review 與 live evidence。

G2 不修改 production Relay、Facade、Hermes、Compose、既有資料庫或 CI live workflow；不重做 PR #120 generic delegation／SQLite v9；不使用 Grok Desktop credential、Keychain 或 private API。Draft CI 若跳過 heavy jobs，不作為測試 PASS 證據。

## G3、G4 與證據邊界

OpenClaw O0 已由 Owner 接受。此接受不等於 G3-P2 live setup GO。未來任何 G3 live work 需先提供具體獨立環境、HTTPS route／Proxy mapping、Owner 操作與撤銷方式、測試案例、限額與 rollback；callback capability delivery／可見性也要明確審查。驗證時以平台 connector 可達性與 Routine context 實測為準，不以本機 curl 或本次離線測試代替。

G4 Relay／StickS3 接入仍依賴 G0 與 G3 的相關證據。Grok 原生 App 內容不是 Facade committed Artifact；收到 Grok receipt 不代表 Amusant channel 已完成或 native Run 已終止。

## 參考

- [v0.2 完整契約與安全範圍](grok-bot-validation-v0.2.md)
- [Codex handoff](../handoffs/20261025-grok-codex.md)
- [OpenClaw handoff](../handoffs/20261025-grok-openclaw.md)
