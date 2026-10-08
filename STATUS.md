# Project Amusant — Status

> 最後更新：2026-10-08
> 規格權威：SPEC.md
> 執行計畫：PLAN.md
> Repo：Private repository; remote is configured locally.

## 目前狀態

**P0 — Foundation：完成。P1 — Kai-sim mock 音訊 smoke：PASS。P2 — Fast Voice：進行中**（Gemini adapter、Kai PTT bridge harness 已實作；24 個 root 離線測試與 4 個 P1 relay tests PASS。Gemini Live text-to-speech smoke PASS：轉錄 `Ready.`、非取消 turn_complete、48,000 bytes 24 kHz PCM。真實 Kai-sim mic/PTT → Live → speaker 尚未測）。P1 的 release-to-first-audio 與重連由使用者手動估計均 <1 s，非正式統計；未整合 OpenClaw slow lane、AWS deployment 或硬體。

## 已確認決策

- Amusant 為獨立維護的 Kai-compatible monorepo；Kai 保持架構與韌體上游。
- 產品範圍只含語音溝通、語音／文字回覆；不含位置、感測器或實體控制。
- 快語音 provider 手動選擇；Gemini Live 為首個 baseline；第二真實 provider 為 optional validation，不是 MVP gate。
- OpenClaw agent 為初期慢任務 backend。
- P0 = Foundation；H0 = Hardware UX Gate。先完成軟體 gate，再進 H0/H1。
- Kai 上游核查基準：v0.12.0，commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f；MIT License。

## P2 — Fast Voice（進行中）

- 已新增 `fast_voice/` provider-neutral `LiveProvider` Protocol、provider capability metadata、provider error metadata 與 fail-closed 的明確 provider selection。介面依 SPEC.md §17；不自動選擇／切換，也不做 fallback。
- 已新增 `GeminiLiveProvider` scaffold，使用注入的 client/types 物件呼叫 `connect`、`send_realtime_input`、`receive` 與 `send_tool_response`；另有 `build_live_provider()` 啟動接線，要求明確 `AMUSANT_LIVE_PROVIDER=gemini`，由執行環境注入 `GEMINI_API_KEY`。沒有自動 provider fallback。
- Contract 定義 mono PCM16LE、Kai baseline 16 kHz input／24 kHz output、input/output partial/final transcript、tool call、interruption、turn complete 與 sanitized error events；callback 順序與 cancel 確認語意亦有定義。
- Gemini scaffold 將 PTT barge-in 與 standalone cancellation 分開；基於 Kai source 的 activity-start 行為標示 barge-in，獨立 `cancel_response()` 目前 fail-closed（unsupported），不假稱可取消。
- Amusant 根目錄新增 `pyproject.toml` 與 `uv.lock`：宣告 `google-genai>=2.0`，鎖定 `google-genai==2.29.0`。SDK 只安裝在 N100 的 repo-local `.venv`；全域 Python 與既有 `relay/.venv` 未改。執行前備份：`~/.openclaw/backups/amusant-before-google-genai-2026-10-08.tar.gz`（SHA-256 已核對）。
- Root `uv run --locked python -m unittest discover -s fast_voice/tests -v`：25/25 PASS；含 fake-session、SDK config、PTT bridge fake-provider integration tests。另在 `relay/` 環境執行 P1 suite：4/4 PASS。Fake-provider bridge tests 不是 Kai-sim 實體麥克風或 Live runtime 證據。
- 實際驗證（2026-10-08，使用者核准上限 US$0.50）：從 AWS Secrets Manager 讀取指定 JSON 欄位至程序記憶體，未輸出或保存 key。Models list API 成功認證，首頁列出 62 個 models，包含 Kai baseline `gemini-3.8-live`；這證明 key 可呼叫 metadata endpoint，不代表 Live 權限。
- 前兩次（top-up 前）Live handshakes 在 session 前失敗；一次觀察到 SDK `APIError`／WebSocket close code `1011`。Models list key authentication 通過，但無 request ID 對應後台 429。
- 儲值後第一次 cap-32 text turn 只回 2 bytes，屬無效 audio output；隨後 cap-64 驗收回合 transcript `Ready.`、turn_complete 非取消、48,000 bytes 24 kHz PCM（約 1 秒），符合 text-to-speech smoke 門檻。帳單未獨立查證。
- `relay/live_bridge.py` 將 Kai `/ws` Bearer auth、PTT start/end、16 kHz PCM input、Gemini provider 與 caption/24 kHz PCM/turn_complete 接起；300 ms tap filter、1 MB RAM utterance cap、private/loopback bind、不落盤。25 root tests PASS（含 fake-provider bridge/overflow tests）；另 4 個 P1 relay tests PASS。真實 Kai-sim mic/PTT runtime 仍 pending。
- 後續一次明確驗收（output cap 64，單一文字 prompt `Say exactly the single word READY, in a natural speaking voice, and nothing else.`）：Live session 建立成功；output transcript `Ready.`（SDK 標為非 final），turn_complete 非取消，收到 24 kHz PCM16LE 48,000 bytes（約 1 秒）。依預先訂門檻 transcript contains READY + turn complete + audio >=12,000 bytes，**text-to-speech smoke PASS**。沒有保存音訊或測真實麥克風輸入；實際費用未獨立查證。因 cap 32 與 64 的測試是在 top-up 前後不同時間，不能單獨判定成功是儲值或 token cap 所致。
- 新增 `relay/live_bridge.py`：Kai `/ws` + Bearer token、16 kHz PCM16LE PTT input、300 ms tap filter、RAM-only buffer、Gemini Live adapter、回傳 caption/24 kHz PCM/turn_complete；只允許 loopback/private bind，server tests 使用 fake provider。實際 Kai-sim laptop 測試仍待進行。
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

- `relay/` 是可重現的 P1 協定／音訊測試工具，不是正式 Amusant Relay。
- P1 mock-based Kai-sim smoke 已通過；正式 Relay／provider 的延遲與可靠性仍須按 P2–P4 驗收。

## Privacy de-identification

- Current Markdown generalizes user-specific examples, account identifiers, exact private host/IP details, and work/network context while preserving project requirements.
- Published main history now starts from a de-identified root snapshot; earlier commits are no longer in its ancestry. GitHub caches and existing clones may still retain old versions.
