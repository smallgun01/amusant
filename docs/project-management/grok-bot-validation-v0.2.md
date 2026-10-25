# Amusant Grok Bot 驗證提案 v0.2

> 階段依存與 Owner G1 驗收已由 [v0.3 更新](grok-bot-validation-v0.3.md) 覆蓋。v0.2 仍是未被 v0.3 明確替換之契約、傳輸、安全與測試範圍的基準。

日期：2026-10-25（Asia/Taipei）
Owner：Arthur
用途：判斷託管 Grok Bot 是否值得成為 Amusant 主要 Agent 後端。
狀態（v0.2 基準時）：設計與可轉交工作單；v0.2 已納入三個公開 Grok Bot 硬體整合案例；當時尚未執行 Amusant Grok 實測、實作、PR、部署或權限變更。後續狀態以 v0.3 與本 handoff 更新為準。

## 1. 決策與產品假設

完成目前正在收斂的 OCI 實體閉環後，優先驗證 Grok Bot。先證明原生產品的任務價值，再做最小雙向接入，最後才接 Relay／StickS3。

要回答的是：Grok Bot 能否以較少設定、維運與人工補救，完成日常委派，回傳有內容的短摘要，讓 Owner 在原生 App 取得完整成果並接續工作？

通過試驗可支持擴大試用或準備後端替換；不自動授權退休 Hermes。若採用 Grok，應明確停止不再需要的自管 Agent 整合工作，避免永久維護兩套完整平台。

保留 Amusant 的一對一、PTT-first、FAST 即時互動、明確要求才回讀、短摘要與完整成果分流。Agent 的工具、Skills、帳號、記憶與審批由 Owner 在原生產品管理；試驗不建立業務工具清單或代理管理 UI。

## 2. 已核對的基準與既有工作

本次核對 main：73397323412e9c27c6caacc5adbbd1a2b5dd9962。這是 source snapshot，不代表 OCI 的實際部署版本。執行者開工時需更新一次現況；以下不是永久固定的 PR 狀態。

| 項目 | 本次 source 狀態 | 處理方式 |
| --- | --- | --- |
| PR #116 | 已合併；新 Hermes Runs v2-only | 不重做 v1/v2 切換；部署證據另查 |
| PR #118 | 已合併；polling／terminal recovery | 保留現有已知 Run GET／STOP 語意 |
| PR #119／ADR-0004 | 已合併；Owner-managed Agent | 延續原生管理與 tool-agnostic 分工 |
| PR #120 | OPEN、Draft；generic delegate_task、agent kind、SQLite v9 | 避免重做通用委派或 ledger migration；不自動中止既有 review |
| Issue #46／#62 | 實體 spoken／OCI E2E 追蹤仍開啟 | G0 引用帶日期的驗收紀錄；不以 issue 摘要或 merged 推定 PASS |
| Issue #105 | bounded universal results 與完整內容交付 | 沿用短呈現規格；Grok App 成果不是 Facade committed Artifact |
| Issue #56／#58 | Hermes native integration／recovery | Grok spike 不重寫 |
| Issue #101／Draft PR #102 | bootstrap 工作 | 不是本試驗前置需求 |

本次 open issue／PR 清單未見獨立 Grok 驗證工作。未建立新 issue 或 PR。後續若需追蹤，先重查是否已有同一工作，再由收到執行指示的 coding agent 建立或沿用一個 Draft，不拆成多條功能線。

既有 handoff 仍有效：Codex 的既有 Hermes C2 與 OpenClaw 的既有 Hermes O1 不因本提案自動撤銷或取得新操作權限。Grok 工作與既有任務使用分離的工作區／分支，避免覆蓋未提交變更。

### 2.1 v0.2 外部參考：三個已公開的 Grok Bot 硬體整合

Grok-C0／Grok-O0 必須把下列 repo 當成 mandatory references，而不是可選背景資料。它們不直接成為 Amusant dependency；要借的是已實測的連線模式、失敗語意與安全邊界。

