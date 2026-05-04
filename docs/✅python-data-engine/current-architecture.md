# 2026-04-24 時点アーキテクチャスナップショット

調査日: 2026-04-24
対象: e-station (Flowsurface v0.8.7)

> この文書は **2026-04-24 時点の調査スナップショット**。
> Phase 1〜8 の実装完了後（2026-05-03）は現状と大きくズレている。最新の構成は以下を参照：
> - [implementation-plan.md](./implementation-plan.md) — Phase 0〜8 の完了状態
> - [python-helper-direct-api.md](./archive/python-helper-direct-api.md) — Phase 8 実装詳細（✅ 完了）
> - [spec.md](./spec.md) — 現行 IPC 仕様
>
> **主な変更点（Phase 5〜8 + R1〜R4 レビュー反映）**:
> - `exchange/` crate から全取引所コード削除済み（Phase 5）
> - `src/replay_api.rs` / `src/api/` ディレクトリ（HTTP API port 9876）削除済み（Phase 8.3）
> - `python/engine/replay_session.py` 新規追加（`ReplaySession` / `LiveSession` / `_AttachClient`）（Phase 8.1）
> - `engine-client/src/session_file.rs` 新規追加（Phase 8.1b）
> - `python/engine/server.py` が multi-client broadcast / state machine 対応（Phase 8.1b）
> - `SCHEMA_MAJOR=3` / `SCHEMA_MINOR=9`（`ClientConnected` / `ClientDisconnected` / `EngineBusy` イベント追加）
> - **2026-05-04 R1〜R4 review-fix-loop 完了**（commit `cb9207f`）:
>   - 型基盤強化: `AppMode` / `AttemptedCommand` / `ReplayStateName | LiveStateName` / `AUTH_FAILED_CODE` Literal/定数共有 + Rust 側 `PositionType` / `OrderStatus` / `CurrentEngineState` / `AttemptedCommand` enum 化
>   - LiveSession attach mode 本実装（旧 NotImplementedError 廃止、`RequestVenueLogin` ↔ `VenueReady`/`VenueError` wire 待ち合わせ）
>   - silent failure 除去（handshake 15s timeout / EngineBusy unicast + `request_id` フィルタ / 全断時 state リセット / EngineStopped 補完 + 二重送出ガード / sticky error）
>   - Rust 健全化（`#[doc(hidden)] pub` を `engine-client` の `testing` feature gate / `pid_is_live` retry / `spawn_venue_ready_bridge` 単一化）

## 全体構成

```
e-station/
├── src/                  # メインバイナリ (Iced GUI)
├── exchange/             # 取引所アダプタ crate (REST/WS)
├── data/                 # チャート集計・設定 crate
├── python/               # 当時は未使用（現在は engine 実装あり）
├── scripts/              # OS別ビルドスクリプト
├── assets/               # フォント・効果音 (WAV)
└── docs/                 # ドキュメント（本計画含む）
```

Cargo workspace 構成 (`Cargo.toml`): `flowsurface` バイナリ + `flowsurface-exchange` + `flowsurface-data`。

## Rust 側が現在担っている責務

### UI / 描画
- フレームワーク: **Iced 0.14** + `iced_wgpu`、canvas ベースの即時描画。
- マルチウィンドウ／マルチモニタ対応、レイアウト永続化（JSON）。
- 描画パネル: ローソク足 / ヒートマップ / フットプリント / Time & Sales / DOM (ladder) / 比較チャート。
- 効果音 (`rodio`, WAV) によるトレード通知。

### データ取得（本計画で Python に移行する対象）
場所: [`exchange/src/adapter/`](../../exchange/src/adapter/)

- **対応取引所 5 種**: Binance, Bybit, Hyperliquid, OKX, MEXC
- 各取引所ごとに `hub/{venue}/fetch.rs`（REST）と `hub/{venue}/stream.rs`（WebSocket）。
- REST で取得しているもの: ティッカーメタデータ、24h 統計、Kline (OHLCV, 100ms〜1d)、Open Interest、L2 デプススナップショット、ヒストリカル trade（Binance は `data.binance.vision` の bulk ダウンロードも）。
- WebSocket で受信しているもの: trade ストリーム、デプス差分、Kline 更新。
- 補助: `limiter.rs`（取引所別レート制限）、`proxy.rs`（HTTP/SOCKS プロキシ、認証情報は OS keyring）。
- HTTP は `reqwest` + Rustls、WS は `fastwebsockets`、JSON は `sonic-rs`。
- trade バッファは 33.3ms ごとに flush。

### 継続的に REST を叩く機能の棚卸し

IPC 計画で取りこぼしがないよう一覧化する（Python 移管の初期スコープ確認用）:

