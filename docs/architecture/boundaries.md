---
title: Rust ↔ Python 境界と所有権
status: draft
authored: 2026-05-08
sources:
  - engine-client/src/dto.rs
  - engine-client/src/lib.rs
  - python/engine/schemas.py
  - python/engine/server_grpc.py
  - docs/architecture/modules/data-engine.md
  - docs/specs/data-engine.md
---

# Rust ↔ Python 境界と所有権

## 設計原則

Rust と Python の境界は **データ所有権・状態管理責務・障害境界** の三つで切る。

- **Python が真実 (SoT)**: market data・注文ライフサイクル・venue session 状態は Python 側が所有する。Rust はキャッシュとレンダリングのみ。
- **Rust は接続管理者**: Python プロセスの spawn / restart / health monitoring は `engine-client/src/process.rs` の `ProcessManager` が担う。
- **障害は IPC で隔離**: Python プロセスがクラッシュしても Rust GUI は生存し、auto-restart で復帰する。逆方向（Rust クラッシュ）も Python は単独動作可能。

## 所有権マップ

| データ / 状態 | 所有者 | Rust 側の扱い |
|---|---|---|
| 板（depth book） | Python (`exchange/<venue>/stream.rs` の後継) | `DepthTracker` がスナップショット + diff を再構築 |
| kline / trade history | Python | レンダリング用キャッシュ |
| 注文セッション (`OrderSessionState`) | Python `order_router.py` | `engine-client/src/order_session_state.rs` がミラー |
| venue capabilities | Python | `VenueCapsStore` がキャッシュ（`engine-client/src/venue_caps.rs`） |
| ティッカーメタ（tick size 等） | Python | `TickerMetaMap`（`engine-client/src/backend.rs`） |
| UI レイアウト / ペイン状態 | Rust | Python は関与しない |
| 認証トークン（OS keyring） | Rust | venue ログインは Rust 起点で Python に注入 |

## IPC 境界の不変条件

- **handshake 順序**: Rust は `Ready` 受領前にマーケットデータ系コマンドを送らない（[data-engine.md §4.5](../specs/data-engine.md)）
- **schema_major 不一致は致命的**: 接続拒否。`schema_minor` 差は警告のみ
- **engine_session_id 切替**: Python 再起動を Rust が検知したら全板・未確定 kline を破棄
- **silent failure 禁止**: handshake 15s timeout / EngineBusy unicast / 全断時の sticky error を全経路で実装

## DTO の二重定義と整合性検証

Rust 側 `engine-client/src/dto.rs` と Python 側 `python/engine/schemas.py` は同じ
スキーマを別言語で表現する。両者の整合は `scripts/check_schema_parity.py` が CI で
検証する。詳細は [ipc-schema.md](ipc-schema.md) と
[../reference/ipc-protocol.md](../reference/ipc-protocol.md) を参照。