| Repo | 已公開的模式 | 本提案採用方式 | 不採用／限制 |
| --- | --- | --- | --- |
| `Liu-zhongxian/zongzhu-bot` | ESP32／Xiaozhi backend → Grok Routine Webhook；完成後以 per-task HTTPS callback 或檔案回傳；submission accepted 與 result delivered 分開 | 借用 acceptance_unknown、單筆 result token、hash-at-rest、write-once result、atomic write、late result、approval hint 與「不盲目重送原任務」 | 不複製其完整 Xiaozhi backend、ASR/TTS 或 2000 字裝置長回覆模型 |
| `adidshaft/grok-gadgets` + gateway | Grok Bot 從 Mac／iPhone 透過 **Remote HTTPS MCP + OAuth** 呼叫 owner-run gateway，再到實體 ESP32；remote tool 與 device port 分離 | **升為 G3 primary return/control integration reference**：Owner-run HTTPS MCP、OAuth、最小工具面、per-owner scope、診斷與 raw device API 分離 | 不把它的 gadget command schema 當 Amusant task schema；不公開 Relay／Facade／SQLite／device raw port |
| `JosssphZhou/groki-bot` | M5Stack/Gateway 與 Grok Bot hand-off；async completion 再回 voice；另有 Grok Desktop 0.71.2 internal API/login bridge | 借用 async completion、late/stale/replay 過濾、completion ≠ 新 user authorization、speech interruption ≠ cancel remote task | **禁止**把 desktop credential scraping、Keychain 解密、`api2.cursor.sh` 私有 RPC、hard-coded client version 或 undocumented native API 變成 Amusant production path |

由這三個案例得到的 v0.2 決策：

1. **Primary path：Routine Webhook 進件 + Owner-run Remote HTTPS MCP/OAuth 回報。** 第一個 MCP 僅暴露 `submit_result`。
2. **Fallback path：per-task HTTPS callback。** 只有在 O0/O2 實測確認 Remote MCP 在 Owner 帳號、Routine context 或網路條件下被阻擋後，才提出啟用；沿用同一份 result domain model，不另建第二套結果語意。
3. **Internal desktop API path：reference only。** 不作為 G2/G3 的成功條件，也不建立 credential extractor、gbot private RPC adapter 或版本追蹤負擔。
4. 無論 primary 或 fallback，`task_id` 在送件前由 Amusant 產生；result receipt 與 native Run state 分離；結果通知不得自行觸發下一筆委派。

## 3. 授權與順序

當前可做：設計、唯讀 source／證據盤點、操作計畫、案例與離線測試設計。
當前真實 Grok 呼叫預算：0；新增部署與權限變更：0。

Owner 後續轉交並指示啟動相應階段後，執行已涵蓋的工作；同一個已核可範圍不逐次重問。仍需區分 repo/offline 工作、原生產品操作，以及主機／網路變更。

| 階段 | 主責 | 輸入與產出 | 啟動／通關條件 |
| --- | --- | --- | --- |
| G0 基線與唯讀準備 | OpenClaw＋Owner；Codex 做 source map | 既有 OCI deployed／實體 E2E 矩陣、Grok 最小 file plan、實驗環境計畫 | 本輪即可準備；新功能實作等既有閉環證據 PASS |
| G1 原生產品適配 | Owner 操作；OpenClaw 收證據 | 查 Issue、法國行程、同成果修改；設定與介入成本 | G0 閉環已確認，原生測試範圍／額度已有啟動指示 |
| G2 隔離程式與離線驗證 | Codex Desktop | 一個 spike、locked dependencies、合約與故障測試、Draft PR | G0、G1 支持繼續，且收到 G2 repo/offline 執行指示 |
| G3 真實雙向接入 | OpenClaw＋Owner | Webhook → Bot → **Remote MCP/OAuth（primary）**；必要時 per-task HTTPS callback（fallback）→ 獨立紀錄；App 成果接續 | G2 reviewed commit；具體環境／入口／額度／案例已核可 |
| G4 裝置接入 | Codex 設計／實作；OpenClaw＋Owner 驗收 | Relay／Inbox／S3 的條件式最小變更與實體證據 | G3 有產品價值；先明定新的通道完成／取消語意，再執行 |
| G5 路線決策 | Owner，ChatGPT 評審 | NO-GO／候選／擴大試用／準備替換 | 依實測效益與成本，另決定後端切換 |

