# nautilus_trader 統合: 仕様

## 1. ゴール

1. **リプレイ機能（replay モード）**: nautilus `BacktestEngine` を使い、**J-Quants 過去データ**（歩み値・分足・日足）を投入して決定論的にバックテストできる
2. **発注機能（live モード）**: nautilus `LiveExecutionEngine` を使い、立花証券（株式現物・N2）と既存暗号資産 venue（N3 以降）に実弾で発注できる
3. **live / replay 互換性**: ユーザー Strategy は `on_trade_tick` を中心に書けば、live / replay の **どちらでも同一コードがそのまま動く**ことを保証する
4. **ナラティブ連携**: nautilus `Strategy` の意思決定が自動で Phase 4a のナラティブ Store に記録される

## 2. スコープ

### 2.0 Phase N-pre — feasibility 確認と前提固め（実装ゼロ）✅ 完了 2026-04-26

[open-questions.md](./open-questions.md) の Q1 / Q3 / Q5 / Q6 / Q7 / Q8 すべて Resolved。詳細は [implementation-plan.md §Phase N-pre](./implementation-plan.md#phase-n-pre-feasibility-と前提固め実装ゼロ)。

### 2.1 Phase N0 — エンジン同梱と日足 Bar ハロー戦略（MVP）✅ 完了 2026-04-26

- `python/engine/` に `nautilus_trader` 依存を追加（`pyproject.toml` / `uv.lock`）
- 新規ワーカー `python/engine/nautilus/` を新設し、`BacktestEngine` を起動して既存 `EventStore` から **日足 Bar** を投入する `data_loader.klines_to_bars()` を実装
- `BuyAndHold` サンプル戦略で PnL とエクイティカーブが headless で返ることを検証
- **発注は出さない**。`SimulatedExchange` のみ
- 決定論性テスト（同一入力 → ビット一致）が GREEN

**N0 は Bar ベースの足場固め**であり、N1 以降の **TradeTick ファースト**設計に置換する前提で完了している（壊れない範囲で残す）。

### 2.2 Phase N1 — TradeTick 抽象 + J-Quants 投入 + REPLAY 仮想注文 + ナラティブ API

本フェーズで **replay モード**が使えるようになる。N1 のキー設計判断:

#### 2.2.1 TradeTick 一本化（ストラテジー互換の核）

ユーザー Strategy が触る一次データを **`TradeTick`（歩み値）** に統一する。Bar が必要な戦略は nautilus 標準の `BarAggregator` で `TradeTick → Bar` を内部生成する（live / replay 同一ロジック）。

```
live  : 立花 FD frame  → tachibana_ws._FdFrameProcessor → trade dict → TradeTick → Strategy.on_trade_tick
replay: J-Quants CSV   → JQuantsTradeLoader            → TradeTick (直接)        → Strategy.on_trade_tick
```

**Strategy 開発規約（不変条件、§3.5 で詳述）**:
- ✅ 推奨: `on_trade_tick(tick)` をオーバーライド
- ✅ 許可: `on_bar(bar)` をオーバーライド（live は `BarAggregator` 経由、replay は直接 J-Quants Bar 投入）
- ❌ 禁止（replay 互換のため）: `on_order_book_*` / `on_quote_tick` のオーバーライド

#### 2.2.2 J-Quants ローダ実装

| データ種別 | ファイル例 | nautilus 型 | 備考 |
|---|---|---|---|
| 歩み値 | `S:\j-quants\equities_trades_YYYYMM.csv.gz` | `TradeTick` | 1 行 = 1 約定。マイクロ秒 timestamp |
| 分足 | `S:\j-quants\equities_bars_minute_YYYYMMDD.csv.gz` | `Bar`（1-MINUTE-LAST-EXTERNAL） | 補助。tick → 分足集約の sanity check に使う |
| 日足 | `S:\j-quants\equities_bars_daily_YYYYMM.csv.gz` | `Bar`（1-DAY-LAST-EXTERNAL） | N0 互換。長期テスト用 |

詳細は [data-mapping.md §1.1〜§1.3](./data-mapping.md#1-tradetick-歩み値) と §8。

#### 2.2.3 REPLAY モード仮想注文の統合

[docs/plan/✅order/](../✅order/) からの引き取り:

- `POST /api/order/submit` `/api/order/modify` `/api/order/cancel` を REPLAY モード時に `SimulatedExchange` 経由にルーティング
- `python/engine/order_router.py` 新設で live / replay を分岐:
  - live → `tachibana_orders.submit_order(...)` → 立花 HTTP
  - replay → `BacktestExecutionEngine.process_order(...)` → SimulatedExchange
- 発注入力 UI（Python tkinter）は replay モード用文言に切替（[wiki UX](../../wiki/orders.md#replay-モード中の動作)）:
  - バナー例「⏪ REPLAYモード中 — 実注文は送信されません」
  - 確認文言「仮想注文確認」
  - 第二暗証番号 modal を **出さない**
- iced は監視・表示のみを担い、注文入力責務は持たない（Q7 決定）
- 約定通知イベント型は live と同じ IPC `Event::Order*` を再利用するが、UI ストアは
  `venue="replay"` で view を分離し、REPLAY 注文一覧・REPLAY 買付余力にのみ反映する
- 監査ログ WAL は `tachibana_orders_replay.jsonl` に分離
- `client_order_id` の名前空間も live / replay で分離

#### 2.2.4 既存 HTTP API の差し替え

- 既存 `/api/replay/order` `/api/replay/portfolio` を nautilus `BacktestEngine` 経由で実装（API 契約は変えない）
- 既存 `FlowsurfaceEnv`（Gymnasium）は HTTP 越しなのでそのまま動く
- `POST /api/agent/narrative` を新設（H5）

#### 2.2.5 N1.11〜N1.16 追加スコープ（2026-04-28 確定、UI 役割境界の確定に伴う）

詳細は [./archive/replay-ui-role-revision-2026-04-28.md](./archive/replay-ui-role-revision-2026-04-28.md) を参照。

- **N1.11（新設）** Replay 再生 speed コントロール（streaming=True 経路）+ IPC
  `Command::SetReplaySpeed` を schema 1.4 に追加。`add_data([item]) → run(streaming=True)
  → clear_data()` を 1 件ずつ回し、ループ間に
  `sleep_sec = min(max(dt_event_sec, MIN_TICK_DT_SEC) / multiplier, SLEEP_CAP_SEC)`
  （`MIN_TICK_DT_SEC=0.001`、`SLEEP_CAP_SEC=0.200`）で sleep。前場-後場・引け後・営業日
  跨ぎは sleep=0 で即時通過。**Pause / Seek は N1 では含めない**（Q14 で再評価）
- **N1.12（新設）** `EngineEvent::ExecutionMarker`（OrderFilled 由来・`narrative_hook.py`
  が自動送出）と `EngineEvent::StrategySignal`（Strategy が `emit_signal(kind, side,
  price, tag, note)` で明示送出。`signal_kind ∈ {EntryLong, EntryShort, Exit, Annotate}`）
  を追加。iced 側 chart pane に execution layer / signal layer の 2 レイヤーを重ねる。
  `signal_kind` の wire 表現（enum vs `kind: String`）は [Q13](./open-questions.md#q13)
  で確定するまで暫定 enum 実装、後方互換性を破らない
- **N1.13（新設）** 起動時モード固定の CLI 引数 `--mode {live|replay}` を追加（必須・
  デフォルトなし、D8 起動時固定の踏襲、デフォルトなし）。Hello に mode を載せて
  Python 側 NautilusRunner に渡し、mode と `StartEngine.engine` の不一致は `ValueError`
  で拒否。**ランタイム切替コマンドは追加しない**。`--mode live` で起動しても N1 では
  nautilus `LiveExecutionEngine` は起動せず、Hello capabilities `nautilus.live=false`
  を維持。ランタイム切替の責務は [Q15](./open-questions.md#q15) で N2 着手前に再評価
- **N1.14（新設）** REPLAY 銘柄追加時に Tick pane と Candlestick(1m) pane を自動生成。
  `ReplayPaneRegistry` で identity = `(mode=replay, instrument_id, pane_kind,
  granularity?)` を管理し重複生成防止。1 銘柄目は横並び 2 分割、2 銘柄目以降はフォーカス
  pane を縦分割。`MAX_REPLAY_INSTRUMENTS = 4` 超過は HTTP 400。手動 close した自動生成
  pane は同セッション中は再生成しない
- **N1.15（新設）** REPLAY 注文一覧 pane を 1 銘柄目の `/api/replay/load` 成功時に
  自動生成（identity = `(mode=replay, pane_kind=order_list)`、銘柄非依存で 1 つだけ）。
  pane header に `⏪ REPLAY` バナー、`venue="replay"` のイベントのみ反映、live の
  注文一覧を汚染しない。`/api/order/list?venue=replay` を新設、`tachibana_orders_replay.jsonl`
  WAL と整合
- **N1.16（新設）** REPLAY 買付余力表示。新規 IPC `EngineEvent::ReplayBuyingPower
  { strategy_id, cash, buying_power, equity, ts_event_ms }` を schema 1.4 に追加。
  `python/engine/nautilus/portfolio_view.py` を新設し nautilus `Portfolio.account_for_venue(SIM)`
  から 1 秒間隔 + 約定即時のハイブリッドで snapshot 送出。N1 は **現物のみ**
  （`buying_power = cash`）。`order_router.py` に **REPLAY モード時は立花
  `CLMZanKaiKanougaku` HTTP を skip する明示ガード**を入れる（誤参照防止コードガード）

### 2.3 Phase N2 — 立花 ExecutionClient

- `python/engine/exchanges/tachibana_nautilus.py` を新設し、nautilus `LiveExecutionClient` を実装
- **前提**: [docs/plan/✅order/](../✅order/) Phase O0〜O2 が完了
- **`tachibana_nautilus.py` は薄い adapter**。発注ロジック本体は `tachibana_orders.py` を再利用
- `tachibana_event._parse_ec_frame` の戻り値 → nautilus `OrderFilled` / `OrderCanceled` イベントに変換し `LiveExecutionEngine.process_event(...)` に流す
- **デモ環境のみ**。本番は `TACHIBANA_ALLOW_PROD=1` 必須

#### 2.3.1 live モードの TradeTick 接続

- 既存 `tachibana_ws._FdFrameProcessor` の trade dict 出力を nautilus `TradeTick` に変換するブリッジを新設（[data-mapping.md §1.2](./data-mapping.md#12-live-立花-fd-frame--tradetick)）
- `LiveDataClient` 系として `python/engine/nautilus/clients/tachibana_data.py` に置く
- nautilus `LiveExecutionEngine` と `LiveDataEngine` を同時起動

### 2.4 Phase N3 — 暗号資産 venue ExecutionClient（任意）

割愛（既存どおり）。

### 2.5 含めないもの

- nautilus の Rust コアを Rust 側 crate から直接呼び出す
- マルチアカウント・複数戦略の同時実行（N0–N2 では 1 戦略 1 アカウント固定）
- nautilus 標準 adapter（Binance, IB 等）の有効化
- **過去板情報の再構成**: J-Quants に板履歴がないため、replay モードで OrderBook を提供しない（[§3.5](#35-livereplay-互換不変条件) で明示禁止）

## 3. 非機能要件

### 3.1 決定論性

- リプレイ（`run()` 自走経路）は **同じ入力 → 同じ PnL** を保証する。`BacktestEngine` の
  clock を仮想時刻モードで使い、**wall clock を一切参照しない**。本不変条件は `run()`
  自走経路に**限定**する（headless / 決定論性検証用）
- UI 駆動 replay viewer（streaming=True 経路）は wall-clock pacing を許す（speed 1x /
  10x / 100x の sleep を streaming ループ間に挟む）。ただし Strategy が観測する
  仮想時刻（`tick.ts_event`）は wall clock から独立であることを保つ
- 決定論性テスト（N0.6 / N1.9）は **`run()` 自走経路でのみ実施**する。streaming 経路は
  pacing テスト（N1.11）で個別に検証する
- ナラティブの `timestamp` も仮想時刻で記録
- **検証テスト（必須）**: 同一 J-Quants ファイル・同一銘柄で `start_backtest(...)` を 2 回回し、最終 equity / 全 fill timestamps / 全 `OrderFilled.last_price` がビット一致（`tests/python/test_nautilus_determinism_tick.py`、N1 で追加）
- **wall clock 非参照テスト**: `run()` 自走経路で `time.time` / `time.monotonic` /
  `datetime.now` を mock しても結果不変

### 3.2 セキュリティ

- 立花クレデンシャルは Phase 1 と同じ keyring 経路。nautilus には Python メモリ上でだけ渡す
- nautilus persistence（Parquet/SQLite）は **無効化**（`CacheConfig.database = None`）。N2 起動時に `CLMOrderList` から毎回 warm-up
- **Strategy 信頼境界**: ユーザー Strategy ロード（`--strategy-file`）は N0/N1 では許さない（[open-questions.md Q2](./open-questions.md#q2)）

### 3.3 パフォーマンス

- リプレイは headless で動く
- **計測対象**: `start_backtest()` 呼出から `Event::EngineStopped` IPC 受領までの wall clock
- **目標**: 1 銘柄 1 ヶ月分の TradeTick リプレイで 60 秒以内（N1.7 で実測確定）
- **市場時間帯と LiveExecutionEngine**: 立花 venue が閉場帯の間は HTTP API 層で `MARKET_CLOSED` 先行 reject

### 3.4 観測性

- nautilus のログは Python 側 `logging` 経由で既存ロガーに統合
- IPC イベントの欠落を防ぐため、`OrderFilled` / `PositionClosed` 等は nautilus → ワーカー → IPC `Event` の 3 段で必ず flush

### 3.5 live/replay 互換不変条件 ⭐ 新設（2026-04-28）

ユーザー Strategy が live / replay の両方で動くことを保証するため、以下を **コード規約** + **CI 検査**で強制する。

#### 3.5.1 Strategy が依存してよい一次データ

| データ | live で利用可 | replay で利用可 | 注記 |
|---|:---:|:---:|---|
| `TradeTick` | ✅ | ✅ | **第一級**。`on_trade_tick` で受ける |
| `Bar` | ✅ | ✅ | live は `BarAggregator` で tick 集約、replay は J-Quants 直接 or 集約 |
| `OrderBook` / `QuoteTick` | ✅ | ❌ | **replay では提供しない**。J-Quants に板履歴なし |
| `Instrument` | ✅ | ✅ | 静的情報、両方で同じ写像 |
| `AccountState` / `Position` | ✅ | ✅ | live は立花 API、replay は SimulatedExchange |

**この不変条件は計画レベルで確定（2026-04-28）**。

#### 3.5.2 戦略コード規約

- ✅ 推奨: `on_trade_tick(tick: TradeTick)` 中心に書く
- ✅ 許可: `on_bar(bar: Bar)` を併用（`BarType` で粒度指定）
- ❌ 禁止（replay 互換が崩れる）:
  - `on_order_book_*`
  - `on_quote_tick`
  - `tick.aggressor_side` を **意思決定の入力**として参照すること（live と replay で精度・約定挙動が違う、§3.5.3 参照。N1.8 lint で WARNING）

#### 3.5.3 既知の live/replay 差分

| 項目 | live | replay | 影響 |
|---|---|---|---|
| `aggressor_side` | FD frame の quote rule + tick rule 推定。曖昧時 `NO_AGGRESSOR`（Q11-pre のホットフィックス完了後。それ以前は `"buy"` 寄せのバグあり）| J-Quants には情報なし → `NO_AGGRESSOR` 固定 | **両モードで意思決定への使用は非推奨**。nautilus `SimulatedExchange` の fill 判定が `aggressor_side` を参照する場面があり、`NO_AGGRESSOR` の挙動差で live/replay が乖離する |
| timestamp 精度 | FD frame の `dT_TIME` ミリ秒精度 | J-Quants `Time` カラムでマイクロ秒精度 | 高頻度戦略の filling 順序差。互換チェッカは ms 精度で比較 |
| 同一価格・同一時刻の trade | FD frame の単発合成 | J-Quants では複数行ありうる | replay の方が忠実度高い |

#### 3.5.4 互換性 CI 検査（N1.8 で追加）

- **lint**: ユーザー Strategy ファイルの AST を解析し、`on_order_book_*` / `on_quote_tick` の定義があれば fail（`tests/python/test_strategy_compat_lint.py`）
- **smoke**: 組み込み Strategy（`BuyAndHold` 等）を **live mock + replay J-Quants の両方**で走らせ、最終ポジションが論理的に同じ方向（買い後 LONG 保有）になることを確認

## 4. 公開 API（不変条件）

`POST /api/order/*` は [docs/plan/✅order/](../✅order/) で定義済み。本計画 N1/N2 でも **API 契約・冪等性規約・reason_code 体系を変更しない**。N1 では REPLAY モード時のみ Python ディスパッチャが SimulatedExchange に流す。

リプレイ・ナラティブ系の API:

| エンドポイント | body | IPC 写像 |
|---|---|---|
| `POST /api/replay/order` | — | — |
| `GET /api/replay/portfolio` | — | — |
| `GET /api/replay/state` | — | — |
| `POST /api/replay/load` | `{instrument_id, start_date, end_date, granularity: "trade"\|"minute"\|"daily"}` | — |
| `POST /api/replay/control` | `{action: "speed", multiplier: 1\|10\|100}` | `Command::SetReplaySpeed { multiplier }` |
| `POST /api/agent/narrative` | — | — |

注記:
- `POST /api/replay/order`: nautilus `OrderFactory` で発注 → `BacktestEngine` 即時約定判定。legacy パス。新規実装は `/api/order/submit` を REPLAY モードで使う方を推奨。
- `GET /api/replay/portfolio`: nautilus `Portfolio` から position / PnL を取得。
- `GET /api/replay/state`: 既存実装のまま（market data のみ。position / PnL は `/api/replay/portfolio` から）。
- `POST /api/replay/load`: **N1 で新設**。J-Quants ファイルを指定して BacktestEngine にロード。**5 件目以降は 400（`MAX_REPLAY_INSTRUMENTS=4`、D9.4）**。
- `POST /api/replay/control`: **N1 で新設**。**N1 で受理する action は `"speed"` のみ**。`pause` / `seek` を含む他 action は **400 Bad Request**。`play` は提供しない（streaming ループの開始は既存の `StartEngine` に統一）。
- `POST /api/agent/narrative`: **N1 で新設**（H5）。nautilus `Strategy` フックから Python が叩く。

**発注 UI の所在（Q7 決定）**: 発注入力 UI は Python tkinter に統一。iced は監視・表示のみ。

## 5. 依存方針（確定版、2026-04-26）

- nautilus_trader は **Python パッケージとしてのみ**取り込む（`uv add nautilus_trader`）
- **配布形態**: venv 配布。PyInstaller one-binary 化は行わない
- **バージョン pin 戦略**:
  - N0/N1: `>=1.211, <2.0`（検証済み最新: `1.225.0`）
  - N2 完了後: `==1.225.x` 厳密 pin
- **wheel 入手性**: Windows 11 で `1.225.0` 確認済み（2026-04-26）

## 6. J-Quants データ前提（2026-04-28 追記）

- 配置先: `S:\j-quants\`（変更不可。読み取り専用）
- ファイル命名:
  - `equities_bars_daily_YYYYMM.csv.gz`
  - `equities_bars_minute_YYYYMMDD.csv.gz`（日次ファイル）
  - `equities_trades_YYYYMM.csv.gz`
- スキーマ:
  - trades: `Date,Code,Time,SessionDistinction,Price,TradingVolume,TransactionId`
  - minute bars: `Date,Time,Code,O,H,L,C,Vo,Va`
  - daily bars: 既存 N0 互換
- **`Code` 写像（confirmed 2026-04-28）**: 5 桁コード（例 `13010`）の末尾 0 を切って 4 桁化 → `{4 桁}.TSE`（例 `1301.TSE`）
- 板情報は提供されない。replay モードでは OrderBook を作らない（§3.5 不変条件）
