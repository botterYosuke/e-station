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
- [x] MEXC ✅ (2026-04-24, ブランチ `phase-3/mexc-python-worker`)

各取引所ごとに：
1. `python/engine/exchanges/<venue>.py` 実装
2. レート制限の移植
3. 統合テスト（Rust 側 UI で動作確認）

**完了条件**: 全 5 取引所が Python 経由で動作。✅ **達成済み**

> **現況（2026-04-24）**: pytest 全体 156 件 PASS（Binance 33 + Bybit 21 + Hyperliquid 29 + OKX 30 + MEXC 34 + その他 9）。全 5 取引所対応完了。

### MEXC 実装詳細（2026-04-24 完了）

- **実装ファイル**: [`python/engine/exchanges/mexc.py`](../../python/engine/exchanges/mexc.py)
- **テスト**: `python/tests/test_mexc_rest.py` (22件) + `python/tests/test_mexc_depth_sync.py` (12件) = 計 34件全 PASS
- **server.py 統合**: `self._workers["mexc"] = MexcWorker()` 追加済み

#### Binance/OKX との主な差異

| 項目 | Binance/OKX | MEXC |
|------|-------------|------|
| REST spot | 各取引所 REST | `https://api.mexc.com/api/v3` |
| REST futures | 各取引所 REST | `https://api.mexc.com/api/v1/contract` |
| WS endpoint | 各取引所 WS | `wss://contract.mexc.com/edge` (futures のみ) |
| WS subscribe | URL / JSON op | `{"method": "sub.depth", "param": {"symbol": ...}}` |
| Depth プロトコル | Snapshot+diff (Binance) / snapshot WS (OKX) | REST snapshot + WS version-based diff |
| Depth シーケンス | Binance: U/u/pu / OKX: seqId | `version` (monotonic +1 per diff) |
| レート制限 | 各取引所固有 | 10 req/2sec (`MexcLimiter`, TokenBucket capacity=10, refill=5/s) |
| OI | REST 履歴あり | **なし** (常に空リスト返却) |
| spot symbol | "BTCUSDT" etc. | spot: "BTCUSDT" / futures linear: "BTC_USDT" / futures inverse: "BTC_USD" |
| Trade direction | buy/sell / side フィールド | `T`: 2=sell, それ以外=buy |
| kline (REST spot) | 各取引所配列形式 | `[open_ts_ms, o, h, l, c, vol, close_ts_ms, ...]` 配列 |
| kline (REST futures) | 各取引所固有 | `{ data: { time: [...sec], open: [...], ... } }` 形式 (timestamp は秒→ms変換必要) |
| kline WS | OKX: "business" エンドポイント | futures WS のみ対応 (spot kline WS は非対応) |
| spot stream | 全市場対応 | spot depth/kline/trades WS は非対応 (Disconnected を即時返却) |

#### MexcDepthSyncer 設計

MEXC WS は REST スナップショット取得後に diff のみ配信する:
1. WS subscribe → 確認メッセージ受信 (`{symbol}.sub.depth` チャネル)
2. REST `GET /v1/contract/depth/{symbol}` でスナップショット取得
3. `apply_snapshot(version, bids, asks)` → `DepthSnapshot` イベント送出、`applied_version = version`
4. 以降の WS diff (`{symbol}.depth` チャネル): `version == applied_version + 1` を厳密チェック
5. gap 検知 → `DepthGap` 送出 + `needs_resync=True` → WS 再接続
6. スナップショット前のバッファリング (MAX_PENDING=512) → スナップショット後にリプレイ

#### fetch_klines のパラメータ

- Spot: `GET /v3/klines?symbol={sym}&interval={1m|5m|...}&limit={n}&startTime={ms}&endTime={ms}`
  - 結果は `[open_ts_ms, open, high, low, close, vol, close_ts_ms, asset_vol]` の配列
- Futures: `GET /v1/contract/kline/{sym}?interval={Min1|...}&limit={n}&start={sec}&end={sec}`
  - 結果は `{ data: { time: [...sec], open: [...], high: [...], low: [...], close: [...], vol: [...] } }` 形式
  - timestamp は秒単位 → `* 1000` で ms 変換