G0 的 E2E gate 是目前收斂中的實體委派與回讀閉環：指定 bundle 下 FAST 可用，Slow 有實質短結果入 Inbox，新 PTT 明確要求後正確回讀，且沒有未處理的測試任務卡住通道。沿用既有核可驗收與證據，不額外創造一套 gate。這不要求先關閉 #62 所有長期營運子項。

G0 未通過時仍可完成兩個 agent 的唯讀準備；不要以新 Grok spike 改變正在驗收的系統。

## 4. 第一個程式驗證的邊界

第一個 spike 只有：

1. 一個一般委派 Routine 的 payload／送件模型。
2. 一個符合 MCP 協定的 `submit_result` 工具與 transport-neutral result receiver。**Remote HTTPS MCP/OAuth 是正式 primary path。**
3. 一個送件／查閱／人工結案 CLI。
4. 一份獨立 SQLite 實驗紀錄。
5. per-task HTTPS callback 只保留為 fallback contract／focused tests；除非 Grok-O0/O2 證明 primary path 被實際阻擋且 Owner 核可，不先建立第二個 live ingress。

不啟動持續輪詢 worker、不建立工作佇列、不建立新的 Cron 或 approval engine，不讓 Grok 假裝成 Hermes executor。

建議新目錄：spikes/005-grok-bot-validation/。這是擬議路徑，尚未建立。可包含 README.md、獨立 pyproject.toml／lock、少量程式模組、tests/、.gitignore；檔名由 Codex 依最小實作收斂。

實驗資料、secret files、完整指令、native locator 與原始回應均不進 git。公開證據只保留去識別的 case alias、階段結果、時間與長度等必要欄位。

### 為何先獨立

目前 application／router／store 的 executor kind 只接受既有後端，dispatcher 的 deferred／recovery／STOP 帶有明確 Hermes 語意。Hermes v2 parser 也要求受信任的 completed 與 Facade artifact 格式。直接塞入 Grok callback 會把「收到結果」偽裝成「平台已確認終止」。（來源 R4、R5）

因此 G2 不修改：

- relay/application.py、relay/dispatcher.py、relay/task_store.py 或既有 schema。
- production Facade、OCI Compose、Hermes config、網路或 secret。
- Live provider、裝置韌體、配網或既有 Inbox。
- 任何普通業務工具或 Agent 權限。
- Grok Desktop credential scraping、macOS Keychain token 解密、`sand-secrets.json`、`api2.cursor.sh` 私有 RPC、hard-coded Grok Bot client version 或其他 undocumented internal API。

spike 可比照現有 speak/card 規格與可直接引用的常數；不得為了重用 parser 而填入假的 Hermes execution_status 或 art_ reference。

### 實驗環境

優先選 Owner 控制的非特權開發環境，使用隔離目錄、venv 與資料檔。OpenClaw 在 G0 提出具體 host、port、HTTPS 路徑、認證注入及 rollback；不能假設現有 N100／Tunnel 可以直接新增路由。

G2 僅 loopback 模擬。**G3 預設採 Remote MCP。** 入口需從平台 connector 可達，且只暴露最小 MCP 端點；不公開現有 Hermes／Facade／Relay 管理 API。Remote MCP 的流量來自平台伺服器，不可只測 Bot 雲端電腦的 curl 就判定可達。Grok Gadgets 已公開 owner-run tunnel + OAuth + Grok Bot Mac/iPhone 實測，可作為 transport/security reference，但 Amusant 必須自行驗證自己的 Owner 帳號與 Routine context。（來源 S3、X2）

官方也有 Command 類 MCP，但本輪 primary 固定為 Remote HTTPS MCP/OAuth；遇阻先報告精確原因，不自動加 VPN、開全網路或改用 Groki 的 desktop internal API。若 blocker 屬 Owner 帳號／Routine 工具可見性／connector reachability，才依本提案啟用 Zongzhu 式 per-task HTTPS callback fallback。（來源 S2、X1、X2、X3）

## 5. 最小回報契約

這是隔離試驗的 Grok Report v0，不修改 Universal Result Envelope v2，也不宣稱是 xAI 官方格式。

### 5.1 Amusant → Routine

~~~json
{
  "schema_version": "amusant-grok-pilot-0",
  "task_id": "<由實驗程式產生的 UUID>",
  "result_token": "<單筆回報用途的隨機 capability>",
  "task_label": "G3-D1",
  "instruction": "<交辦內容及必要上下文>"
}
~~~

