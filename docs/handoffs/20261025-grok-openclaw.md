# OpenClaw 任務指引：Amusant Grok Bot 驗證準備與實測

日期：2026-10-25（Asia/Taipei）  
對象：OpenClaw（Ubuntu／既有受限環境存取）  
搭配文件：Amusant_Grok_Bot_Validation_Proposal_20261025.md  
狀態：可轉交工作單；v0.2 已把 Remote HTTPS MCP/OAuth 設為 primary live feasibility path；本文件未啟動部署、原生設定或真實呼叫。

## 1. 現在先做 Grok-O0

收到本單後先完成唯讀證據與具體實驗環境計畫。延續既有授權的存取邊界；本單沒有新授權 SSH、sudo、Docker socket、secret 讀取、網路規則、主機寫入或模型呼叫。

不要把新 Grok 設計混進目前 OCI E2E。也不要因 main 已合併 v2／polling 修正就判定 OCI 已部署或實體 PASS。

主要目標：讓 Owner 知道下一階段究竟要在哪裡跑什麼、使用哪個帳號與既有能力、怎麼接收結果、花多少、失敗時怎麼停止與保存證據。完成這些具體工作後才需要實測啟動決定。

## 2. O0-A：更新現有基線證據

本次 proposal source snapshot 為 main 73397323412e9c27c6caacc5adbbd1a2b5dd9962：

- #116／#118／#119 已合併。
- #120 OPEN Draft，generic delegation 已在做；不假定部署，也不重做。
- #46／#62／#105 與現有 handoff 是依存索引，不是單憑 OPEN／merged 判定的驗收結果。

讀最新 source／PR、日期明確的 E2E 記錄與 Owner 已提供的實測證據。issue 頂端摘要可能落後，以實際 bundle／案例證據核對。

產出矩陣：

| 層次 | 必要資訊 |
| --- | --- |
| Source | main SHA、#116／#118／#119／#120 狀態 |
| Deployment | 實際 image digest／revision；mounted plugin/config 的獨立版本證據 |
| Functional | FAST、Slow 實質結果、Inbox、新 PTT 後回讀、spoken 記錄 |
| Outstanding | 未結案測試 task、slot、目前阻塞原因 |
| Gate | 已有證據 PASS／FAIL／BLOCKED／NOT TESTED，不要求重跑有效案例 |

若現有權限無法查看 digest、health 或資料目錄，列出被阻擋的具體欄位；不以另個帳號、sudo 或 Docker socket 繞過。不得輸出 .env、auth.json、完整 config／inspect env、原始 Run、完整報告或 secret hash。

G0 要求正在收斂的實體閉環有證據；不是要求先關閉 #62 全部營運子項。沒有 G0 PASS 時仍可完成下列計畫，不能開始新功能／live setup。

## 3. O0-B：把 Grok 實驗環境規劃到可執行

提出一個最小方案，明列：

1. 指定的 Owner Bot 與帳號來源；記錄名稱／用途即可，登入由 Owner 在原生安全流程處理。
2. 原生 App 是否可用；Routine webhook、custom MCP 設定是否在該帳號可見。尚未親自觀察的項目標 DOC VERIFIED／NOT TESTED。
3. 實驗程式使用哪個非特權開發目錄、Python／venv、獨立 SQLite 路徑與 port。
4. **Primary feasibility：Owner Grok Bot 是否能以原生 Remote HTTPS MCP + OAuth 連到 owner-run endpoint**；host、DNS／TLS／route、OAuth flow、auth scope、Routine context tool visibility 與撤銷方式。參考 Grok Gadgets 的公開 Mac/iPhone 實測，但必須在 Owner 帳號自行驗證。
5. 若使用既有 tunnel／reverse proxy，需要新增或修改的精確項目；沒有核可不得套用。若 Remote MCP 被實際 blocker 阻擋，另列 Zongzhu 式 per-task HTTPS callback fallback 的 exact delta；**不要自動啟用**。
6. 只公開最小 MCP 端點；production Hermes／Facade／Relay 管理面不可外露。
7. ingress key、MCP credential、result_token 的分離與安全儲存；只交名稱與型別，禁止貼值。
8. 流量、logs、資料保留與移除方式；不把完整成果匯進 Amusant。
9. 停止試驗、停用 Routine trigger／MCP、處理原生未結束工作與撤銷測試 credential 的順序。
10. 測試案例、次數、額度、可接受額外支出與需要 Owner 親自操作的步驟。

Remote MCP 由平台 connector 伺服器連出，Bot 電腦可以 curl 不足以證明 connector 可達。第一個 live feasibility check 就是：Owner Grok Bot 能否完成 OAuth connect、列出 `submit_result`，且 Routine context 真正可呼叫。若 PASS，MCP 保持 primary；若 BLOCKED，記錄是 account/product/tool-visibility/network 哪一層，再提出 per-task HTTPS callback fallback。**禁止以 Groki 的 desktop token/Keychain/internal API 繞過 blocker。**