- **Open Interest**: [`src/chart/indicator/kline/open_interest.rs`](../../src/chart/indicator/kline/open_interest.rs) がインジケータとして継続的に `FetchRange::OpenInterest` を要求。→ MVP 必須。
- **Ticker stats (24h)**: `TickerStats`（[`exchange/src/lib.rs`](../../exchange/src/lib.rs) L640 付近）。現行 `tickers_table.rs` で 24h 変化率・出来高・`mark_price` を表示するため取得。→ MVP 必須（`mark_price` はここに含まれる）。
- **Kline 履歴フェッチ**: [`src/connector/fetcher.rs`](../../src/connector/fetcher.rs) 経由、ユーザー操作時（スクロール・期間変更）。
- **Trade 履歴フェッチ**: 同上。Binance は `data.binance.vision` bulk download も利用。

**独立インジケータが存在しないもの**（現時点でソースを grep して確認済み）:
- Funding rate — インジケータ化されておらず、`TickerStats` にも含まれない。現行で REST を継続要求している経路は無い。将来追加時に IPC スキーマへ追加する。
- Liquidations — 同上、現行に継続要求経路なし。

### データフロー（Phase 0.5 以降）

**Phase 0.5 (2026-04-24)** で `VenueBackend` trait を導入した。これにより venue ごとに backend を差し替え可能になった。

1. 起動時 `AdapterHandles::spawn_all()` で全取引所の `NativeBackend` を spawn ([`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs))。
   - 内部的には `Arc<dyn VenueBackend>` として保持。`NativeBackend` enum が既存の `hub/{venue}` ハンドルをラップ。
   - `set_backend(venue, Arc<dyn VenueBackend>)` で venue 単位に backend を上書き可能（Phase 2 での `EngineClientBackend` 差し込み口）。
2. メタデータを REST で取得し `tickers_info` にキャッシュ。
3. UI でティッカー選択 → `AdapterHandles::kline_stream` / `trade_stream` / `depth_stream` 経由で WS を開く。
   - 各メソッドは `get_backend(venue)` → `backend.kline_stream(...)` と 2 段階で委譲。
4. `exchange::Event` 列挙体（`DepthReceived` / `TradesReceived` / `KlineReceived`）として UI に流す（変更なし）。
5. [`src/screen/dashboard.rs`](../../src/screen/dashboard.rs) の `ingest_depth` / `ingest_trades` / `update_latest_klines` が消費（変更なし）。
6. インメモリ構造体に保持し、Iced が毎フレーム再描画。永続化 DB は無し。

**追加された型・ファイル**:
- [`exchange/src/adapter/venue_backend.rs`](../../exchange/src/adapter/venue_backend.rs): `VenueBackend` trait、`NativeBackend` enum、`TickerMetadataMap` / `TickerStatsMap` 型エイリアス。
- [`exchange/tests/venue_backend.rs`](../../exchange/tests/venue_backend.rs): trait 抽象化の統合テスト。

## Python 側の現状（2026-04-24 時点）
- `python/` は **空ディレクトリ**。Cargo にも `pyo3` 等の Python 連携依存は **無し**。
- 既存の subprocess・IPC・HTTP ローカルサーバ等の Rust↔Python 接続コードは存在しない。

> 注: この記述は **当時の状態**。2026-05-03 時点では以下が実装済み：
> - `python/engine/` — WS IPC サーバー、5 取引所ワーカー、NautilusRunner、replay/live セッション helper
> - `engine-client/` — Rust WS クライアント crate（handshake / process 管理 / session_file）
> - Rust↔Python WS IPC（SCHEMA_MAJOR=3, SCHEMA_MINOR=9）が本番稼働中
> - HTTP API（ポート 9876）は廃止済み。新規ルートは `python -m engine.replay_session run`

## 主要依存
- UI: `iced`, `iced_wgpu`, `palette`, `rodio`
- 通信: `reqwest`, `fastwebsockets`, `tokio`, `tokio-rustls`, `tokio-socks`
- データ: `sonic-rs`, `serde_json`, `csv`, `zip`, `chrono`
- セキュア保存: `keyring`（プロキシ認証）

## リファクタ観点での所感
- **強み**: `exchange` crate は既に独立しており、UI 側からは `Event` ストリームと `subscribe` 呼び出しのみで疎結合。Python 化の境界として理想的。
- **課題**:
  - WS 受信の高頻度（trade 33ms flush, depth 差分）に耐える IPC が必要。
  - `limiter.rs` のレート制限ロジックは Python に再実装が必要。
  - 既存 UI コードが `exchange::*` の型（`Trade`, `Kline`, `Depth` 等）に強く依存しているため、これらを共通スキーマとして固定化する必要あり。