task_id 在送 POST 前產生並持久化。task_label 只方便 Owner 在 App 辨認。instruction 以現有 task 的 4,096 字元上限為起點，不傳整份語音歷史或長報告。

result_token 只授權這一筆結果回報；不授權帳號、主機或外部業務操作。server 保存其 hash；sender 在本次送件程序中保留原值以組成 webhook body。不可把 token 寫進一般日誌、公開 evidence 或最終內容。它可能出現在 Bot 的私有任務脈絡，不能宣稱是平台簽章或對模型不可見的秘密。

### 5.2 Primary：Bot → Remote MCP `submit_result`

~~~json
{
  "schema_version": "amusant-grok-pilot-0",
  "task_id": "<原值>",
  "result_token": "<原值>",
  "reported_outcome": "completed",
  "speak": "有實際結論的短摘要。",
  "card": {
    "title": "工作結果",
    "lines": ["第一個重點", "第二個重點"]
  },
  "native_result_locator": null
}
~~~

reported_outcome 僅允許 completed／not_completed，語意為 Agent 自述。它不會改寫 native Run state，也不是 Action 成功證明。

native_result_locator 是可選的原生工作／成果定位資訊：真實可用連結，或 Owner 可辨識的對話／成果名稱。上限建議 512 字元。沒有可用 deep link 時可為 null，另由 task_label 找到 App 工作。它不是下載承諾、Facade artifact_ref 或任意檔案存取指令。

展示上限沿用現有設計：speak 240 字元、title 80、lines 1–4 行且每行 120 字元；整份 tool arguments 的 UTF-8 JSON 上限 3,000 bytes。拒絕未知欄位、控制字元、型別或長度錯誤；回可理解的 validation error，讓 Bot 只修正回報，不重做任務。（來源 R5）

MCP HTTP／JSON-RPC 包裝另設有界 body cap，初值建議 16 KiB；在讀取過程限制大小，不等整份任意 body 進記憶體後才驗證。MCP 協定選用受維護 SDK 並鎖版，不自行把 JSON REST endpoint 取名 MCP。

### 5.3 Fallback：per-task HTTPS callback

只有在 Remote MCP/OAuth primary path 被實際驗證阻擋後才啟用 live fallback。它使用和 `submit_result` 相同的 schema、task-scoped capability、write-once receipt 與 SQLite transaction，不建立第二套任務狀態機。

參考 Zongzhu 的已公開模式：每筆 task 產生隨機 result token，server 僅保存 hash；callback 驗證 task/token、期限、size 與 conflict；成功持久化後才 ACK；timeout 或原始 Webhook acceptance_unknown 不會重送原任務。HTTPS callback credential 與 Routine ingress key、MCP OAuth credential 分離。

Fallback 不得取得 Relay DB、Facade writer、Hermes credential 或任意 host filesystem 權限。若啟用，URL/token 只放在該筆私有任務脈絡，不寫一般 log／git。

### 5.4 接收端回 ACK

~~~json
{
  "accepted": true,
  "task_id": "<匹配的 task_id>",
  "receipt_id": "<server 產生的不透明 ID>",
  "received_at": "<UTC ISO 8601>",
  "duplicate": false
}
~~~

receipt 只證明該回報已通過接入驗證並持久保存。MCP HTTP 200 不等於工具 accepted；呼叫方需檢查 tool result／error。

不先實作 progress、needs_approval 或多階段事件流。原生 runtime 可能先暫停，模型沒有機會回報等待批准；缺 callback 只能表示未收到結果。（來源 S4）

## 6. 認證、持久化與去重

### 認證

- Routine ingress key 與 MCP credential 分開；不重用 device、Hermes writer 或 Facade key。
- MCP credential 綁定一個實驗 Owner，只能使用結果回報工具；從安全設定注入，不放 CLI arguments 或一般 Bot 對話。
- Owner 身分由認證與預先保存的 task 決定，不接受模型提供 owner_id。
- task_id 為關聯碼，另驗證 task-scoped result_token。MCP session ID 不當作 Bot Run ID。
- 完整成果參照只作資料，不自動抓 URL、讀本機路徑或執行內容。

### 分開保存的事實

