# Codex Desktop 任務指引：Amusant Grok Bot 隔離驗證

日期：2026-10-25（Asia/Taipei）  
對象：Codex Desktop（Windows 11 開發環境）  
搭配文件：docs/project-management/grok-bot-validation-v0.2.md  
狀態：可轉交工作單；v0.2 已納入 Zongzhu／Grok Gadgets／Groki mandatory references；本文件未觸發實作、PR 或部署。

## 1. 任務目標與目前可開始的工作

用最小實驗驗證 Grok Bot 能否承接 Amusant 一般委派，回報短結果，並把完整成果留在原生 App 接續。Agent 能力與權限留在原生產品；不做每工具一個 Relay adapter。

收到這份工作單後先完成 Grok-C0：唯讀現況核對與狹窄 file plan。維持 Owner 的先完成既有 E2E、再擴大新功能實作順序。

Grok-C1 的 repo/offline 實作須同時具備：

- 既有 OCI 實體閉環的 G0 PASS 證據。
- Grok 原生產品 G1 有用、值得接線的結論。
- Owner 已轉交並指示啟動 Grok-C1／G2 的 repo/offline 範圍。

之後在這個範圍內持續完成，不逐次要求確認。這不授權 live provider、帳號連接、公開 endpoint、OCI 操作或 merge。

## 2. Grok-C0：現在先交 source map 與最小方案

讀本地適用的 AGENTS.md／repo 指引，以及最新版：

- SPEC.md、docs/adr/0004-owner-managed-hermes.md。
- docs/handoffs/20261025-codex.md、20261025-openclaw.md、協調索引。
- PR #116／#118／#119／#120，Issue #46／#62／#105。
- relay/application.py、slow_path.py、dispatcher.py、task_store.py、hermes_result.py。
- .github/workflows/ci.yml。

外部 mandatory references（以最新 main 讀 source，不抄 README 結論）：

- `Liu-zhongxian/zongzhu-bot`：`docs/GUIDE.md`、`xiaozhi-esp32-server/main/xiaozhi-server/core/handle/grokBotHandle.py`、`grokBotReply.py`。重點：accepted/unknown、per-task callback token、atomic/write-once result、late reply、approval hint。
- `adidshaft/grok-gadgets` + `adidshaft/grok-gadgets-gateway`：`docs/getting-started/connect-grok-bot.md`、`docs/public/support-matrix.md`、gateway `README.md`、`docs/architecture.md`。重點：Remote HTTPS MCP + OAuth、owner-run tunnel、tool/device port 分離、command status／no blind resend、evidence boundary。
- `JosssphZhou/groki-bot`：`docs/architecture.md`、`gateway/GBOT_VOICE_VALIDATION.md`、`gateway/scripts/gbot_voice_session_bridge.mjs`。重點：async completion、stale/replay filtering、result ≠ new user authorization。其 desktop credential/internal API 路徑只做風險研究，**不得實作進 Amusant spike**。

本提案 snapshot：main 73397323412e9c27c6caacc5adbbd1a2b5dd9962；#116／#118／#119 merged；#120 OPEN Draft，generic delegate_task 與 SQLite v9 已在該 PR 實作。開工時更新狀態，不重複 generic delegation、task kind 或 migration 工作。

既有 Hermes C2 工作與 review 不因本單自動取消。先標明已進行的分支與 overlap；不要覆蓋未提交工作，不把 Grok 改動混入 #120。

Grok-C0 交付：

1. source SHA、現有 PR 狀態及實作 gate 是否已有證據。
2. 一頁 file plan／dependency plan，分清已存在檔案與擬新增檔案。
3. **Primary 固定為 Remote HTTPS MCP/OAuth** 的 transport/security plan；列出 Zongzhu 式 per-task HTTPS callback 何種 blocker 下才啟用為 fallback。
4. 三個 external reference 的 reusable pattern／不可照搬部分對照表，並列出五至十項核心測試對應風險。
5. READY FOR G2 WHEN GATES PASS 或具體 BLOCKED；此階段不寫 runtime／開新 PR。

## 3. Grok-C1／G2：最小離線 spike

擬議範圍：

~~~text
spikes/005-grok-bot-validation/
  README.md
  pyproject.toml
  lock file
  少量程式模組
  tests/
  .gitignore
~~~

路徑尚未建立；若同類 spike 已存在，沿用而非再開一份。模組數以可理解為準，不為此建立 framework。MCP 使用受維護 SDK 並鎖版；不要更新 production dependencies。

只提供：

