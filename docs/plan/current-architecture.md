# 現状アーキテクチャ調査

調査日: 2026-04-24
対象: e-station (Flowsurface v0.8.7)

## 全体構成

```
e-station/
├── src/                  # メインバイナリ (Iced GUI)
├── exchange/             # 取引所アダプタ crate (REST/WS)
├── data/                 # チャート集計・設定 crate
├── python/               # 空（拡張用）
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

### データフロー（現状）
1. 起動時 `AdapterHandles::spawn_all()` で全取引所のハンドラを spawn ([`exchange/src/adapter/client.rs`](../../exchange/src/adapter/client.rs))。
2. メタデータを REST で取得し `tickers_info` にキャッシュ。
3. UI でティッカー選択 → `subscribe(StreamKind)` で WS を開く。
4. `exchange::Event` 列挙体（`DepthReceived` / `TradesReceived` / `KlineReceived`）として UI に流す。
5. [`src/screen/dashboard.rs`](../../src/screen/dashboard.rs) の `ingest_depth` / `ingest_trades` / `update_latest_klines` が消費。
6. インメモリ構造体に保持し、Iced が毎フレーム再描画。永続化 DB は無し。

## Python 側の現状
- `python/` は **空ディレクトリ**。Cargo にも `pyo3` 等の Python 連携依存は **無し**。
- 既存の subprocess・IPC・HTTP ローカルサーバ等の Rust↔Python 接続コードは存在しない。

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
