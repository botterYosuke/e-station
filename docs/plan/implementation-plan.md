# 実装計画

[`spec.md`](./spec.md) の構成へ段階的に移行するためのフェーズ分け。
各フェーズは単独でマージ可能・動作確認可能な粒度を目指す。

## フェーズ 0: 準備 & ベースライン計測（リスク低）

> **完了**

- [ ] `python/` に `engine` パッケージのスケルトンを置く。
- [ ] [`docs/plan/schemas/`](./schemas/) に IPC DTO の JSON Schema を作成。
  - 対象: `TradeMsg`, `KlineMsg`, `DepthSnapshotMsg`, `DepthDiffMsg`, `TickerMsg`, `TickerInfoMsg`, `TickerStatsMsg`, `OpenInterestMsg`, および各コマンド (`Hello` / `Ready` / `Subscribe` / `Unsubscribe` / `FetchKlines` / `FetchTrades` / `FetchOpenInterest` / `FetchTickerStats` / `ListTickers` / `GetTickerMetadata` / `RequestDepthSnapshot` / `SetProxy` / `Shutdown` / `Error` / `EngineError` / `DepthGap`)。
  - 参考: 既存 Rust 型 [`exchange/src/lib.rs`](../../exchange/src/lib.rs), [`exchange/src/adapter.rs`](../../exchange/src/adapter.rs) の `Event`。
  - スキーマ ⇔ 型定義の生成方針（`quicktype` / `datamodel-code-generator` 等）を決定。
  - `schema_major` / `schema_minor` の運用ポリシーを [`CHANGELOG.md`](./schemas/) に記載。