| 維度 | 最小狀態／欄位 |
| --- | --- |
| 提交 | prepared／sending／accepted／acceptance_unknown／rejected |
| 回報 | waiting／received；immutable report、digest、receipt |
| 觀察旗標 | overdue、conflict、late |
| 原生執行 | 預設 unknown；Owner 在 App 的觀察另記來源、時間與對應線索 |
| 人工實驗結案 | open／closed、結案理由、觀察者；不是 native cancelled |

送件順序：產生 ID／token → durable prepared → durable sending → 一次 POST → 更新 acceptance。程式在 sending 後中斷或 HTTP timeout，重開後保持 acceptance_unknown，不重新 POST。prepared 尚未送出的工作可人工放棄；MVP 不實作自動恢復送件。

200 表示官方入口已接受並啟動；若回應有 runUuid，將它當 opaque optional metadata，不要求存在、不依賴其 schema、不推導查詢或取消 API。（來源 S1）

完整明確拒絕與 transport ambiguity 分開記錄；即使收到非 200，本輪也不自動重試。送件 HTTP client 關閉所有自動 redirect，包括同來源 307／308，以免重送 POST 或洩漏認證。代理錯誤、截斷、timeout 均不能猜測「平台一定沒啟動」。

result report（Remote MCP tool call 或 HTTPS fallback callback）可能早於原始 Webhook POST 回覆，故以預先存在的 task 和 token／Owner credential 驗證；稍後 HTTP 回覆不得把 received 改回 waiting。若回報已收到但 acceptance 仍不明，保留兩個事實。

第一份有效回報與 receipt 在同一 SQLite transaction 持久化後才 ACK。相同 task／相同正規化內容重送，回同一 receipt；JSON key 順序或空白不影響 digest。不同內容回 conflict，不覆蓋既有結果。認證 token 不納入內容 digest。

重啟後 duplicate 判斷仍有效。寫入失敗不能回 accepted=true。晚到回報保存為 late，不復活人工結案、不綁到下一筆工作。人工結案本身不撤銷結果 token：在實驗保留期間仍可接受已認證的晚到證據；試驗清理或 credential／token 撤銷後則拒絕晚到回報，不重建 task。限量紀錄異常，不能讓錯誤流量造成無界資料增長。

原生 App 在交付後修改文件，不覆寫已接受的 immutable report／receipt，也不要求再次用原 task_id 提交不同摘要。原摘要描述當次交付，未自動同步後續 App 版本。若未來從裝置交辦修訂，使用新的 task_id 與必要工作參照；本輪不建立多版本事件同步。

**結果去重不提供平台執行去重。** 不因缺回報、timeout 或重啟重新觸發原業務任務，也不自動交給 Hermes 再做一次。

## 7. Single-flight、取消與 G4 的決策點

G2／G3 harness 一次只進行一件實驗。Owner 核對原生工作後人工結案再做下一件；這是實驗控管，不修改目前 Amusant Single-flight。

三件事獨立呈現：

1. 回報已持久保存。
2. Agent 宣稱工作完成或未完成。
3. 原生 App 顯示 Run 已結束，或程式尚無法確認。

本地停止等待不等於取消遠端；不提供假的 stop webhook，不回傳「已取消」來釋放未知工作。App Stop 也不代表已完成外部動作被撤銷。

G4 實作前需評審精確差異：

- 是否採納「收到最後交付＝Amusant 通道完成」的 Grok 專用語意，並明示 native Run 仍 unknown。
- 如未採納，維持需要 Owner 原生確認的受控實驗；不能無聲降低原有 terminal-evidence 規則。
- 如何新增真正的 backend identity，與 #120 generic delegation 相容。
- 如需 schema 變更，精確 migration／rollback 另審；不冒充 hermes_runs／openab_acp。
- 當次裝置只連一個 active Relay；不增加自動跨後端 fallback。
- 收到結果不自動開口；沿用 Inbox、新 PTT、tool-disabled spoken session 等已驗收呈現方式。
- 「在 App 接續同一成果」為本輪必測；「下一次裝置直接續接同一 native thread」若尚未實作，單獨列限制。

G4 是條件式後續工作，不夾帶在 G2 spike PR。

## 8. 產品案例、配額與證據

### G1 原生案例

最多 4 個使用者測試 turn：

