# 新仕様: Rust ビュアー + Python データエンジン

## 1. ゴール

- **Rust** はビュアー専用。UI 描画・ユーザー操作・レイアウト永続化のみを担当する。
- **Python** が取引所 REST/WebSocket への接続、レート制限、データ正規化、配信を担当する。
- Rust は取引所 SDK を**直接呼ばない**。すべてのマーケットデータは Python サービス経由で受け取る。

## 2. 責務分割

| 機能 | 現状 | 新構成 |
|---|---|---|
| Iced GUI / 描画 / 入力 | Rust | **Rust** |
| レイアウト・テーマ永続化 | Rust | **Rust** |
| サウンド再生 | Rust | **Rust** |
| ティッカーメタデータ取得 | Rust (`exchange/`) | **Python** |
| Kline / OI / 24h 統計の REST 取得 | Rust | **Python** |
| trade / depth / kline の WebSocket 購読 | Rust | **Python** |
| 取引所別レート制限 | Rust (`limiter.rs`) | **Python** |
| プロキシ / 認証情報 | Rust (`keyring`) | **Rust が保持し Python に渡す**（または Python 側に環境変数で注入） |
| ヒストリカル trade（bulk download 含む） | Rust (`connector::fetcher`) | **Python** |
| インメモリ集計（チャート用バッファ） | Rust (`data/`) | **Rust に残す**（描画直近のものに限定） |

## 3. プロセスモデル

```
┌──────────────────────────┐        IPC         ┌──────────────────────────┐
│  Rust Viewer (Iced)      │ ◄────────────────► │  Python Data Engine      │
│  - UI / canvas           │   (本文 §4 参照)    │  - REST clients          │
│  - layout state          │                    │  - WS clients            │
│  - input handling        │                    │  - rate limiter          │
│  - in-memory chart bufs  │                    │  - normalization         │
└──────────────────────────┘                    └──────────────────────────┘
```

- Rust 起動時に Python サブプロセスを spawn（同梱バイナリ or `python -m flowsurface_data`）。
- 異常終了時は Rust が再起動を試行（指数バックオフ、最大 N 回）。
- 開発時は Python を独立起動して Rust から既存ポートに接続する形も許容（`--data-engine-url` フラグ）。

## 4. IPC プロトコル

### 4.1 トランスポート

**第一案: ローカル WebSocket + JSON**
- Python 側で `localhost:<port>` に WebSocket サーバを立てる（`websockets` or `fastapi`）。
- Rust 側は既存の `fastwebsockets` クライアントを再利用できる。
- 双方向（コマンド↔イベント）を 1 接続で扱える。
- ポートは Rust が空きを選び `--port` 引数で Python に渡す。

**代替案**:
- gRPC（`tonic` + `grpcio`）: 型安全だが依存が重い。
- Unix Domain Socket / Named Pipe + length-prefixed JSON: OS 依存だが最速。
- ZeroMQ: 低レイテンシだがバイナリ依存追加。

**推奨**: まず WebSocket+JSON で開始し、ボトルネックが出たら MessagePack に切替。スキーマは [4.3](#43-メッセージスキーマ) を共通定義とする。

### 4.2 メッセージ方向

| 方向 | 種類 | 例 |
|---|---|---|
| Rust → Python | `Subscribe` / `Unsubscribe` / `FetchKlines` / `FetchTrades` / `ListTickers` / `Shutdown` | `{"op":"subscribe","venue":"binance","ticker":"BTCUSDT","stream":"trade"}` |
| Python → Rust | `Tickers` / `Klines` / `Trade` / `Depth` / `KlineUpdate` / `Error` / `Stats` | `{"event":"trade","venue":"binance","ticker":"BTCUSDT","p":68000.1,"q":0.012,"side":"buy","ts":...}` |

### 4.3 メッセージスキーマ

- 既存 Rust 型（`exchange::Trade`, `Kline`, `OpenInterest`, `Ticker`, `TickerInfo`, `TickerStats`, `Depth`）をそのまま JSON 表現できる shape に揃える。
- 共通スキーマ定義は `docs/plan/schemas/` 配下に JSON Schema として配置（実装時に追加）。
- Python 側は `pydantic` モデル、Rust 側は `serde` 派生で 1:1 対応。
- タイムスタンプは UNIX ms (i64)、価格・数量は `string`（精度損失防止、Rust 側で `Decimal` 相当へパース）。

### 4.4 バックプレッシャ
- Python は trade を 33ms（現行と同じ）でバッチ化して送信、1 メッセージ複数トレードの配列を許容。
- depth は差分のみ。スナップショット要求は明示コマンド `RequestDepthSnapshot`。
- Rust 側 receive キューが詰まった場合、最古の trade バッチから drop し warning ログ。

## 5. Rust 側の変更概要

- `exchange` crate を **`engine-client` crate に置換**（または同名のまま中身を差し替え）。
  - 既存 `AdapterHandles` の API（`spawn_all`, `subscribe`, イベントストリーム）はできるだけ維持し、UI 側変更を最小化。
  - 中身は WebSocket クライアントに置換、`Event` 列挙体は維持。
- `connector::fetcher` も `engine-client` 経由に置換。
- `data/` crate のチャートロジックはそのまま流用。
- `limiter.rs`, `proxy.rs`, `hub/*` は削除（Python 側に移管）。
- 起動時の Python プロセス管理用モジュール `src/engine/process.rs` を新設。

## 6. Python 側の構成

```
python/
├── pyproject.toml
├── flowsurface_data/
│   ├── __main__.py          # CLI: `python -m flowsurface_data --port N`
│   ├── server.py            # WS サーバ・dispatch
│   ├── schemas.py           # pydantic モデル
│   ├── limiter.py           # 取引所別レート制限
│   ├── exchanges/
│   │   ├── base.py          # Exchange ABC（fetch_*, stream_*）
│   │   ├── binance.py
│   │   ├── bybit.py
│   │   ├── hyperliquid.py
│   │   ├── okex.py
│   │   └── mexc.py
│   └── bulk/                # data.binance.vision 等の bulk DL
└── tests/
```

- 主要依存: `aiohttp` or `httpx`（REST）、`websockets`（WS クライアント＆サーバ）、`pydantic`、`uvloop`（Linux/macOS）、`orjson`。
- 配布: 開発時は `uv` / `pip` セットアップ、リリース時は `PyInstaller` で同梱バイナリ化（決定は [open-questions.md](./open-questions.md) 参照）。

## 7. 互換性・移行戦略

- 既存ユーザー設定・レイアウト JSON はそのまま使える（UI 側スキーマは変えない）。
- 取引所名・ティッカー識別子は現行 Rust 型と同じ表記を維持。
- 段階的に取引所単位で Python に移し、未移行のものは Rust 直接接続を残す（Feature flag で切替）。

## 8. 非ゴール

- UI 機能の追加・変更は本計画の対象外。
- Python 側で任意の戦略実行 / 自動売買を行うことは対象外（あくまで取得・配信エンジン）。
- 永続化 DB の導入は対象外（必要なら別計画）。