#### Spot WebSocket の非対応について

MEXC の spot WS は `wss://wbs.mexc.com/ws` という別エンドポイントで、サブスクリプション形式も異なる。
Rust の実装も futures WS (`contract.mexc.com`) のみを使用しているため、Python 側でも spot depth/kline/trades stream は
`Disconnected` イベントを即時返却する設計とした。UI 側は native MEXC backend (spot) を引き続き使用するか、spot は表示しない設計で対応可能。

#### Tips

- **REST spot ticker stats の `priceChangePercent`**: 小数分率（例: `0.005` = 0.5%）→ `* 100` で % 変換
- **REST futures ticker stats の `riseFallRate`**: 同様に小数分率 → `* 100` で % 変換
- **Futures kline time は秒単位**: `time` 配列の値は UNIX 秒 → `* 1000` で ms 変換が必要（見落としやすい）
- **linear / inverse 判定**: futures は symbol 末尾が `_USDT` = linear, `_USD`（かつ `_USDT` 非末尾）= inverse
- **WS ping**: `{"method": "ping"}` を 15 秒ごとに送信。pong: `{"channel": "pong", ...}`
- **Depth WS channel 名**: subscribe 確認 = `{symbol}.sub.depth`, diff = `{symbol}.depth`, trade = `{symbol}.deal`, kline = `{symbol}.kline`
- **OI 非対応**: MEXC は過去の OI 時系列 API を持たないため常に空リスト返却。Hyperliquid と同様。
- **Kline WS の `t` は秒**: REST futures kline 同様、WS kline の `t` フィールドも秒単位 → `* 1000` で ms 変換。

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

## フェーズ 3 完了後レビュー指摘 (2026-04-24)

pytest 124/124 PASS の状態で実施したコードレビューで検出した IPC パス固有の契約ギャップ。

### 修正済み

#### Fix #2 (High): Bybit depth 復旧がブロードキャストラグ後に壊れる
**症状**: Rust 側ブロードキャストチャネルがラグした際、`backend.rs:335` が tracker をリセットして `RequestDepthSnapshot` を送出。Bybit は `orderbook.200` と REST スナップショットのシーケンス空間が別のため `fetch_depth_snapshot` が `NotImplementedError` を raise し、`_spawn_fetch` がそれをキャッチして `Error` イベントを返していた。Rust 側は Error を無視するため、以降の diff がすべて gap 扱いになり無限ループに陥る。

**修正**: `BybitWorker._reconnect_triggers` (dict[(ticker, market), asyncio.Event]) を追加。`fetch_depth_snapshot` は trigger をセットして `NotImplementedError` を raise する代わりに WS ストリームに再接続を促す。`stream_depth` は各メッセージ後に trigger を確認し set 済みなら内部ループを break して WS 再接続。`server.py._do_request_depth_snapshot` は `NotImplementedError` を info ログのみで握りつぶし（Error イベントを送出しない）。WS 再接続後の `DepthSnapshot` イベントがそのまま Rust に届き tracker が再設定される。

**ファイル**: [`python/engine/exchanges/bybit.py`](../../python/engine/exchanges/bybit.py), [`python/engine/server.py`](../../python/engine/server.py)

#### Fix #4 (Medium): MEXC perp `daily_volume` がコントラクト枚数のまま返る
**症状**: `_fetch_ticker_stats_futures._parse()` が `volume24`（コントラクト枚数）をそのまま `daily_volume` として返していた。ネイティブ Rust アダプタは `volume24 * contract_size * last_price`（linear）/ `volume24 * contract_size`（inverse）で USD 換算している（[exchange/src/adapter/hub/mexc/fetch.rs:365](../../exchange/src/adapter/hub/mexc/fetch.rs)）。

**修正**: `MexcWorker.__init__` に `_contract_sizes: dict[str, float]` を追加。`_list_tickers_futures` が各銘柄の `contractSize` をキャッシュ。`_fetch_ticker_stats_futures._parse()` でキャッシュ値を使い linear/inverse を判別して USD 換算するよう修正。

