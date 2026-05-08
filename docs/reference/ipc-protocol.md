---
title: IPC プロトコル契約
status: draft
authored: 2026-05-08
sources:
  - python/engine/schemas.py
  - engine-client/src/dto.rs
  - engine-client/src/grpc_transport.rs
  - python/engine/server_grpc.py
  - proto/engine.proto
  - scripts/check_schema_parity.py
---

# IPC プロトコル契約

## トランスポート

主系統は **gRPC over loopback (127.0.0.1)** である。実装:

- Rust クライアント: `engine-client/src/grpc_transport.rs`（`EngineConnection` 経由で公開）
- Python サーバ: `python/engine/server_grpc.py`
- proto 定義: `proto/engine.proto`（`buf` で codegen）

ループバック以外の interface には bind しない。token mismatch は即切断する。

## 三者対応表

| 層 | 表現 | ファイル |
|---|---|---|
| 言語非依存契約 | protobuf message / service | `proto/engine.proto` |
| Rust 型 | `serde` + `Deserialize` の DTO | `engine-client/src/dto.rs` |
| Python 型 | Pydantic v2 モデル | `python/engine/schemas.py` |

3 者の整合は CI で `scripts/check_schema_parity.py` が検証する。

## コマンド系統（Rust → Python）

主なファミリ:

- **subscribe 系**: `SubscribeDepth` / `SubscribeKline` / `SubscribeTrades` / `Unsubscribe*`
- **fetch 系**: `RequestDepthSnapshot` / `FetchHistoricalKlines`
- **order 系**: `PlaceOrder` / `CancelOrder` / `CancelAll` / `ModifyOrder`
- **session 系**: `Hello` / `RequestVenueLogin` / `LogoutVenue`
- **replay 系**: `LoadReplayData` / `StartReplay` / `StopReplay` / `ForceStopReplay` / `SetReplaySpeed`
- **scenario 系**: `LoadStrategyScenario` / `SaveStrategyScenario`

## イベント系統（Python → Rust）

- **handshake**: `Ready` / `EngineBusy` / `EngineStopped` / `ClientConnected` / `ClientDisconnected`
- **market**: `DepthSnapshot` / `DepthDiff` / `Trade` / `Kline`
- **order**: `OrderUpdate` / `Execution`（`ExecutionMarker.commission` を含む。SCHEMA_MINOR 21〜）
- **session**: `VenueReady` / `VenueError` / `LiveBuyingPower` / `LiveStateName`
- **replay**: `ReplayDataLoaded` / `ReplayStopped` / `ReplayTimeUpdated`
- **strategy**: `StrategyScenarioLoaded`

完全な enum 値とフィールドは `proto/engine.proto` を SoT とする。バージョニング規則は
[../architecture/ipc-schema.md](../architecture/ipc-schema.md)。