不需要建立 OCI 第五個常駐服務、不需替每個業務工具開權限、不做新的 Agent 管理 UI。

### Grok-O0 完成格式

~~~text
READY FOR OWNER REVIEW / BLOCKED
G0：已有基線證據與缺口
Source/deployed/plugin/config/device 矩陣
實驗 host、port、HTTPS route、auth、資料位置
逐項 change / reason / authority / verification / rollback
G1 原生案例與 G3 live envelope
現有權限內可完成事項與精確 blocker
~~~

不要回 READY TO DEPLOY。這份計畫要具體到後續啟動只需核可已列出的範圍，不再重新討論整個架構。

### O0-C：mandatory external reference 差異表

O0 報告必須各自回答：

- **Zongzhu**：哪些 callback/idempotency/late-result pattern 可直接借；若 fallback 使用 HTTPS callback，哪些 security/retention 調整是 Amusant 必須補的。
- **Grok Gadgets**：它的 Remote MCP/OAuth、owner-run tunnel、tool/device-port separation 哪些可直接映射到 OCI/Cloudflare；哪些只是 gadget-specific。
- **Groki**：async completion、stale/replay filtering、speech-stop ≠ task-cancel 哪些是產品語意參考；列出 internal credential/API route 為 **DO NOT USE**。

## 4. Grok-O1／G1：原生產品適配

僅在 G0 完成且 Owner 啟動原生試驗後進行。Owner 負責登入、帳號連接與審批；OpenClaw 在已授權範圍內協助操作或整理證據，不擅自假定 signed-in session 可使用。

先最多 4 個測試 turn：

1. 指定 repo open issue 數量與重點。
2. 第二次同類查詢，觀察是否需要重設工具或改 prompt。
3. 合成條件的兩週法國行程完整文件。
4. App 內接續同一成果，巴黎減一天並調整後續。

Issue 必須排除 PR、記錄條件與時點；旅行只規劃，不訂房、購票或付款。使用提案的合成條件，不能把它存成 Owner 的真實偏好。

記錄完整成果是否真的可開啟、修改是否對到同一工作。把一次性 setup、驗收觀察、正常必要批准與救援分開計數。既有帳號連接較方便是產品整合收益，不推論模型能力全面優於 Hermes。

G1 若沒有明顯產品價值，先交結論，不要求 Codex 為了證明接線而繼續建 adapter。

## 5. Grok-O2／G3：真實雙向試驗

前置：Codex G2 固定 reviewed commit／離線證據已取得，實驗 host／入口／credential scope／案例與用量已啟動。可使用經審閱的隔離 spike commit；不得將未合併 prototype 部署到 production 或替換現有後端。

使用一次核可的 envelope 執行，不每次 POST 重問。未涵蓋的新權限、付費或 host/network 變更，先完成具體差異計畫再回報。

1. 在指定隔離目錄依 lock 安裝／啟動；不 live hot-patch。
2. **Primary path first：** Owner 以 Grok Bot 原生流程連接 Amusant Remote HTTPS MCP，使用 OAuth／平台原生安全設定；不把 bearer/token 放 repo、一般聊天或 CLI args。確認只有 `submit_result` 等已核可最小工具可見。
3. 從真實 Routine context 確認 `submit_result` 可見且可呼叫；原生聊天能呼叫不等於 Routine 自動有同一工具。記錄 OAuth connect、tool discovery、Routine invocation 三個 gate。
4. 若 primary PASS，使用 Codex CLI 送一次 task；確認實際 Webhook response，不假設 runUuid 必然存在。
5. 在獨立 journal 觀察 MCP receipt、短摘要、native locator；Owner 在 App 核對成果及接續。App 修改不覆寫原 receipt／摘要，不要求用舊 task_id 重新回報不同內容；這不等於自動同步最新文件至 Inbox。
6. **只有 primary 明確 BLOCKED 時**，提交 blocker 與 fallback delta；Owner 核可後才啟用 per-task HTTPS callback，並沿用同一 validator/store/receipt。callback token hash-at-rest、write-once、late/duplicate/conflict 語意參考 Zongzhu。
7. 一次只進行一件實驗。案例人工結案後再進下一件；不要把原生其他工作納入全球鎖。
8. acceptance_unknown／回報缺席時，到 App 查明；不自動重送原工作，也不改派 Hermes。

### 第一輪上限

- 原生 App 合計最多 8 user turns：G1 四個、設定最多兩個、G3 接續最多兩個。
- Webhook POST 合計最多 8 次：G3 最多六次，G4 預留兩次；失敗與未知也計數。
- 預設新增付費上限 0；使用 Owner 已同意的既有額度，記錄用量前後。
- 不自行購買／升級／開額外計費。run／turn 上限不是費用估算或額度保證。
- 達上限先交判斷，不無限改 prompt 重試。

G3 案例：三筆查詢、一筆文件、一次可恢復的結果回報失敗、一次原生批准觀察。批准案例限定預先定義且可安全拒絕的原生步驟；沒有適當既有權限就 NOT TESTED。回報失敗只影響隔離測試端點，不關 production Relay／OCI。

