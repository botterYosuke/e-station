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

**完了条件**: Python のみで Binance のリアルタイム trade / depth / kline / OI を取得・配信でき、depth の gap 検知と再同期が動作する。 → **達成済み**（pytest 30件全 PASS）

## フェーズ 2: Rust 側に engine-client を実装し Binance を切替

> **部分完了** (2026-04-24, ブランチ `phase-1/python-data-engine`)

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
- [ ] Binance futures WS レート制限解除後に `test_trade_stream.py` で Trades 受信を確認し、GUI で chart 描画を目視確認。
- [ ] レイテンシ・CPU 使用率の実測比較（Python spawn モード配線後に実施）。
- [ ] 障害試験（Python kill → 自動復旧 → 板再同期の手動確認、spawn モード配線後に実施）。

> **現況（2026-04-24）**: IPC 接続・REST フェッチは動作確認済み。Binance futures WebSocket
> (`fstream.binance.com`) がデバッグセッション中の過剰接続により一時レート制限中のため
> chart 描画の目視確認が保留。spot WS (`stream.binance.com:9443`) は同一マシンから正常受信
> 確認済みでありコード上の問題ではない。レート制限解除後に再試験すること。

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

## フェーズ 3: 残り取引所の Python 移植

優先順（取引所の安定度・利用頻度で並べ替え可）:

- [x] Bybit ✅ (2026-04-24)
- [ ] Hyperliquid
- [ ] OKX
- [ ] MEXC

各取引所ごとに：
1. `python/engine/exchanges/<venue>.py` 実装
2. レート制限の移植
3. 統合テスト（Rust 側 UI で動作確認）

**完了条件**: 全 5 取引所が Python 経由で動作。

### Bybit 実装詳細（2026-04-24 完了）

- **実装ファイル**: [`python/engine/exchanges/bybit.py`](../../python/engine/exchanges/bybit.py)
- **テスト**: `python/tests/test_bybit_rest.py` (9件) + `python/tests/test_bybit_depth_sync.py` (10件) = 計 19件全 PASS
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

#### Tips

- **Bybit OI は linear のみ**: `category=linear` 固定。inverse 板の OI は API 仕様が異なるため空リストを返す。
- **Depth REST snapshot**: `RequestDepthSnapshot` op 対応のため `GET /v5/market/orderbook?category={cat}&symbol={sym}&limit=200` を `fetch_depth_snapshot` で実装。`result.u` を `last_update_id` として使用。
- **Depth level**: `orderbook.200` トピックを使用（200レベル、100ms 更新）。更小レベル (50) も選択可。
- **ticker_stats volume**: Bybit の `volume24h` は base asset 単位のため、`volume24h * lastPrice` で USD 換算している。

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