1. 指定 repo 的 open issue 數量與 1–2 個重點。
2. 第二次相同類型查詢，驗證無需修 prompt／重設工具。
3. 建立兩週法國行程完整文件。
4. 對同一成果要求「巴黎減一天，調整後續安排」。

Issue 查詢必須排除 PR，記錄查詢條件與時間，與相近時間的來源核對，不把 repo 的混合 Issue/PR 計數直接當答案。資料變動造成差異時先核對時點。

旅行案例固定為合成測試條件，不寫成 Owner 的真實家庭偏好：例如 2027-05-10 起、14 天、2 位成人、巴黎進里昂出、城際鐵路優先；只規劃，不預訂、不付款。產出日期／天數一致的完整文件、可辨識的修訂關係。摘要應交代路線或關鍵取捨，不能只有「幫你做好了」。

G1 通過：答案有用且可核對、完整成果可取得、後續修改能對到原工作，Owner 認為設定與必要人工介入可接受。無須先證明所有 Agent 功能。

### G3／G4 一輪配額

建議初始 envelope：

- 原生 App 合計最多 8 個使用者 turn：G1 測試 4、接入設定最多 2、G3 成果接續最多 2。
- Webhook POST 合計最多 8 次，包括失敗或 acceptance_unknown；G3 最多 6，預留 G4 最多 2。
- 不自動重送 POST；資料與 screenshot 補錄不重做工作。
- 一個 run 可能含多次模型／工具呼叫，以上不是費用保證。
- 預設新增付費支出上限為 0；僅在 Owner 明確同意使用的既有額度內測試，不自行購買、升級或開啟額外計費。
- 啟動紀錄需寫明既有方案、可觀察額度、上限、案例、觀察窗口與原生停止方式。未填妥時維持設計／離線準備。
- 超過上限前先交現有證據與尚未解決的問題，不以無限重試取得 PASS。

| 案例 | 數量上限 | 預期證據 |
| --- | --- | --- |
| G3-Q：一般查詢 | 3 次 | 同一 Routine／工具契約，正確短結果；至少一個類型重複成功 |
| G3-D：完整行程 | 1 次 | 原生文件可開啟；callback 短摘要；App 對同成果修改 |
| G3-R：回報暫時失敗 | 1 次 | 僅對測試回報路徑注入一次可恢復失敗；觀察重試／回報；不重做業務工作 |
| G3-A：原生批准觀察 | 1 次 | 使用預先定義可安全拒絕的原生步驟；觀察 UI／是否有 callback |
| G4-Q／G4-D：實體裝置 | 2 次 | 已核可新案例由 S3 發起，短結果入 Inbox，明確要求後語音回讀 |

G3-A 如無法在既有授權範圍安排，不為測試開新權限；記 NOT TESTED，讓決策保留限制。G3-R 不關閉 production Relay／OCI；主要 ACK-loss／重啟故障以離線模擬完成。G4 延後時不必用完預留額度。

Coding Draft PR、Issue 更新、Cron／Scheduled Action 是後續選配，第一輪不執行外部業務寫入。若追加，必須驗證實際來源記錄，不能把 callback receipt 當成外部 Action Receipt。

### 證據欄位與評分

每個案例記錄：case_id、task alias、source commit、入口、submitted／received 時間、acceptance、report 狀態、receipt、摘要字元／bytes、native outcome 的來源、成果核對、App 接續、介入與用量、判定。

對 Owner 的實測操作分開計數：

- setup：一次性設定。
- evidence_only：為驗收拍照／查狀態；不算日常救援成本。
- workflow_required：完成工作本來就要處理的確認或登入。
- rescue：補送結果、找回遺失工作、修 prompt／修環境。

公開紀錄不含 token、完整 task、私有文件、原生敏感 URL 或完整 run response。原生對照證據由 Owner 私下保存，公開摘要保留可追溯 alias。

每項結論標成 DOC VERIFIED、MOCK PASS/FAIL、CLOUD PASS/FAIL/UNKNOWN、DEVICE PASS/FAIL/UNKNOWN 或 NOT TESTED。未做不是 PASS。原生 App 顯示成功、收到 callback、S3 播放完成分欄，不合成一個 success。

## 9. 最少必要離線驗收

