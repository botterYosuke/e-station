---
title: "replay モード仕様 (data-engine 側)"
status: migrated
migrated_from:
  - docs/✅python-data-engine/spec.md#replay
source_commit: 9f8b91b
---

> **Note**: バックテスト時の replay 仕様（NautilusTrader を使ったヒストリカル replay）は [`specs/backtest.md`](./backtest.md) を参照。本ファイルは data-engine の IPC ハンドシェイクで `mode == "replay"` のときの動作仕様だけを切り出している。data-engine 全般の IPC は [`specs/data-engine.md`](./data-engine.md) を参照。

# replay モード（data-engine handshake における mode=="replay"）

## 1. mode フィールド（Hello メッセージ、N1.13 追加）

`Hello` メッセージの `mode: "live" | "replay"` フィールドにより、起動時に固定する動作モードを指定する。

- `"replay"` のとき Python は `LoadReplayData` / `StartEngine{engine: Backtest}` / `SetReplaySpeed` を受理し、live venue 購読 (`Subscribe` を `replay` venue 等で投げる) は拒否する。
- `RequestVenueLogin` も replay モードでは `VenueError{code:"mode_mismatch"}` で拒否する（バックテストはブローカー接続を要しないため。2026-04-30 追加）。
- 旧クライアント互換のため省略時は `"live"` にフォールバック。

## 2. 起動フローでの mode チェック

[`specs/data-engine.md` §3.1](./data-engine.md#31-起動フロー外部エンジン自動-attach--spawn-フォールバック) のプローブ手順内で、外部エンジンが `--mode replay` で起動している場合は live クライアントの attach を拒否する／逆も真。`EngineConnection::probe(probe_url, token, mode)` で `mode` を渡し、不一致時は失敗理由を `log::info!` に記録して spawn にフォールバックする。

## 3. venue startup login のスキップ（mode=="replay"）

[`specs/data-engine.md` §4.5 step 4](./data-engine.md#45-起動ハンドシェイク) の Python 側 venue startup login（autonomous Tachibana login）について、**`mode == "replay"` のときはこのステップを skip する**（2026-04-30 追加）。

- バックテストは Tachibana 接続を要さず、`Hello.mode` を見て Python 側 (`server.py::_handle()`) が `_startup_tachibana()` の spawn を抑止する。
- 明示の `RequestVenueLogin` も §4.5 step 1 の通り replay モードでは `mode_mismatch` で拒否されるため、replay セッション中に Tachibana ログインフローが起動する経路は存在しない。

## 4. replay/nautilus エンジンの IPC コマンド・イベント

[`specs/data-engine.md` §4.2](./data-engine.md#42-メッセージ方向) の表より該当行を抜粋:

| 方向 | 種類 |
|---|---|
| Rust → Python（replay / nautilus エンジン） | `StartEngine` / `StopEngine` / `LoadReplayData` / `SetReplaySpeed` |
| Python → Rust（replay / nautilus エンジン） | `EngineStarted` / `EngineStopped` / `ReplayDataLoaded` / `PositionOpened` / `PositionClosed` / `ExecutionMarker` / `StrategySignal` / `ReplayBuyingPower` / `MarketPriceResponse` / `MarketPriceHistoryResponse` |

## 5. multi-client lifecycle（Phase 8.1b 以降）

replay/live の attach mode 採用に伴い追加されたイベント（[`specs/data-engine.md` §4.5.2](./data-engine.md#452-既存接続の置換半死接続対策) も参照）:

| 方向 | 種類 |
|---|---|
| Python → Rust | `ClientConnected{count}` / `ClientDisconnected{count}` / `EngineBusy{current_state, attempted_command, reason, request_id?}` |

`EngineBusy.current_state` は `ReplayStateName | LiveStateName` の直交 union として model_validator で検証する（R1〜R4 review-fix-loop, 2026-05-04）。

## 6. 関連実装ファイル

- `python/engine/replay_session.py` — `ReplaySession` / `LiveSession` / `_AttachClient`
- `python/engine/server.py` — `ReplayState` / `LiveState` 直交 state machine、`_Broadcaster` multi-client fanout
- `engine-client/src/session_file.rs` — engine-session.json の atomic write / delete
- `src/modal/replay_form.rs` — GUI replay 起動フォーム（Phase 8.1c）