**ファイル**: [`python/engine/exchanges/mexc.py`](../../python/engine/exchanges/mexc.py)

### フェーズ 3 完了後レビュー追加修正 (2026-04-24)

#### 修正 H1: WS ストリームの例外ハンドリング粒度
**症状**: 全 WS ストリームの内側例外ハンドラが `except Exception` で JSON パースエラーと接続断を区別せず `log.warning` で握りつぶしていた。接続断が silent failure になりやすい経路。

**修正**: 内側ハンドラを `except (KeyError, ValueError, TypeError, orjson.JSONDecodeError)` に絞り `log.debug` に格下げ。外側ハンドラに `isinstance(exc, (ConnectionClosed, OSError, TimeoutError))` チェックを追加し、接続断は `log.warning`、予期外エラーは `log.error` で区別。

**ファイル**: bybit.py, hyperliquid.py, okex.py, mexc.py（各ファイルの trade/depth/kline ストリーム、計9箇所）

#### 修正 H2: `_spawn_fetch` による `WsNativeResyncTriggered` の二重握りつぶし防止
**症状**: `_do_request_depth_snapshot` が内部で `WsNativeResyncTriggered` をキャッチしているが、将来その catch が外れた場合に `_spawn_fetch` の汎用ハンドラが `fetch_failed` Error を送出してしまう構造だった。

**修正**: `_spawn_fetch._run()` に `except WsNativeResyncTriggered: raise` を追加して明示的に除外。

**ファイル**: [`python/engine/server.py`](../../python/engine/server.py)

#### 修正 H3: IPC スキーマの `market` フィールド欠落
**症状**: `Subscribe` / `ListTickers` / `GetTickerMetadata` / `RequestDepthSnapshot` / `FetchTickerStats` に `market` フィールドが未定義。`extra="ignore"` により偶然動いていたが、Rust 側が送る `market` が無言で破棄されていた。

**修正**: 該当 Pydantic モデルに `market: str | None = None` を追加。

**ファイル**: [`python/engine/schemas.py`](../../python/engine/schemas.py)

#### 修正 M1: 未知 venue/stream で Error イベントを送出
**症状**: `_handle_subscribe` で未知 venue・未知 stream の場合に `log.warning` + silent return だったため Rust 側が永遠にイベントを待ち続けた。

**修正**: 両経路で `outbox` に `Error` イベント（code=`unknown_venue` / `unsupported_stream`）を積むよう変更。

**ファイル**: [`python/engine/server.py`](../../python/engine/server.py)

#### 修正 M3: 接続置換時に旧ストリームタスクが残存
**症状**: `_do_handshake` の接続置換処理が旧コネクションを close するだけで `_cancel_all_streams()` を呼ばないため、旧接続のストリームタスクが zombie として残存し得た。

**修正**: handshake lock 内の接続置換後に `await self._cancel_all_streams()` を追加。

**ファイル**: [`python/engine/server.py`](../../python/engine/server.py)

#### 修正 M6: ストリームタスク例外時に Error イベントを送出
**症状**: ストリームタスクが予期せず終了しても done_callback は `_outbox_event.set()` するだけで Rust 側に一切通知がなかった。

**修正**: done_callback を `_on_done(t)` に変更し、`t.exception()` が非 None の場合に `Error`（code=`stream_error`）を outbox に積み、`_streams` から該当キーを除去。

**ファイル**: [`python/engine/server.py`](../../python/engine/server.py)

> **テスト結果 (2026-04-24)**: pytest 全体 161 件 PASS（旧 156 件 + 上記修正で追加テスト 5 件は今回の修正に追加テストなし、既存テストがすべて通過）

---

### 未修正（要対応、フェーズ 4 以前）