1. Callback 早於 webhook HTTP response，結果正確保留。
2. POST timeout／sending crash 後重啟，送件次數仍為 1；不自動 fallback。
3. 回報 transaction 已 commit 但 ACK 遺失，重送同內容回同 receipt。
4. 同 task 衝突內容拒絕覆寫；晚到回報不復活已結案案例。
5. 錯 credential／Owner／task／token 不寫入合法結果。
6. malformed、未知欄位、控制字元、超長 Unicode／UTF-8 aggregate 拒絕，不截斷長報告充當摘要。
7. storage failure 不回 accepted；已保存結果跨重啟仍可辨認。
8. overdue／Agent completed 不被轉換成 native terminal 或 cancelled。
9. native locator 不觸發 URL fetch、檔案讀取或執行。
10. 真正 MCP initialize／tools/list／tools/call 與工具 error 有協定測試；不是只有 Python 函式單元測試。

以上驗證跨越實際風險，不要求建立大型測試矩陣。

CI 沿用既有成本政策：純 Markdown 為輕量檢查；Draft 的 heavy jobs 會 skip；非文件 Ready PR 與 main push 仍觸發既有 heavy gates。spike 新測試不在目前 discovery 範圍，若要 merge，需在既有 offline job 增加一個明確步驟。不可把 Draft skipped 宣稱通過，也不可為省分鐘繞過 Ready gate。（來源 R6）

## 10. 通關、停止與路線決策

### 支持進入 G4

- G1 原生產品價值成立。
- G3 至少有兩筆查詢及一筆文件的正常短回程，含重複類型成功；所有嘗試都列出，失敗不被丟掉。
- 文件可取得且在 App 成功接續。
- 關聯、持久化、去重及未知狀態的離線不變條件全通過。
- 回報與原生審批的摩擦可被理解；不需要每類任務加專用程式。
- Owner 認為設定／維護／救援負擔有足夠優勢。

這是小樣本可行性判斷，不宣稱可靠度百分比。

### 支持擴大使用

再加上 G4 真實裝置閉環，以及 Owner 接受原生 App 承擔審批、取消與詳細工作。仍須列出 native Run 控制與跨通道接續限制。正式切換時另列應退休的 Hermes 元件、保留資料與 rollback；不先並行建立兩套完整產品。

本輪證據僅覆蓋查詢、文件與原生成果接續。若正式替換所承諾的能力包含 Action、Coding 或 Cron，須另有各自的真實成果與權限／交付證據；不得用本輪 PASS 代替那些能力的驗收。這不增加本輪案例。

### 停止當前範圍

- 任務錯綁、錯誤回報成功／取消、未授權副作用或敏感資料洩漏。
- 必須擴大主機權限、修改正在驗收的 OCI、逐工具新增 Amusant 業務邏輯才能推進。
- 原生任務與成果接續本身沒有價值，或頻繁人工救援吃掉便利性。
- 接入需要全新 agent framework、queue、廣泛 schema 重構才可能試第一個案例。
- 到達預先設定的呼叫／用量上限。

停止是停止新增實驗；已在平台執行的工作須在 App 如實處理，不能宣稱本地退出已停止遠端。

## 11. Coding agents 的交付與協作

Codex Desktop：

- Grok-C0 現在：更新 source map、#120 overlap、spike file plan 與離線契約；不寫 runtime。
- Grok-C1 G2：隔離 spike、focused tests、Draft PR、精確 CLI／README。
- Grok-C2 G4 前：只根據 G3 發現提出 Relay 接入差異與 lifecycle 決策，經指示後才實作。
- 不做 OCI、帳號設定、公開入口、live provider 或 merge。

OpenClaw：

- Grok-O0 現在：既有 E2E 證據矩陣＋具體實驗環境與 live envelope 計畫。
- Grok-O1 G1：在已啟動範圍內協助 Owner 原生測試並記錄產品成本。
- Grok-O2 G3：執行 reviewed commit 與指定實驗端點；收真實證據。
- Grok-O3 G4：使用 reviewed bundle 配合 Owner 實體操作。
- 不在 live host 修 code，不自動 sudo／Docker、開通權限或重播工作。

Owner 負責原生登入／帳號連接／審批、案例真實價值判斷、PTT、實測啟動範圍及最後採用決定。已授權範圍內持續執行，只有具體新增權限／付費／部署或 scope 變更才帶精確方案回報。