- [ ] Rust 側に `--data-engine-url ws://...` CLI フラグを追加（未指定時は従来動作）。dev モード時の接続トークンは環境変数 `FLOWSURFACE_ENGINE_TOKEN` から読み、本番同梱 spawn 時は stdin から受け取る（[spec.md §4.1.1](./spec.md#411-ローカル-ipc-のアクセス制御)）。
- [ ] **ベースライン計測**（[spec.md §9.3](./spec.md#93-ベースライン計測)）を実施し `docs/plan/benchmarks/baseline.md` に記録。以降のフェーズで比較する基準。Windows (開発環境) 必須、可能なら Linux も。
- [ ] 既存 Rust テストを通したまま CI を維持する。

**完了条件**: 既存挙動を変えずにマージでき、ベースラインが数値で記録されている。

## フェーズ 0.5: venue 単位 backend 抽象化（Rust 側のみ）✅

[spec.md §5.1](./spec.md#51-venue-単位の-backend-抽象化先行作業) に対応。取引所単位の段階移行を現実的にする前提工事。

> **完了** (2026-04-24, commit `4456ea5` ブランチ `phase-0.5/venue-backend-trait`)

- [x] `VenueBackend` trait を定義。現行 `AdapterHandles` の全経路を網羅:
  - 初期化: `fetch_ticker_metadata` / `fetch_ticker_stats`（`list_tickers` / `get_ticker_metadata` 相当）
  - ストリーム: `kline_stream` / `trade_stream` / `depth_stream`
  - フェッチ: `fetch_klines` / `fetch_open_interest` / `fetch_ticker_stats` / `fetch_trades`
  - 運用: `request_depth_snapshot` / `health`
  - 実装場所: [`exchange/src/adapter/venue_backend.rs`](../../exchange/src/adapter/venue_backend.rs)
- [x] `AdapterHandles` の各 venue フィールドを `Arc<dyn VenueBackend>` に置換（`Clone` 互換性のため `Box` ではなく `Arc`）。
  - `set_backend(venue, Arc<dyn VenueBackend>)` API を追加（Phase 2 で `EngineClientBackend` を差し込む入口）。
  - stream / fetch メソッドをすべて `get_backend(venue) -> Option<Arc<dyn VenueBackend>>` 経由に統一。
- [x] 既存 `hub/{venue}` を包む `NativeBackend` enum を実装し挙動を維持。
  - `NativeBackend::Binance(BinanceHandle)` / `Bybit` / `Hyperliquid` / `Okex` / `Mexc` の 5 バリアント。
  - Hyperliquid の `depth_stream` が要求する `tick_multiplier` 引数など venue 固有差異をここで吸収。
- [x] venue 毎に backend を指定できる `set_backend` API を追加（未指定時は `spawn_venue` が全 `NativeBackend` で起動）。
- [x] `cargo test --workspace` 全 PASS、`cargo clippy -- -D warnings` warning なし。
- [x] TDD: `exchange/tests/venue_backend.rs` に 4 テスト（set/get/configured_venues/health）。

**完了条件**: 抽象化導入後も従来の挙動・レイテンシが維持されている。→ 達成（NativeBackend は既存ハンドルをそのままラップ）。

## フェーズ 1: Python データエンジン MVP（Binance のみ） ✅

> **完了** (2026-04-24, commit `51459a7` ブランチ `phase-1/python-data-engine`)

- [x] `engine.server` に WS サーバを実装（`websockets` ライブラリ）。loopback バインドのみ、単一クライアント制限 + トークン一致時の既存接続置換（[spec.md §4.5.2](./spec.md#452-既存接続の置換半死接続対策)）、接続トークン検証、起動ハンドシェイク（[spec.md §4.5](./spec.md#45-起動ハンドシェイク)）、ping/pong keepalive を初期実装に含める。
- [x] `ExchangeWorker` 抽象 / server↔worker dispatch の境界を最初から設ける（[spec.md §6.1](./spec.md#61-プロセスモデルフェーズ-1-時点)）。フェーズ 1 は asyncio 単一プロセスだが、将来 venue 分割できる構造で着地させる。
- [x] `exchanges/binance.py` で REST メタデータ + Kline + **Open Interest** + 24h 統計 + WebSocket trade/depth/kline を実装（OI はインジケータが継続要求するため初期から必須）。
- [x] depth 整合性プロトコル（[spec.md §4.4](./spec.md#44-バックプレッシャと整合性保証)）: `session_id` / `sequence_id` / `prev_sequence_id` の付与、gap 検知時の `DepthGap` 送出と自発的再スナップショット、checksum がある場合の検証を実装。
- [x] `limiter.py` で Binance のレート制限を移植（[`exchange/src/adapter/limiter.rs`](../../exchange/src/adapter/limiter.rs) を参考）。
- [x] スキーマは pydantic、出力は orjson。
- [x] stdin から `{port, token}` JSON を受け取り、ランダムポート・トークンで起動できるようにする（開発時は環境変数フォールバックを許容）。
- [x] pytest で REST/WS の最低限のテスト（モック取引所 or VCR）＋ depth gap / session 切替の再同期テスト。

**完了条件**: Python のみで Binance のリアルタイム trade / depth / kline / OI を取得・配信でき、depth の gap 検知と再同期が動作する。 → **達成済み**（pytest Binance 33件全 PASS）

## フェーズ 2: Rust 側に engine-client を実装し Binance を切替

> **部分完了** (2026-04-24, ブランチ `phase-2/wiring`)

- [x] `engine-client` crate（`flowsurface-engine-client`）を `engine-client/` に新規作成し IPC DTO と WebSocket クライアントを実装。
  - `engine-client/src/dto.rs`: `Command` / `EngineEvent` / `TradeMsg` / `KlineMsg` / `DepthLevel` / `OiPoint`
  - `engine-client/src/convert.rs`: DTO ⇔ `exchange::` ドメイン型変換（`Trade` / `Kline` / `OpenInterest` / `Arc<Depth>`）
  - `engine-client/src/error.rs`: `EngineClientError` (thiserror)
- [x] 起動ハンドシェイク（`Hello` / `Ready`）と接続トークン受け渡しを実装（`engine-client/src/connection.rs`）。
  - schema_major 不一致時 `SchemaMismatch` エラー
  - broadcast channel でイベントをファンアウト
- [x] `EngineClientBackend` が `VenueBackend` trait を実装（`engine-client/src/backend.rs`）。
  - kline_stream / trade_stream / depth_stream
  - fetch_klines / fetch_open_interest / fetch_trades / request_depth_snapshot
  - depth は `session_id` / `sequence_id` で gap 検知し `RequestDepthSnapshot` を送る
- [x] `DepthTracker` 状態機械で gap 検知（`engine-client/src/depth_tracker.rs`）。
- [x] **Python プロセス監視・自動再起動・状態再投入** の骨格実装（`engine-client/src/process.rs`）:
  - `PythonProcess::spawn()`: stdin 経由で `{port, token}` を渡す
  - `ProcessManager::run_with_recovery()`: 指数バックオフで自動再起動・購読再送
- [x] Workspace `Cargo.toml` に `engine-client` を追加。
- [x] 統合テスト 36 件 全 PASS (`cargo test -p flowsurface-engine-client`)。
- [x] `cargo clippy -p flowsurface-engine-client -- -D warnings` warning なし。

**完了（2026-04-24, ブランチ `phase-2/engine-client`）**:
- [x] `--data-engine-url` CLI フラグで `src/main.rs` から `EngineClientBackend` を差し替える実装。
  - `ENGINE_CONNECTION: OnceLock<Arc<EngineConnection>>` グローバルで接続を保持。
  - 専用 tokio ランタイム（`engine-client` スレッド）が IO タスクを生涯保持。
  - `Flowsurface::new()` が `set_backend(Venue::Binance, EngineClientBackend)` を注入。
- [x] UI 側「エンジン再起動中」ステータス表示。
  - `ENGINE_RESTARTING: OnceLock<watch::Sender<bool>>` グローバルで再起動状態を配信。
  - `engine_status_stream()` → `Subscription::run` でイベントを Iced に流す。
  - `Message::EngineRestarting(bool)` → Toast 通知表示。`Flowsurface.engine_restarting` で状態保持。
  - `ProcessManager::run_with_recovery` に `on_ready: impl Fn()` コールバックを追加（TDD RED→GREEN）。
- [x] `docs/plan/benchmarks/phase-2.md` 作成（計測手順・合格ライン・障害試験手順を記録）。
- [x] IPC ハンドシェイク・`FetchKlines` REST 経由の疎通確認（2026-04-24）。
- [x] `Subscribe(stream=trade)` コマンドが IPC 経由でエンジンに到達することを確認（2026-04-24）。
- [x] `test_trade_stream.py` で Trades 受信確認（spot endpoint で 30 件 PASS, 2026-04-24）。
- [ ] `flowsurface --data-engine-url` で GUI chart 描画を目視確認（Binance futures WS throttle 解除後）。
- [ ] レイテンシ・CPU 使用率の実測比較（Python spawn モード配線後に実施）。
- [ ] 障害試験（Python kill → 自動復旧 → 板再同期の手動確認、spawn モード配線後に実施）。

> **現況（2026-04-24）**: IPC プロトコル層は全項目疎通確認済み（Hello/Ready, FetchKlines, Subscribe, Trades 受信）。
> Binance futures WS (`fstream.binance.com`) のみデバッグ中の過剩接続による一時的な IP throttle が残っており、
> GUI chart の目視確認が保留中。spot WS は正常動作しており **コードの問題ではない**。
> spawn モード未配線のため自動復旧試験は次フェーズ以降に実施。

**完了条件**: フラグ ON で Binance チャートが Python 経由で正しく描画される。**加えて Python を kill しても自動復旧し、購読と板整合性が回復する**。

### 設計判断・ハマりどころ・Tips

- **FetchError は外部から構築不可**: `exchange::error::FetchError` のフィールドは `pub(crate)` のため `engine-client` からは構築できない。`AdapterError::InvalidRequest(String)` を代替として使用。
- **async_stream クレート**: `VenueBackend` の `BoxStream<'static, Event>` 戻り値は `async_stream::stream!` マクロで実装。futures の `channel` パターンより記述が簡潔。
- **broadcast channel のラグ対策**: 容量 512 で設定。高頻度の depth diff はラグが発生しうるため `RecvError::Lagged` をログ警告でハンドリング。
- **テストの crate 名**: package name `flowsurface-engine-client` → テスト内では `flowsurface_engine_client`（ハイフンがアンダースコアに変換される）。
- **tokio-tungstenite 0.26**: `Message::Text` は `String` を直接受け取らず `.into()` が必要（`Utf8Bytes` ラッパー）。
- **`--data-engine-url` wiring**: `Flowsurface::new()` は同期関数のため async 接続は `main()` 内の専用 tokio ランタイムで行い、`OnceLock` 経由で共有。ランタイムを `_engine_rt` 変数でライフタイム保持（`main()` 戻りまで保持）。
- **`watch::Ref` + async**: `rx.borrow()` の戻り値 `Ref<'_, bool>` は `!Send`。`yield` の前に `let value = *rx.borrow();` でコピーしてから yield すること（Send 境界違反回避）。
- **`Subscription::run` の制約**: Iced 0.14 の `Subscription::run` は `fn() -> S` のみ受け付ける（クロージャ不可）。グローバルへのアクセスが必要なら top-level 関数として定義し static を読む。
- **`asyncio.wait_for(ws.recv(), timeout=短時間)` は Windows で禁止**: IocpProactor 上では短周期キャンセルが `websockets` 内部の受信バッファを破壊し、接続は維持されるがメッセージが無音になる。`async for raw in ws` + 別タスクによる定期フラッシュで代替すること（`stream_depth` / `stream_kline` の実装を参照）。

## フェーズ 3: 残り取引所の Python 移植

優先順（取引所の安定度・利用頻度で並べ替え可）:

- [x] Bybit ✅ (2026-04-24)
- [x] Hyperliquid ✅ (2026-04-24)
- [x] OKX ✅ (2026-04-24, ブランチ `phase-3/okex-python-worker`)
- [ ] MEXC

各取引所ごとに：
1. `python/engine/exchanges/<venue>.py` 実装
2. レート制限の移植
3. 統合テスト（Rust 側 UI で動作確認）

**完了条件**: 全 5 取引所が Python 経由で動作。

> **現況（2026-04-24）**: pytest 全体 119 件 PASS（Binance 33 + Bybit 21 + Hyperliquid 29 + OKX 30 + その他 6）。残り MEXC のみ。

### OKX 実装詳細（2026-04-24 完了）

- **実装ファイル**: [`python/engine/exchanges/okex.py`](../../python/engine/exchanges/okex.py)
- **テスト**: `python/tests/test_okex_rest.py` (20件) + `python/tests/test_okex_depth_sync.py` (10件) = 計 30件全 PASS
- **server.py 統合**: `self._workers["okex"] = OkexWorker()` 追加済み

#### Binance/Bybit との主な差異

| 項目 | Binance/Bybit | OKX |
|------|---------------|-----|
| REST base | 各取引所 REST | `https://www.okx.com/api/v5` |
| WS base | 各取引所 WS | `wss://ws.okx.com/ws/v5/public` (trades/depth) / `wss://ws.okx.com/ws/v5/business` (klines) |
| WS subscribe | URL / JSON msg | `{"op":"subscribe","args":[{"channel":"trades","instId":"BTC-USDT"}]}` |
| Depth プロトコル | snapshot+diff (Binance) / snapshot-only WS (Bybit) | **snapshot+delta** (action="snapshot"/"update") |
| Depth シーケンス | Binance: U/u/pu / Bybit: u (monotonic +1) | `seqId` (monotonic +1 per message) |
| レート制限 | 各取引所固有 | 20 req/2sec (`OkexLimiter`, TokenBucket capacity=20, refill=10/s) |
| OI | REST 履歴あり | REST `/rubik/stat/contracts/open-interest-history?instId=...&period=1H` |
| symbol 形式 | "BTCUSDT" etc. | spot: "BTC-USDT" / linear: "BTC-USDT-SWAP" / inverse: "BTC-USD-SWAP" |
| Trade side | buy/sell | `side` フィールドがそのまま "buy"/"sell" |
| kline confirm | Bybit: `confirm` bool | index[8]: "1"=closed, "0"=open |
| 板スナップショット | REST + WS | REST `/market/books?instId=...&sz=400` (seqId がシーケンス基準) |

#### OkexDepthSyncer 設計

Bybit 類似の snapshot+delta プロトコル:
1. `action="snapshot"` → `DepthSnapshot` イベント送出、`applied_seq = seqId`
2. `action="update"` → `seqId == applied_seq + 1` を厳密チェック
3. gap 検知 → `DepthGap` 送出 + `needs_resync=True` → stream_depth が WS 再接続
4. スナップショット到着前のバッファリング (MAX_PENDING=512) → スナップショット後にリプレイ
5. 新 snapshot 到着時に `needs_resync=False` にリセット（Bybit と異なり同一 WS 接続内でスナップショット再取得可能）

#### fetch_klines のパラメータ

OKX `/market/history-candles` はページネーション cursor:
- `before={start_ms}` → `ts > start_ms` なローソクを返す
- `after={end_ms}` → `ts < end_ms` なローソクを返す
- 結果は降順で返るため Python 側で `sort(key=open_time_ms)` で昇順化

#### fetch_open_interest の注意

- 返値配列: `[ts, oi_contracts, oi_currency]`、index[2] (oi_currency = BTC/USD建て) を使用
- Rust Fetch.rs と同じ `oi_ccy` (index 2) を選択

#### バグ修正（2026-04-24、レビュー後修正済み）

- **Bug #1** `fetch_klines` が `limit=400`（server.py デフォルト）をそのまま OKX に渡していた → OKX の `/market/history-candles` max は 300 であり 400 は API エラー。`min(limit, 300)` でクランプ。テスト `test_fetch_klines_clamps_limit_to_okx_max` 追加。commit `7314502`
- **Bug #2** `fetch_ticker_stats("__all__", "linear_perp")` が SWAP エンドポイントの全銘柄を返しており、inverse 銘柄（`-USD-SWAP` サフィックス）が混入していた → `_matches_market(inst_id)` で instId サフィックスにより絞り込み（linear: `-USDT-SWAP` / inverse: `-USD-SWAP`）。テスト 2件追加。commit `7314502`

#### Tips

- **WS 2エンドポイント**: trades/depth は `/public`、klines は `/business`。同一接続に混在不可。
- **seqId は連番保証**: OKX API ドキュメントでは seqId は必ず +1 で増加。gap 検知は Bybit と同じロジックが適用可能。
- **state フィルタ**: `state == "live"` のみ（spot）、SWAP は `state == "live"` + `ctType` + `settleCcy` で絞り込み。
- **spot vol 計算**: `volCcy24h` は spot では quote 通貨 (USDT) 建て → そのまま daily_volume として使用。perp では base 通貨 (BTC/ETH) 建て → `volCcy24h * last_price` に変換。
- **kline confirm フィールド**: index[8] が存在しない古いデータでも安全に処理できるよう `len(row) > 8 and row[8] == "1"` でチェック。
- **kline limit クランプ**: OKX `/market/history-candles` の max は 300。server.py は `limit=400` をデフォルトで渡すため、必ず `min(limit, 300)` が必要。

### Hyperliquid 実装詳細（2026-04-24 完了）

- **実装ファイル**: [`python/engine/exchanges/hyperliquid.py`](../../python/engine/exchanges/hyperliquid.py)
- **テスト**: `python/tests/test_hyperliquid_rest.py` (16件) + `python/tests/test_hyperliquid_depth_sync.py` (9件) = 計 25件全 PASS
- **server.py 統合**: `self._workers["hyperliquid"] = HyperliquidWorker()` 追加済み

#### Binance/Bybit との主な差異

| 項目 | Binance/Bybit | Hyperliquid |
|------|---------------|-------------|
| REST base | 各取引所 REST | `https://api.hyperliquid.xyz/info` (POST のみ) |
| WS base | 各取引所 WS | `wss://api.hyperliquid.xyz/ws` |
| WS subscribe | URL or JSON msg | `{"method":"subscribe","subscription":{...}}` |
| Depth プロトコル | snapshot+diff (Binance) / snapshot-only WS (Bybit) | **毎回フル l2Book スナップショット** (diff なし) |
| Depth シーケンス | Binance: U/u/pu / Bybit: u (monotonic) | `time` フィールド (ms) + 単調増加保証 |
| レート制限 | 各取引所固有 | 1200 req/60sec (`HyperliquidLimiter`) |
| OI | REST 履歴あり | **なし** (常に空リスト返却) |
| Ticker symbol | "BTCUSDT" etc. | perp: "BTC" (coin name) / spot: "BTCUSDC" (display) |
| Trade side | buy/sell 直接 | "A" = 売り aggressor → sell / "B" → buy |
| Market | linear/inverse/spot | **linear_perp/spot のみ** (inverse なし) |
| 複数 DEX | なし | perpDexs API で DEX 一覧取得 → マージ |

#### HyperliquidDepthSyncer 設計

Hyperliquid は **毎回完全な l2Book** を WS で配信する（diff なし）:
1. 各 WS メッセージ → `DepthSnapshot` イベントを即時送出
2. `sequence_id` = `time` フィールド (ms) 、ただし同一 time が連続した場合は +1 で単調増加を保証
3. `DepthDiff` / `DepthGap` は一切発生しない
4. 再同期が必要な場合は WS 再接続 → 次の l2Book メッセージが新スナップショットになる

#### fetch_klines のタイムレンジ計算

Hyperliquid の `candleSnapshot` は `startTime`/`endTime` のみで制御し `limit` パラメータがない:
- `start_ms` と `end_ms` 両方指定 → そのまま使用
- `start_ms` のみ省略 → `end_ms - limit * interval_ms` を計算
- `end_ms` も省略 → 現在時刻を `end_ms` に使用

#### spot ティッカー記号マッピング

- pair name が `@N` 形式 → `base_name + quote_name` に展開 (e.g., "@1" → "BTCUSDC")
- pair name が "/" 含む → "/" を除去 (e.g., "BTC/USDC" → "BTCUSDC")
- WS subscribe では `coin` に display symbol を使用 (Hyperliquid は display name でも受け付ける)

#### バグ修正（2026-04-24、レビュー後修正済み）

- **Bug #1** `_list_tickers_spot` が display symbol ("BTCUSDC") を `symbol` として返していた → Rust の `engine-client/src/backend.rs:397` は `symbol` フィールドをそのまま `coin` として API コールに使うため、"BTC/USDC" (raw pair name) を返す形に修正。テスト `test_list_tickers_spot` / `test_list_tickers_spot_excludes_zero_price` / `test_fetch_ticker_stats_spot` を更新。commit `b754f40`
- **Bug #1 再発防止**: spot round-trip テスト (`test_spot_symbol_roundtrip_*`) を 4 件追加。`list_tickers` 返値の symbol を直接 `fetch_depth_snapshot` / `fetch_ticker_stats` / `fetch_klines` に渡すことで契約を検証。

#### 既知の課題（Medium）― IPC 経由の display symbol 欠落

**状況**: Python IPC パスでは spot ティッカーの `symbol` フィールドが raw pair name（`BTC/USDC`, `@1` 等）のまま Rust 側に届く。`engine-client/src/backend.rs:397` は `Ticker::new(symbol, exchange)` に渡すだけなので、`BTCUSDC` / `HYPEUSDC` 相当の display alias が構築されない。ネイティブ Hyperliquid アダプタは [`exchange/src/adapter/hub/hyperliquid/fetch.rs:283`](../../exchange/src/adapter/hub/hyperliquid/fetch.rs) で display symbol を別途生成しており、IPC パスとで表示が乖離する。`@...` 形式の pair がサイドバー・保存レイアウト上に生のまま露出しうる。

**影響範囲**: Hyperliquid Python worker が本番 wiring されるまでは潜在バグ（現状は native backend が生きている）。フル切替前に修正が必要。

**修正方針**: `TickerInfoMsg` に `display_symbol: Optional[str]` フィールドを追加し、Python 側が `_spot_display(pair)` で生成した値（`@N` → `base+quote`、`/` 除去後の文字列）を乗せて送出。Rust 側 `engine-client/src/backend.rs` で `display_symbol` が Some の場合は `Ticker { symbol: display_symbol, raw: symbol }` 相当に展開する。`exchange/src/lib.rs:344` の既存 display 対応フィールドが使用できる。

**検証済みテスト**: `uv run pytest python/tests/test_hyperliquid_rest.py python/tests/test_hyperliquid_depth_sync.py` → 29 PASS（2026-04-24）。

#### Tips

- **全リクエストが同一 POST エンドポイント**: `https://api.hyperliquid.xyz/info` への POST のみ。テストでは pytest-httpx の FIFO レスポンス機能を使い複数コールをシミュレート。
- **perpDexs 必須**: `list_tickers(linear_perp)` と `fetch_ticker_stats` はまず `perpDexs` を呼んで DEX 一覧を取得し、DEX ごとに `metaAndAssetCtxs` を呼ぶ。テストは `[null]` (メイン DEX のみ) を想定。
- **midPx が `null` や空文字の場合がある**: `_asset_price` で `float(ctx.get("midPx") or 0)` として安全にゼロ fallback。
- **tick_size 計算**: Rust の `compute_tick_size` をそのまま Python 移植。`_MAX_DECIMALS_PERP=6`, `_SIG_FIG_LIMIT=5` で BTC(5桁)=1.0、ETH(4桁,sz=4)=0.1 等を正しく計算。
- **OI は非対応**: Hyperliquid は過去の OI 時系列 API を持たないため常に空リスト返却。UI は OI グラフを非表示にするだけで問題なし。

### Bybit 実装詳細（2026-04-24 完了）

- **実装ファイル**: [`python/engine/exchanges/bybit.py`](../../python/engine/exchanges/bybit.py)
- **テスト**: `python/tests/test_bybit_rest.py` (11件) + `python/tests/test_bybit_depth_sync.py` (10件) = 計 21件全 PASS
- **server.py 統合**: `self._workers["bybit"] = BybitWorker()` 追加済み

#### Binance との主な差異

| 項目 | Binance | Bybit |
|------|---------|-------|
| REST base | `https://api.binance.com` etc. | `https://api.bybit.com` |
| WS base | `wss://fstream.binance.com` etc. | `wss://stream.bybit.com/v5/public/{linear\|inverse\|spot}` |
| WS subscribe | 接続 URL に stream 名を埋め込む | 接続後に `{"op":"subscribe","args":[...]}` を送信 |
| Depth 初期化 | REST でスナップショット取得 → WS で diff | WS の最初のメッセージが type="snapshot" で完全板 |
| Depth シーケンス | `U`/`u`/`pu` フィールド（Binance 独自） | `u` のみ（必ず連番 +1 で継続を検証） |
| 板の resync | `BinanceDepthSyncer.resync()` → REST 再取得 | `needs_resync=True` → WS 再接続で新 snapshot を受信 |
| レート制限 | 1200 weight/min + 300 raw/5min | 600 req/5sec (`BybitLimiter`) |
| kline interval | "1m", "5m" ... | "1", "5", ..., "D" (数値文字列または "D") |
| OI period | "1h", "4h" ... | "1h", "4h", "1d", "5min" ... |
| Trade side | `m` (bool) → buy/sell | `S` ("Buy"/"Sell") |

#### BybitDepthSyncer 設計

Binance と異なり WS 自身がスナップショットを配信する:
1. type="snapshot" メッセージで `_apply_snapshot` → DepthSnapshot イベント送出
2. type="delta" で `_apply_delta` → `u == applied_seq + 1` を厳密チェック
3. gap 検知 → DepthGap 送出 + `needs_resync=True` → stream_depth が WS 再接続
4. スナップショット到着前のバッファリング (MAX_PENDING=512) → スナップショット後にリプレイ

#### バグ修正（2026-04-24、テスト追加で検出・修正済み）

- **Bug #1** `fetch_open_interest` が `category=linear` 固定だったため inverse_perp で誤 API を叩いていた → `_market_category(market)` を使う形に修正。テスト `test_fetch_open_interest_inverse_uses_inverse_category` 追加。commit `7fcb84f`
- **Bug #2** `list_tickers` が Bybit の `status` フィールドを無視しており、`PreLaunch` / `Settling` 等の非稼働銘柄が UI に混入する恐れがあった → `status != "Trading"` の場合は除外するよう修正。テスト `test_list_tickers_excludes_non_trading_status` 追加。commit `0fde866`

#### Tips

- **Bybit OI は linear/inverse 両対応**: `category` は `_market_category(market)` で決定。ただし inverse OI は spot 同様に空リストを返さず `category=inverse` で正しく取得できる。
- **Depth REST snapshot**: `RequestDepthSnapshot` op 対応のため `GET /v5/market/orderbook?category={cat}&symbol={sym}&limit=200` を `fetch_depth_snapshot` で実装。`result.u` を `last_update_id` として使用。
- **Depth level**: `orderbook.200` トピックを使用（200レベル、100ms 更新）。更小レベル (50) も選択可。
- **ticker_stats volume**: Bybit の `volume24h` は base asset 単位のため、`volume24h * lastPrice` で USD 換算している。

### OKX 実装詳細（2026-04-24 完了）

- **実装ファイル**: [`python/engine/exchanges/okx.py`](../../python/engine/exchanges/okx.py)
- **server.py 統合**: `self._workers["okx"] = OkxWorker()` 追加済み

## フェーズ 4: ヒストリカルデータ・bulk download 移植

- [ ] [`src/connector/fetcher.rs`](../../src/connector/fetcher.rs) 相当の機能を Python に実装。
- [ ] `data.binance.vision` からの zip/csv 取得・展開を Python で実施。
- [ ] Rust 側は `FetchTrades` / `FetchKlines` コマンドを送って結果を待つだけにする。

**完了条件**: ヒストリカル trade のフェッチが Python に移管。

## フェーズ 5: Rust から取引所コードを削除

- [ ] `exchange/src/adapter/hub/` を削除。
- [ ] `limiter.rs`, `proxy.rs`（プロキシ設定の受け渡しは残す）, `connect.rs` の取引所固有部分を削除。
- [ ] `reqwest`, `fastwebsockets`, `tokio-rustls`, `tokio-socks`, `sonic-rs`, `csv`, `zip` 等、Python 移管で不要になった依存を削除。
- [ ] `--data-engine-url` フラグをデフォルト動作に格上げ、旧経路コードを撤去。

**完了条件**: Rust ビルドが Iced と engine-client のみに依存し、ビルドサイズが縮む。

## フェーズ 6: 配布・運用整備

- [ ] PyInstaller / Nuitka 等で Python サイドを単一実行ファイル化、Rust バイナリと同梱。
- [ ] [`scripts/`](../../scripts/) の Win/Mac/Linux ビルドスクリプトに Python 同梱手順を追加。
- [ ] 起動時の Python プロセス監視・再起動ロジックを本実装。
- [ ] エラーログを Rust 側 `fern` ロガーに集約（Python の stderr を吸い上げる）。
- [ ] README / ユーザードキュメント更新。

**完了条件**: ユーザーが Python ランタイムを別途インストールせずに既存と同じ操作で起動できる。

## ロールバック戦略

- フェーズ 5 完了までは旧 Rust 実装が残っているため、`--data-engine-url` を外せば従来動作に戻せる。
- フェーズ 5 のマージはタグを切ってから実施し、問題が出たら 1 リリース前に戻せるようにする。

## 計測指標と合格ライン

詳細は [spec.md §9](./spec.md#9-非機能要件合格ライン)。各フェーズ完了時に再計測し `docs/plan/benchmarks/` に追記する。

フェーズ 2 合格ライン（抜粋）:
- IPC 追加レイテンシ: 中央値 < 2 ms / p99 < 10 ms
- Python クラッシュ → 自動復旧完了: < 3 秒
- depth 再同期: < 500 ms
- CPU 使用率: 現行 Rust 直結の +30% 以内
- depth gap 検知漏れ: 0

未達時の対応:
- レイテンシ / CPU 不足 → [spec.md §4.3.1](./spec.md#431-depth-チャネルのバイナリ化検討) のバイナリ化を適用。
- 慢性的な性能差 → [spec.md §7.1](./spec.md#71-rust-直結モードの長期方針要決定) の案 C（Rust 直結の optional 残置）を再検討。
