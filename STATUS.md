# Project Amusant — Status

> 最後更新：2026-10-07
> 規格權威：SPEC.md
> 執行計畫：PLAN.md
> Repo：Private repository; remote is configured locally.

## 目前狀態

**P0 — Foundation：完成。P1 — local mock probe：已實作並啟動；LAN 上另一台主機的 Kai-sim client smoke 尚待執行。** 尚未接 Live provider、OpenClaw、AWS 或硬體。

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
- 驗證結果：4 個 unit/protocol tests PASS；private host 本機透過 LAN IP 做 synthetic WebSocket smoke PASS，收到 state/card/caption/turn_complete 與 6,240 bytes audio。這不是 kai-sim client 或真實麥克風的 runtime PASS。
- Kai source-level 比對（2026-10-07）：以 pinned Kai v0.12.0 commit `1681fb3c11acd28525d8987bba4b9bf4bc2a288f` 的 `sim_device.py`／`session.py` 為準；mock 覆蓋 P1 所需 Bearer auth、`/ws`、hello/PTT、16 kHz PCM input、24 kHz PCM output 與 card/caption。此為程式碼比對，未由真實 kai-sim 執行證實。
- 界線：mock 是 P1 test probe，非 Kai fork、非完整 Kai relay，也非 production Amusant Relay；未實作 Opus/TLS-PSK、Live provider、Hermes/OpenClaw。
- 效能：目前 `capture_ms` 量的是 PTT 按住時間，不是 relay latency；PTT release→first audio 與完整回應時間尚未實測。
- 目前 mock 在 PTT down 即送 `thinking`，與 Kai relay 的狀態時序有差異；不是 Kai-sim 音訊路徑已驗證的證據。
- Relay 目前由背景 exec process 執行，非 systemd；沒有修改 firewall、port-forward 或 OpenClaw Gateway 綁定。重開機後需手動重啟。

## 等待 LAN kai-sim client

- 用可信 LAN 上、有麥克風／喇叭的主機跑 kai-sim；OS 尚待確認。
- 目標連線：LAN client → private mock host 的 port 8765；本次僅測同一 LAN，不依賴外部 tunnel。
- 待驗：client handshake、真實 PTT/audio capture、回傳聲音播放及同一 LAN 的 RTT。若 relay port 被防火牆擋住，先回報，不自行改 firewall。

## P1 probe 定位

- `relay/` 是可重現的 P1 協定／音訊測試工具；此狀態不代表 P1 runtime 驗收完成。
- P1 尚待 LAN 主機以真實 Kai-sim、麥克風與喇叭連線；RTT/回應延遲仍未量測。

## Privacy de-identification

- Current Markdown generalizes user-specific examples, account identifiers, exact private host/IP details, and work/network context while preserving project requirements.
- Existing Git history has not been rewritten; prior commit versions remain in repository history.