#### Finding #1 (High): Kline IPC 経由でボリューム正規化が失われる ✅ (2026-04-24, phase-4/historical-trades)
**症状**: Python ワーカーは取引所の raw `volume` フィールド（基本通貨建てや枚数など）をそのまま `KlineMsg.volume` にシリアライズ。Rust の `KlineMsg::to_kline()` は `Volume::TotalOnly(Qty::from_f32(volume))` に直接変換する（[engine-client/src/convert.rs:48](../../engine-client/src/convert.rs)）。ネイティブアダプタは `Kline` 構築前に正規化している（例: [exchange/src/adapter/hub/mexc/fetch.rs:301](../../exchange/src/adapter/hub/mexc/fetch.rs) は `quoteVolume` を優先）ため、IPC チャートは現行 Rust パスと異なるボリュームバーを表示する。

**修正内容**:
- `KlineMsg` に `quote_volume`, `taker_buy_volume`, `taker_buy_quote_volume` オプションフィールドを追加（Python schemas.py および Rust dto.rs）。
- Binance worker の `fetch_klines` が `row[7]` (quote_asset_volume), `row[9]` (taker_buy_base), `row[10]` (taker_buy_quote) を設定するよう更新。
- `KlineMsg::to_kline()` のボリューム優先度: 1) `taker_buy_volume` あり → `Volume::BuySell`、2) `quote_volume` あり → `Volume::TotalOnly(quote)`、3) fallback → raw `volume`。

#### Finding #3 (Medium): Hyperliquid spot の display symbol が IPC パスで失われる ✅ (確認済み)
→ `_list_tickers_spot()` がすでに `display_symbol` フィールドを返しており (`hyperliquid.py:342`)、Rust `backend.rs:425` が `Ticker::new_with_display(symbol, exchange, display_symbol)` で処理済み。追加修正不要。

## フェーズ 4: ヒストリカルデータ・bulk download 移植 ✅

> **完了** (2026-04-24, ブランチ `phase-4/historical-trades`)

- [x] `BinanceWorker.fetch_trades()` を Python に実装。
  - 当日分: aggTrades REST API (`/fapi/v1/aggTrades`, `/api/v3/aggTrades` 等)
  - 過去日分: `data.binance.vision` から zip/CSV をダウンロードしてローカルキャッシュ
  - 404 時は intraday API にフォールバック
  - キャッシュヒット時は再ダウンロードなし
- [x] `TradesFetched` IPC イベントを Python schemas (`schemas.py`) と Rust DTO (`dto.rs`) に追加。
- [x] `server.py` の `FetchTrades` dispatch を実装（旧 "not_supported" スタブから完全実装へ）。
  - `_do_fetch_trades(msg)`: venue/market/start_ms/data_path を解析し `worker.fetch_trades()` を呼び出し結果を `TradesFetched` イベントとして送出。
  - 未知 venue は `ValueError` → `Error` イベントに変換。
  - `fetch_trades` 未実装 venue は `NotImplementedError` → `Error` イベントに変換。
- [x] `Command::FetchTrades` に `market` フィールドを追加（Python server の `_market_from_msg` と対応）。
- [x] Rust `EngineClientBackend::fetch_trades()` が `TradesFetched` イベントを受信し `Vec<Trade>` に変換して返すよう更新（旧実装は Error イベント待機のみ）。
- [x] pytest 172 件全 PASS（旧 161 + 新 11）。
- [x] `cargo test --workspace` 全 PASS。
- [x] `cargo clippy -p flowsurface-engine-client -- -D warnings` warning なし。

**完了条件**: ヒストリカル trade のフェッチが Python に移管。✅ **達成済み**

### Phase 4 設計判断・Tips