Agent 可能在回報前就被批准流程暫停。沒有 result report（MCP 或 fallback callback）只能記未收到，不能自動判 failed／needs_approval／cancelled。原生 App 的觀察另記來源與時間。

## 6. Grok-O3／G4：裝置驗收是條件式下一步

G3 成功後，Codex 先提出真正的 Grok backend identity、通道完成、取消、Single-flight 與必要 migration／rollback。Owner 接受精確產品語意後，再依 reviewed bundle 執行。

不能把 Grok callback 包成 Hermes 已確認 completed，也不能用本地停止等待宣稱遠端取消。現有生產語意未被本單放寬。

裝置最多兩個預留案例：查詢、文件。由 Owner 操作 PTT，OpenClaw 記錄：

- 裝置交辦與 truthful admission。
- 短結果正確入 Inbox。
- 完成後不主動開口。
- Owner 新 PTT 明確要求後才回讀，內容是實際結論。
- 完整成果在原生 App 可找到；長報告不進 Live。
- App 接續與 native Run 狀態分別記錄。
- 只有一個 active Relay 接受該裝置工作。

沒做到這個階段就只宣告 CLOUD／callback 可行，不能把 CLI／MCP 成功說成 StickS3 E2E PASS。

## 7. 失敗處理與 rollback

- 保留最小去識別證據，停止新增案例。
- 本地停程式不代表遠端停止；Owner 到 App 查看並處理未完成工作。
- 暫停／刪除 Routine 是避免未來觸發，不當成已啟動 Run 的取消證明。
- 保存原生重要成果；完整內容不複製進公開紀錄。
- 確認回報已收或決定不再收後，關閉實驗入口，撤銷測試 MCP credential／webhook key，依原生支援操作。人工結案時可先保留收取晚到證據；正式撤銷後晚到請求應拒絕，不能自動重建 task。
- 只清理本試驗目錄／route／Routine；不刪 Owner 既有 Bot 記憶、登入、Skills 或 production volume。
- 如使用已核可的 reversible 裝置切換，依 reviewed rollback 回到基線，保留已產生任務資料。
- 發現程式缺陷回報 Codex；不在運行主機臨時修 production code。
- 不使用 Groki 的 `sand-secrets.json`／macOS Keychain 解密／`api2.cursor.sh` 私有 RPC／hard-coded Grok Bot client version 作為 live fallback。

## 8. 最終證據與決策報告

~~~text
Verdict:
  G1 / G3 / G4 PASS、CONDITIONAL、FAIL 或 NOT TESTED
  建議：停止／保留候選／進下一階段
  能力範圍：查詢／文件／原生接續；Action／Coding／Cron 另需證據

Environment:
  source commit、實驗 host 類型、實際 transport、綁定 Owner
  僅必要非敏感 metadata

Per case:
  case alias、入口、submit/receive 時間
  acceptance、report、receipt、摘要長度
  Agent reported_outcome
  原生 Run observation（來源/時間）
  實際成果核對、App 修改關係
  setup / evidence_only / workflow_required / rescue 次數
  用量、結果、未解原因

Scope:
  真正做過的變更與呼叫，未做／受阻項目
  結果接收 ≠ 原生 Run 終止 ≠ 外部 Action 成功

Next:
  最小需要的 code／環境修正
  有效證據如何沿用，避免重跑
  停用／回復狀態與剩餘原生工作
~~~

## 9. 主要參考

- [既有 OpenClaw handoff](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/docs/handoffs/20261025-openclaw.md)
- [ADR-0004](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/docs/adr/0004-owner-managed-hermes.md)
- [#46](https://github.com/smallgun01/amusant/issues/46)、[#62](https://github.com/smallgun01/amusant/issues/62)、[#105](https://github.com/smallgun01/amusant/issues/105)、[#120](https://github.com/smallgun01/amusant/pull/120)
- [Routine webhook](https://cursor.com/help/grok-bot/routines)
- [Remote／Command MCP 設定](https://docs.x.ai/grok-bot/team-bots)
- [Remote MCP 網路來源](https://docs.x.ai/grok-bot/security)
- [原生批准流程](https://docs.x.ai/grok-bot/approvals-security-and-privacy)
- [完整成果與修訂](https://docs.x.ai/grok-bot/files-and-results)
- [Zongzhu Bot](https://github.com/Liu-zhongxian/zongzhu-bot)：`docs/GUIDE.md`、`grokBotHandle.py`、`grokBotReply.py`
- [Grok Gadgets](https://github.com/adidshaft/grok-gadgets) + [Gateway](https://github.com/adidshaft/grok-gadgets-gateway)：`connect-grok-bot.md`、support matrix、gateway architecture
- [Groki Bot](https://github.com/JosssphZhou/groki-bot)：`docs/architecture.md`、`gateway/GBOT_VOICE_VALIDATION.md`；internal API 僅作 reference，禁止作 Amusant live route
