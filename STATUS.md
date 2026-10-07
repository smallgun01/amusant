# Project Amusant — Status

> 最後更新：2026-10-08
> 規格權威：SPEC.md
> 執行計畫：PLAN.md
> Repo：Private repository; remote is configured locally.

## 目前狀態

**P0 — Foundation：完成。P1 — Kai-sim mock 音訊 smoke：PASS**（跨 LAN PTT／音訊回聲與一次重連通過）。使用者回報 release-to-first-audio 與重連均 <1 s（手動估計、樣本數未記）；尚未接 Live provider、OpenClaw、AWS 或硬體。

## 已確認決策

- Amusant 為獨立維護的 Kai-compatible monorepo；Kai 保持架構與韌體上游。
- 產品範圍只含語音溝通、語音／文字回覆；不含位置、感測器或實體控制。
- 快語音 provider 手動選擇；Gemini Live 為首個 baseline；第二真實 provider 為 optional validation，不是 MVP gate。
- OpenClaw agent 為初期慢任務 backend。
- P0 = Foundation；H0 = Hardware UX Gate。先完成軟體 gate，再進 H0/H1。
- Kai 上游核查基準：v0.12.0，commit 1681fb3c11acd28525d8987bba4b9bf4bc2a288f；MIT License。

## P1 — local mock probe

- 原始碼：relay/mock_relay.py；隔離依賴環境：relay/.venv；websockets 15.0.1。
- Network binding：mock 只綁明確指定的 private LAN 位址；文件不保存實際 IP，連線前於本機確認。
- 認證：mock 要求 Bearer test token；執行時值僅由本機環境提供，不放入程式或 repo；只供可信家庭 LAN 測試。
- 協定：Kai-compatible /ws；16 kHz mono PCM16 PTT input；回傳文字卡片、字幕、短 tone + resampled audio echo；單次 audio 暫存 RAM，無 disk log、無模型/API/OpenClaw calls。
- 單元／合成驗證：4 個 unit/protocol tests PASS；private host synthetic WebSocket smoke PASS，收到 state/card/caption/turn_complete 與 6,240 bytes audio。
- 真實 Kai-sim runtime（2026-10-08）：same-LAN client connected；多次 PTT 收到非零 PCM input，mock 回傳 card/caption/turn_complete 與 tone/echo，使用者確認聽到自身語音回聲；一次 Ctrl+C 後重連成功。使用者手動回報 first-audio 與重連均 <1 s，樣本數／計時精度未記。伺服器未保存音訊、未呼叫外部模型/API；不代表語音理解或 Live latency 通過。
- Kai source-level 比對以 pinned v0.12.0 commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f` 為準；本次 runtime 證據只涵蓋 kai-sim 基本 PCM/PTT/card/caption/tone-echo 閉環，不代表完整 Kai relay 或硬體相容性。
- 界線：mock 是 P1 test probe，非 Kai fork、非完整 Kai relay，也非 production Amusant Relay；未實作 Opus/TLS-PSK、Live provider、Hermes/OpenClaw。
- 效能：目前 `capture_ms` 量的是 PTT 按住時間，不是 relay latency；PTT release→first audio 與完整回應時間尚未實測。
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
