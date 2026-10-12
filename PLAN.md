# Project Amusant — Execution Plan

> 更新：2026-10-11
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
| P4 | Two-ECS software integration / AWS staging | 待重估 | P4-A1 live one-off PASS (2026-10-12). ADR-0002 accepted. Store-ready Relay code (sqlite/dynamodb switch, boto3, Dockerfile/taskdef) is offline; no table. Next live envelope is write/read-after-stop. A2 24/7 is not automatic. |
| H0 | Hardware UX Gate | P4 後估算 | 比較專用裝置與最佳化手機流程；只有明顯降低摩擦才繼續。 |
| H1 | Hardware integration | H0 通過後估算 | 使用 Kai-derived M5StickS3 firmware 驗證裝置音訊、PTT、網路與可靠性；不修改 Kai 上游，除非另行核准。 |

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