- dry-run 預設的送件／查閱／人工結案 CLI。
- 真實送件須顯式 live 模式，HTTP client 不自動重送 POST，且關閉所有自動 redirect，包含可能重送 POST 的同來源 307／308。
- 單一 `submit_result` MCP 工具；本機協定測試涵蓋 initialize、tools/list、tools/call 和 error。Remote HTTPS MCP/OAuth 為 primary live path；domain receiver 不綁 transport。
- 與 production 完全分離的 SQLite 紀錄。
- 去識別化 evidence 輸出。
- 一份供 OpenClaw 使用的真實 CLI／設定／rollback 操作說明，優先描述 owner-run Remote MCP endpoint + OAuth；fallback HTTP callback 只描述 gate／contract，除非 primary 已證明 blocked。

G2 不使用真實 endpoint／credential／模型。沒有資料庫、key 或 route 時預設不進行 network submission。所有測試狀態存暫存目錄。

## 3.1 v0.2 transport decision

Codex 不再把「MCP 或 callback 任選」留給實作者。設計順序固定：

1. `submit_result` domain receiver + **Remote HTTPS MCP/OAuth primary adapter**。
2. Zongzhu 式 per-task HTTPS callback 是 fallback；G2 可測 contract/validator，但除非 OpenClaw 回報 primary 在真實 Owner/Routine 環境被阻擋且 Owner 核可，不建立 live fallback route。
3. Groki 的 internal Grok Desktop API/login bridge 只做設計反例與 async UX reference。

這樣若 primary 成功，Amusant 不需要把 callback token/URL 當長期公開產品介面；若 primary 失敗，fallback 也不用重寫 result store。

## 4. 合約與關鍵語意

以提案第 5–7 節為準：

- submit 前建立 task_id、task_label 與單筆 result_token，server 僅保存 token hash。
- `submit_result` 接受 schema_version、task_id、result_token、reported_outcome、speak、card、native_result_locator；若後續啟用 HTTPS fallback，必須共用同一 validator/store/receipt，不複製狀態機。
- reported_outcome 只有 completed／not_completed，代表 Agent 自述；native Run 保持獨立 unknown。
- speak ≤240 字元，title ≤80，lines 1–4 行且每行 ≤120，tool args UTF-8 JSON ≤3,000 bytes。
- native locator 可選且有界，不當作 Facade artifact，不 fetch、不讀檔。
- 最小 MCP credential 綁 Owner；不接受 payload owner_id。task_id 本身不是授權。
- token／完整指令／私有 native locator 不出現在一般 logs 或 git。
- 不強制 needs_approval／progress callback；runtime 可能先停住。
- canonical report digest 排除 auth token；內容相同但 JSON key 順序不同仍為 duplicate。

提交先 durable prepared，再 durable sending，最後一次 POST。timeout／sending crash 留 acceptance_unknown；不得自動重送、改送 Hermes 或猜測 remote failure。

接受 callback 不依賴 HTTP response 先到，也不依賴 runUuid。runUuid 若真有回傳只記 optional opaque metadata。

回報與 receipt 原子持久化後才 ACK。相同內容回同 receipt；不同內容 conflict，不覆寫。storage error 不回 accepted。晚到回報不復活人工結案；不要把最後一筆 active task 當成 callback 對象。人工結案不等於撤銷 token；在實驗保留期間可收 authenticated late evidence，清理或撤銷後則拒絕且不重建 task。App 後續修訂不得覆寫原 receipt，從裝置提出的新修訂使用新 task_id。

## 5. 禁止夾帶的實作

- 不修改 production application／dispatcher／Task Ledger／schema。
- 不修改 Facade、Compose、Hermes profile、模型、工具或權限。
- 不冒充 hermes_runs／openab_acp，不偽造 execution_status=completed 或 art_ reference。
- 不重做 #120 generic delegation。
- 不做 LLM summarizer、長文字截斷 fallback、業務工具分類器。
- 不建立 queue、scheduler、approval engine、Agent 管理 UI。
- 不替換 FAST、不改韌體或 device provision。
- 不讀真實 secret、不呼叫 Bot／Gemini／Hermes provider、不建立公開入口。
- **不讀或解密 Grok Bot Desktop credential／Keychain／`sand-secrets.json`，不呼叫 `api2.cursor.sh` 私有 RPC，不 hard-code Grok Bot client version，不建立 gbot internal API adapter。**
- 不自動 merge 或修改 production。

這些是隔離試驗的範圍，不限制 Owner 在 Grok 原生介面使用既有能力。

## 6. 必要離線驗收