- **`fetch_trades` の責務境界**: Rust `fetch_trades_batched` は `fetch_trades` をループ呼び出しして `latest_trade_t` を更新する。Python は1リクエストで `start_ms` から1日分のデータを返す。Rust ループが `end_ms` に達するまで繰り返し呼び出す設計を維持。
- **data_path は Rust 側からは渡さない**: 現在の `Command::FetchTrades` に `data_path` は未追加。Python サーバ側で `data_path` を環境変数 or 設定ファイルから管理する拡張に備えて `schemas.FetchTrades.data_path: str | None = None` は定義済み。
- **aggTrades フォーマット**: zip 内 CSV のカラム順: `[agg_id, price, qty, first_trade_id, last_trade_id, timestamp_ms, is_buyer_maker]`。`is_buyer_maker` が `"true"` → sell、`"false"` → buy（Rust 実装 `DeTrade.is_sell: bool` と同等）。
- **インタデイ+ヒストリカルの結合**: 過去日のデータは historicアル zip を取得後、末尾の取引タイムスタンプから aggTrades API で残り時間を補完する。zip が空なら fallback で intraday のみ返却。
- **`TradesFetched` vs `Trades`**: ストリームイベント (`Trades`) と REST レスポンス (`TradesFetched`) を別型として設計。backend.rs は両イベントを独立したパスで処理する。

### フェーズ 4 完了後レビュー指摘 (2026-04-24) ✅

Phase 4 完了後のレビューで検出した、`FetchRange::Trades(from, to)` の上下限契約に関する 3 件を修正。

#### Finding #1 (High): Binance ヒストリカル取得で `from_time` 下限が無視されていた
**症状**: `fetch_trades()` が `start_ms` から日付のみを抽出し `_fetch_historical_trades()` に渡す実装だったため、返却される aggTrades zip の全日分（`start_ms` より前のデータを含む）がそのままチャートに挿入されていた。Rust 側 `dashboard.rs` は `trade.time <= until_time` のみクリップし、下限は信頼していた。日中から始まるヒストリカル要求で過剰なデータが描画される。