兩份可轉交工作單另附；它們是任務指引，不是已執行證據。

## 12. Routine 固定指令草案

以下供 G3 啟動時由 Owner 放入一個專用 Routine；本文件不建立 Routine。

~~~text
這個 Routine 接收 Amusant 的一般委派。JSON body 內的 instruction 是本次工作；
task_id、result_token 與 task_label 是回報對應資料，請保持原值。

使用你已具備、且 Owner 已授權的工具與帳號完成工作。
工作文字不授權新權限。需要登入、批准或補充資料時依原生流程處理。
不要為了回報成功而跳過批准、捏造工具結果或重做可能已完成的外部操作。

在原生對話中保留可閱讀的完整成果，讓 Owner 用 task_label 找到工作並繼續修改。
不把完整報告、工具紀錄、secret 或 result_token 放進短摘要。

本次工作能完成或已確定無法完成時，呼叫 amusant 的 submit_result：
帶回原始 task_id、result_token、reported_outcome、有實際結論的 speak/card，
以及真實可用的 native_result_locator；沒有可用定位資訊時用 null。
遵守工具 schema 的長度限制。completed 只描述你根據實際結果判斷的業務完成，
不代表向 Amusant 證明平台 Run 已終止。

工具 accepted=true 才表示回報已保存；遇到 validation error 只修正回報。
收到 accepted=true 後本次 task 的回報已完成；後續 Owner 在原生 App 修改成果，
不要再以原 task_id 提交不同的 final report。
若回報傳輸失敗，可以對同一份結果再嘗試一次，不重新執行原業務工作。
仍無法回報時，在原生對話清楚說明回報未確認，由 Owner 查看。
不要另觸發 webhook，不要把整個 Bot 的其他原生對話改成這個回報格式。
~~~

## 13. 來源與閱讀順序

Repo 文件與 code 是指定 snapshot；官方產品文件為 2026-10-25 查核。產品文件不等於本帳號已啟用或實測通過。

- [R1：SPEC v0.6](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/SPEC.md)
- [R2：ADR-0004](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/docs/adr/0004-owner-managed-hermes.md)
- [R3：PR #120 generic delegation](https://github.com/smallgun01/amusant/pull/120)
- [R4：application](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/relay/application.py)、[dispatcher](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/relay/dispatcher.py)、[task_store](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/relay/task_store.py)
- [R5：Hermes v2 bounded result](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/relay/hermes_result.py)
- [R6：CI](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/.github/workflows/ci.yml)
- [R7：既有 Codex handoff](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/docs/handoffs/20261025-codex.md)、[OpenClaw handoff](https://github.com/smallgun01/amusant/blob/73397323412e9c27c6caacc5adbbd1a2b5dd9962/docs/handoffs/20261025-openclaw.md)
- [R8：#46](https://github.com/smallgun01/amusant/issues/46)、[#62](https://github.com/smallgun01/amusant/issues/62)、[#105](https://github.com/smallgun01/amusant/issues/105)
- [S1：官方 Routine webhook](https://cursor.com/help/grok-bot/routines)
- [S2：官方 MCP 設定類型](https://docs.x.ai/grok-bot/team-bots)
- [S3：Remote MCP 網路來源](https://docs.x.ai/grok-bot/security)
- [S4：原生審批](https://docs.x.ai/grok-bot/approvals-security-and-privacy)
- [S5：原生完整成果與修訂](https://docs.x.ai/grok-bot/files-and-results)
- [S6：Skills／Routines](https://docs.x.ai/grok-bot/skills-routines-and-automations)
- [X1：Zongzhu Bot](https://github.com/Liu-zhongxian/zongzhu-bot) — `docs/GUIDE.md`、`grokBotHandle.py`、`grokBotReply.py`
- [X2：Grok Gadgets](https://github.com/adidshaft/grok-gadgets) 與 [gateway](https://github.com/adidshaft/grok-gadgets-gateway) — Remote HTTPS MCP/OAuth、command status、owner-run tunnel、verification records
- [X3：Groki Bot](https://github.com/JosssphZhou/groki-bot) — `docs/architecture.md`、`gateway/GBOT_VOICE_VALIDATION.md`、`gbot_voice_session_bridge.mjs`；internal API 僅供風險/UX 參考