| 風險 | 必測結果 |
| --- | --- |
| callback 早到 | 已存在 task 可接收；晚到 HTTP acceptance 不覆寫 received |
| POST timeout／crash | 重啟仍不第二次 POST，native state 不捏造 |
| commit 後 ACK lost | retry 得同一 receipt，只有一份 final report |
| conflict／late | 拒絕不同內容覆寫；late 不復活已人工結案 |
| auth／identity | 錯 credential、Owner、task 或 token 無法寫入 |
| payload 越界 | Unicode 字元、UTF-8 aggregate、型別、未知欄位、控制字元正確拒絕 |
| storage failure／restart | 未 commit 不 ACK；已 commit 可在重啟後讀取與去重 |
| 狀態誤解 | overdue／Agent completed 不轉為 native completed／cancelled |
| native locator | 任意 URL／路徑只為資料，不被取用 |
| 假 MCP | SDK client 實際握手與 call 可用，工具 error 可辨識；result domain 不依賴 localhost/private desktop API |

CLI／操作 README 需適用 Windows 開發與 Ubuntu 執行；明示 shell 差異。不要拿 Windows POSIX-path 失敗或缺依賴測試當 PASS。

交付實際已跑的命令與結果，不照抄尚未存在的假 CLI。只對具體風險做 focused tests，修復後不反覆擴大昂貴測試。

## 7. CI 與 Draft PR

G2 指示有效後可在獨立分支建立一個 Draft PR，例如 codex/grok-bot-validation-spike；先查是否已有同一工作。不要修改 #120 分支。

現有 CI：

- 純 Markdown 有輕量檢查。
- Draft heavy jobs skip；這不等於測試 PASS。
- 非文件 Ready PR／main push 會跑既有 offline／ARM64 image／v2 gates。
- 新 spike 不在既有 discovery；merge 前為 existing offline job 增加一個明確小步驟，若需要才將 .github/workflows/ci.yml 納入 diff。
- 不建立新的 ARM64 matrix、不手動 dispatch live workflow、不修改 filters 來掩蓋必要 gate。

Draft 先交本地 focused evidence。Ready／merge 時依 repo 規則一次驗證；實驗 live 本身不可跑在 CI。不要要求 pipeline 注入真實 credentials。

PR 說明依序交代：要降低哪個整合不確定性、這個 spike 能證明什麼、不能證明什麼、測試證據、獨立資料與移除方式。明示尚未取代後端或通過實體 E2E。

## 8. Grok-C2／G4 前的後續工作

僅在 G3 有實際成果後，提出小型接入差異：

1. #120 最新合併狀態與可重用 generic delegation。
2. 真正的 Grok backend identity，不借用 Hermes 名稱。
3. result_received／native unknown／取消與 Single-flight 的產品語意。
4. 如需 schema 變更，精確 migration、歷史相容及 rollback。
5. Inbox／spoken 如何重用，完整文件如何留原生 App。
6. 獨立驗證環境與單一 active Relay 的切換方式。
7. 可受控驗證的裝置案例及還未支援的跨通道接續。

未有這些決策前不實作 G4。若第一個 callback 就要求大改 dispatcher／schema，先回到隔離 spike，不擴大 Grok-C1。

## 9. 回報格式

~~~text
Verdict:
  Grok-C0 READY FOR G2 WHEN GATES PASS / BLOCKED
  或 Grok-C1 READY FOR REVIEW / BLOCKED

Source:
  main / branch / commit / PR
  #120 狀態、G0/G1 證據參照

Scope:
  已變更路徑、明確未涉及的 production 邊界

Evidence:
  實際命令、PASS / FAIL / BLOCKED / NOT TESTED
  MCP 協定測試、持久化與去重、unknown 狀態

Handoff to OpenClaw:
  固定 commit、環境需求、真實 CLI、secret names/types only
  Primary Remote MCP/OAuth 的最小 host／HTTPS 需求、OAuth scope、關閉與移除方式
  Fallback callback 的啟用 blocker 與 exact delta（未啟用時不要部署）

Remaining:
  原生帳號/工具可見性/回報可靠性/裝置整合等未實測事項
~~~

需要 Owner 決定的內容應帶具體方案與原因；不要只問「要不要繼續」。本指引不要求寄送消息給外部第三人或執行原生業務 Action。

## 10. 主要參考

- [提案基準 SPEC](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/SPEC.md)
- [ADR-0004](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/docs/adr/0004-owner-managed-hermes.md)
- [PR #120](https://github.com/smallgun01/amusant/pull/120)
- [既有 Codex handoff](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/docs/handoffs/20261025-codex.md)
- [CI](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/.github/workflows/ci.yml)
- [Routine webhook](https://cursor.com/help/grok-bot/routines)