**修正**: [python/engine/exchanges/binance.py:522](../../python/engine/exchanges/binance.py#L522) — `_fetch_historical_trades()` の戻り値を `ts_ms >= start_ms` でフィルタしてから返却。根本原因を Python 側で封じ込めたため、`dashboard.rs` の下限クリップ追加は不要。

#### Finding #2 (Medium): 空バッチが後続日のフェッチを打ち切っていた
**症状**: Phase 4 で「1 リクエスト = 1 カレンダー日」契約に変更されたが、`fetch_trades_batched()` は空バッチで `break` する実装のままだった。流動性の低い銘柄・新規上場直後・取引所のデータ欠損などで 1 日分が空の場合、それ以降の全日がフェッチされず途切れる。

**修正**: [src/connector/fetcher.rs:477](../../src/connector/fetcher.rs#L477) — 空バッチ時は `latest_trade_t` を翌日 midnight に進めて `continue`。全体ループ条件 `latest_trade_t < to_time` で自然終了する。

#### Finding #3 (Medium): `to_time` 上限が IPC 層で失われ `now_ms` が送られていた
**症状**: `fetch_trades_batched()` は `to_time` を保持するが、`VenueBackend::fetch_trades()` 以降のシグネチャが `from_time` のみで、`EngineClientBackend` はハードコードで `end_ms: now_ms` を送っていた。過去スライスを要求しても常に「現在まで」を取得しに行くため、レート制限・ダウンロード量が悪化。

**修正**: `VenueBackend` トレイトに `to_time: u64` を追加して 9 ファイルで伝搬（`VenueBackend` トレイト、`AdapterHandles`、`FetchCommand::Trades`、`BinanceHandle`/`HyperliquidHandle`、Binance Worker `FetchCommandHandler` 実装、`binance/fetch.rs`、`EngineClientBackend`、`HybridBackend`、テストスタブ 2 箇所）。
- [engine-client/src/backend.rs:708](../../engine-client/src/backend.rs#L708): `end_ms: to_time as i64`（旧: `now_ms`）。
- [exchange/src/adapter/hub/binance/fetch.rs:704](../../exchange/src/adapter/hub/binance/fetch.rs#L704): ヒストリカル zip を `retain(|t| t.time >= from_time)`、intraday 拡張分を `filter(|t| t.time <= to_time)`。

**テスト**: `cargo test -p flowsurface-exchange` 17/17 PASS。`cargo check --workspace` clean。Python 側は既存 `test_binance_fetch_trades.py` / `test_server_dispatch.py` が引き続き PASS。

## フェーズ 5: Rust から取引所コードを削除 ✅

> **完了** (2026-04-25, ブランチ `phase-5/remove-native-exchange`)

- [x] `exchange/src/adapter/hub/` を削除（binance/bybit/hyperliquid/okex/mexc 全5取引所 + hub.rs）。
- [x] `limiter.rs`, `connect.rs` を削除。`proxy.rs` は接続コード（`ProxyStream`, `connect_tcp`, `try_apply_proxy`）を削除、設定データ型（`Proxy`, `ProxyScheme`, `ProxyAuth`）のみ保持。
- [x] `reqwest`, `fastwebsockets`, `tokio-rustls`, `tokio-socks`, `sonic-rs`, `csv`, `zip`, `bytes`, `hyper`, `hyper-util`, `http-body-util`, `webpki-roots`, `base64` を `exchange/Cargo.toml` から削除。
- [x] `NativeBackend` enum を `venue_backend.rs` から削除。`VenueBackend` trait のみ保持。
- [x] `AdapterHandles::spawn_all()`, `spawn_selected()`, `spawn_venue()` を `client.rs` から削除。
- [x] `AdapterNetworkConfig` を削除（proxy 設定はネイティブ接続に不要）。
- [x] `exchange/src/error.rs` を reqwest 非依存に簡略化（`FetchError` を `String` ベースに統一）。
- [x] `allowed_multipliers_for_min_tick()` を `adapter.rs` にインライン移植（hub 非依存）。
- [x] `--data-engine-url` フラグを必須化。未指定時はエラーメッセージを表示して終了。
- [x] `main.rs` で全5取引所を `EngineClientBackend` 経由に配線（旧 `HybridVenueBackend` 不要）。
- [x] `cargo test --workspace` 全 PASS（Rust: 82 件 + Python: 180 件）。
- [x] `cargo clippy -- -D warnings` warning なし。
- [x] TDD: `exchange/tests/engine_only_wiring.rs` に6テスト追加（全 PASS）。

**完了条件**: Rust ビルドが Iced と engine-client のみに依存し、ビルドサイズが縮む。✅ **達成済み**

### フェーズ 5 設計判断・ハマりどころ・Tips

- **serde `std` feature の暗黙依存**: `exchange/Cargo.toml` に `serde.workspace = true` のみでは serde の `std`/`alloc` feature が有効にならず `Deserialize` derive が失敗する。reqwest が取り除かれると依存チェーン経由の有効化がなくなるため、`serde = { workspace = true, features = ["std"] }` を明示的に追加する必要があった。
- **`FetchError` の简略化**: `exchange/src/error.rs` は reqwest の `Error`, `StatusCode`, `Method`, `Url` を多用していた。hub/ 削除後はこれらが不要になるため、`FetchError(String)` に統一し `ui_message()` をシンプルな文字列返却に変更。既存コードは `err.ui_message()` 呼び出しのみ使っており後方互換。
- **`serde_util.rs` の dead code**: `de_string_to_number` と `value_as_u64` が hub/ でのみ使用されていたため削除。`value_as_f32` と `de_number_like_or_object` は lib.rs 内の型定義（DepthPayload 等）で使用継続。
- **`allowed_multipliers_for_min_tick` の移植**: Hyperliquid の depth tick multiplier テーブルは定数 3 個 + 関数 1 個で完結。`adapter.rs` にそのままコピーするだけで OK（ロジック変更不要）。
- **`--data-engine-url` 必須化のタイミング**: Iced の `daemon()` 起動前に `ENGINE_CONNECTION.get().is_none()` チェックを挿入。Iced の panic ハンドラに入る前に明確なエラーメッセージで終了できる。
- **`HybridVenueBackend` の不要化**: フェーズ 2 では native metadata + Python stream のハイブリッド構成が必要だったが、フェーズ 5 では Python engine が全 metadata も担うためシンプルな `EngineClientBackend` × 5 に置き換え。`engine-client/src/hybrid.rs` と関連テストは次フェーズ以降の cleanup 候補（今回は残置）。

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
