# nautilus_trader 統合: アーキテクチャ

## 1. 配置原則

```
┌─────────────────────────────────────────────────────────┐
│ Rust (flowsurface 本体, iced)                            │
│  ├─ HTTP API (port 9876)        ← 不変条件（外向き契約）  │
│  ├─ EventStore                  ← Klines/Trades 履歴の真実 │
│  ├─ exchange/ (暗号資産 adapter) ← データ取得用に役割を絞る  │
│  └─ engine-client/              ← Python ワーカーへの IPC  │
└────────────────┬────────────────────────────────────────┘
                 │ IPC (stdin/stdout JSON, schema 1.4)
┌────────────────▼────────────────────────────────────────┐
│ Python (engine プロセス)                                  │
│                                                          │
│  既存ワーカー（venue 直結）             nautilus ワーカー   │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │ python/engine/       │  │ python/engine/nautilus/  │ │
│  │   exchanges/         │  │  ├─ engine_runner.py     │ │
│  │   ・hyperliquid      │  │  ├─ data_loader.py       │ │
│  │   ・bybit            │  │  ├─ jquants_loader.py ⭐ │ │
│  │   ・tachibana (P1)   │  │  ├─ strategies/          │ │
│  └──────────────────────┘  │  ├─ clients/             │ │
│                            │  │   ├─ tachibana_data ⭐│ │
│                            │  │   └─ tachibana.py     │ │
│                            │  └─ narrative_hook.py    │ │
│                            └────────────┬─────────────┘ │
│                                          │ in-process    │
│                            ┌─────────────▼─────────────┐ │
│                            │ nautilus_trader (PyPI)    │ │
│                            │  ・BacktestEngine (replay)│ │
│                            │  ・LiveExecutionEngine    │ │
│                            │  ・LiveDataEngine ⭐      │ │
│                            │  ・Strategy / OrderFactory│ │
│                            │  ・BarAggregator ⭐       │ │
│                            └───────────────────────────┘ │
└──────────────────────────────────────────────────────────┘

⭐ = N1 / N2 で新設
```

### 責務分割

| 責務 | 所在 | 備考 |
| :--- | :--- | :--- |
| HTTP API のレスポンス組立 | **Rust** | 既存 `replay_api.rs` を維持 |
| 履歴データの正本（Klines） | **Rust `EventStore`** | nautilus にコピー注入 |
| **過去歩み値・分足の正本（J-Quants）** | **`S:\j-quants\` 直読み** | `python/engine/nautilus/jquants_loader.py` がストリーム読込 |
| バックテスト実行（replay） | **Python `nautilus.BacktestEngine`** | Rust から「リプレイ開始」コマンドを受けて起動 |
| ライブ発注の意思決定 | **Python `Strategy`** | ユーザー実装（N0/N1 は組み込みのみ） |
| ライブ発注の送信 | **Python `LiveExecutionClient`** | venue ごとに 1 実装 |
| **ライブ歩み値配信** | **Python `LiveDataClient`（N2 で新設）** | 立花 FD frame → `TradeTick` |
| 立花の認証・session 管理 | **Python（既存 Phase 1 コード）** | 重複実装しない |
| ナラティブの記録 | **Python `narrative_hook.py`** | nautilus `Strategy.on_event` から `/api/agent/narrative` を叩く |
| keyring 永続化 | **Rust `data::config`** | 既存どおり |
| **REPLAY pane の自動生成と identity 管理** | **Rust UI（iced）** | chart pane は `(mode, instrument_id, pane_kind, granularity?)`、order list / buying power pane は `(mode, pane_kind)` で identity を取り、`/api/replay/load` 成功イベントを契機に生成判定を行う |
| **REPLAY 注文一覧 view** | **Rust UI（iced）** | `OrderListStore` を venue で 2 view に分割。REPLAY view は `venue="replay"` のイベントのみ反映、バナー付き |
| **REPLAY 買付余力 view** | **Rust UI（iced）** | `BuyingPowerStore` を venue で 2 view に分割。REPLAY view は `EngineEvent::ReplayBuyingPower` のみ反映、`CLMZanKaiKanougaku` を一切参照しない |
| **REPLAY portfolio snapshot** | **Python `python/engine/nautilus/portfolio_view.py`（新設）** | nautilus `Portfolio` から `cash` / `equity` / `mark_to_market` を 1 秒間隔で算出 |

**Rust 直結（NativeBackend）は使わない**: `EngineClientBackend` 一本に統一。

## 2. プロセス起動とハンドシェイク

既存 IPC は `Command::Hello` の `schema_major / schema_minor` 構成。本計画は **schema 1.4**。

1. Rust → Python: `Hello { schema_major: 1, schema_minor: 4, mode: "live" | "replay", capabilities: { nautilus: true } }`  // `mode` は N1.13 / D8 起動時固定
2. Python → Rust: `Ready { schema_major: 1, schema_minor: 4, mode: "live" | "replay", capabilities: { nautilus: { backtest: true, live: false_until_n2 } } }`  // `mode` は N1.13 / D8 起動時固定
3. Rust → Python: `SetVenueCredentials`（既存）
4. Rust → Python: `Command::StartEngine { engine, ... }`（§3 参照）
   - `engine: Backtest` + `Hello.mode="replay"` → `BacktestEngine` 起動 + J-Quants ロード（`/api/replay/load` → §4）
   - `engine: Live` + `Hello.mode="live"` → **N1 では**既存 Phase 1 の立花 EVENT WS 閲覧経路のみ起動し、nautilus `LiveExecutionEngine` / `LiveDataEngine` は stub のまま。**N2 から** live engine 起動に切り替える

## 3. 新規 IPC メッセージ

[engine-client/src/dto.rs](../../../engine-client/src/dto.rs) に以下を追加（schema 1.4）。**`SubmitOrder` / `Order*` 系は order/ schema 1.3 で定義済み**。本計画で追加するのは backtest engine ライフサイクル、replay データロード、speed 制御、overlay、REPLAY 買付余力:

```rust
pub enum Command {
    StartEngine {
        request_id: String,
        engine: EngineKind,          // Backtest | Live
        strategy_id: String,
        config: EngineStartConfig,   // ticker, range, initial_cash, granularity
    },
    StopEngine { request_id: String, strategy_id: String },
    LoadReplayData {                 // ⭐ N1 新設
        request_id: String,
        instrument_id: String,       // "1301.TSE"
        start_date: String,          // "2024-01-01"
        end_date: String,            // "2024-01-31"
        granularity: ReplayGranularity, // Trade | Minute | Daily
    },
    // ⭐ N1 新設: streaming ループ間の wall-clock pacing を変える（D4 / D7）
    // Pause / Resume / Seek は N1 では追加しない（Q14 で再評価）
    SetReplaySpeed { request_id: String, multiplier: u32 },   // 1 | 10 | 100
}

pub enum ReplayGranularity { Trade, Minute, Daily }

pub enum EngineEvent {
    EngineStarted { strategy_id: String, account_id: String, ts_event_ms: i64 },
    EngineStopped { strategy_id: String, final_equity: String, ts_event_ms: i64 },
    ReplayDataLoaded {               // ⭐ N1 新設
        strategy_id: String,
        bars_loaded: u64,
        trades_loaded: u64,
        ts_event_ms: i64,
    },
    PositionOpened { strategy_id, venue, instrument_id, position_id, side, opened_qty, avg_open_price, ts_event_ms },
    PositionClosed { strategy_id, venue, instrument_id, position_id, realized_pnl, ts_event_ms },
    // ⭐ N1 新設: OrderFilled 由来・narrative_hook が自動送出（D6）
    ExecutionMarker {
        strategy_id: String,
        instrument_id: String,
        side: OrderSide,             // Buy | Sell
        price: String,               // 文字列精度規約
        qty: String,
        ts_event_ms: i64,
        client_order_id: String,
    },
    // ⭐ N1 新設: Strategy.emit_signal(...) による明示送出（D6）
    StrategySignal {
        strategy_id: String,
        instrument_id: String,
        signal_kind: SignalKind,     // EntryLong | EntryShort | Exit | Annotate
        side: Option<OrderSide>,
        price: Option<String>,       // 注釈のみで価格を持たないケースあり
        ts_event_ms: i64,
        tag: Option<String>,         // Annotate 時の任意ラベル
        note: Option<String>,
    },
    // ⭐ N1 新設: REPLAY 買付余力（D9.6、schema 1.4）
    ReplayBuyingPower {
        strategy_id: String,
        cash: String,                // 文字列精度規約
        buying_power: String,        // N1 は cash と同値（現物のみ）
        equity: String,              // cash + Σ position MTM
        ts_event_ms: i64,            // 仮想時刻
    },
}
```

**replay 中の市場データは既存 `EngineEvent::Trades` / `EngineEvent::KlineUpdate` を再利用する**（D5）。新規 market data event は足さない。`engine_runner.py` の data feed 直前で「Rust 向けにも 1 件複製送出」する経路を 1 箇所追加するのみ。venue タグは `"replay"`。

**精度保持規約（H2）**: 数量・価格・PnL は **文字列**で運ぶ。`f64` 変換は Rust UI レンダラ層が最後に行う。

**venue フィールド（H1）**: ポジション系イベントには `venue` を必須化。値は IPC スキーマ安定名（`"tachibana"` / `"replay"`）のみ。

**clock 注入（H4 / Q3 決定）**: `AdvanceClock` Command は **実装しない**。`BacktestEngine.run(start, end)` で自走（[open-questions.md Q3](./open-questions.md#q3)）。

## 4. データフロー（replay モード）

**`/api/replay/load` 成功 → Rust UI が Tick + Candlestick + 注文一覧 + 買付余力 の 4 種 pane を自動生成（identity 重複なら skip）**
**→ それぞれが対応する IPC（`Trades` / `KlineUpdate` / `Order*` / `ReplayBuyingPower`）を venue=replay で購読する**
（chart pane の identity = `(mode=replay, instrument_id, pane_kind, granularity?)`、注文一覧 / 買付余力は `(mode=replay, pane_kind)`、D9 参照）

```
Rust HTTP /api/replay/load
   │ POST {instrument_id: "1301.TSE", start_date, end_date, granularity: "trade"}
   ▼
engine_client.send(Command::LoadReplayData { ... })
   ▼
Python nautilus/jquants_loader.py
   │ ストリーム読込: gzip.open("S:/j-quants/equities_trades_202401.csv.gz")
   │ 銘柄フィルタ + 期間フィルタ
   │ Code "13010" → InstrumentId("1301.TSE")（末尾 0 切り）
   ▼
TradeTick リスト → BacktestEngine.add_data(ticks)
   ▼
Strategy.on_trade_tick(tick)  ←─ ★Strategy はここを実装する★
   │ （必要なら BarAggregator 経由で on_bar も発火）
   │
   │ ユーザー判断: BacktestEngine.submit_order(...)
   ▼
nautilus SimulatedExchange
   │ TradeTick の価格・サイズで約定判定（板なしなので last-trade-fill モデル）
   ▼
Strategy.on_event(OrderFilled)
   │ narrative_hook.record(Outcome) ──→ HTTP /api/agent/narrative
   ▼
Event::OrderFilled → IPC → Rust → HTTP レスポンス
   │
   ├─ OrderFilled → ExecutionMarker → iced execution layer
   └─ Strategy.emit_signal → StrategySignal → iced signal layer
```

**replay モードの約定判定**: 板履歴がないため、`SimulatedExchange` の matching engine は **直近 TradeTick の last_price ベース**で fill する。指値は `last_price <= limit_price`（買い）/ `>= limit_price`（売り）で fill する単純モデル。これは現実の板状況より楽観的だが、戦略の方向性検証には十分（[spec.md §3.5.3](./spec.md#353-既知のlivereplay差分) で利用者に明示）。

**REPLAY 中は立花 `CLMZanKaiKanougaku` HTTP 呼び出しを `order_router.py` で skip する**（D9.6 の誤参照防止コードガード）。

## 5. データフロー（live モード・立花）

```
立花 EVENT WebSocket (FD frame)
   ▼
python/engine/exchanges/tachibana_ws._FdFrameProcessor
   │ trade dict + depth dict を合成
   ▼
python/engine/nautilus/clients/tachibana_data.py  ⭐ N2 新設
   │ trade dict → nautilus TradeTick に変換
   │ LiveDataEngine.process(tick)
   ▼
Strategy.on_trade_tick(tick)  ←─ ★replay と同一インタフェース★
   │
   │ ユーザー判断: LiveExecutionEngine.submit_order(...)
   ▼
TachibanaExecutionClient (= python/engine/nautilus/clients/tachibana.py)
   │ tachibana_orders.submit_order(...) に委譲（重複実装しない）
   │ POST CLMKabuNewOrder
   ▼
EVENT WebSocket (p_evt_cmd=EC)
   ▼
tachibana_event_bridge._parse_ec_frame → nautilus OrderFilled
   ▼
Strategy.on_event → narrative_hook
   ▼
Event::OrderFilled → IPC → Rust → UI 反映
```

## 6. live / replay 互換のための共通インタフェース ⭐ 2026-04-28 追記

ユーザー Strategy が `on_trade_tick(tick)` を実装すれば、以下のどちらの経路でも同じハンドラが呼ばれる:

```python
class MyStrategy(Strategy):
    def on_trade_tick(self, tick: TradeTick):
        # tick.instrument_id, tick.price, tick.size, tick.ts_event は live/replay で同じ意味
        ...

    def on_bar(self, bar: Bar):
        # BarAggregator が tick から作るか、replay モードで J-Quants 直接投入
        ...
```

**禁止メソッド（[spec.md §3.5.2](./spec.md#352-戦略コード規約)）**:
- `on_order_book_*` — replay で板を作らないため
- `on_quote_tick` — 同上

これらは N1.8 の lint で検出する。

### 6.1 再生コントロールと実行モデル

D4 の写像。N1 では実行モデルを 2 経路に分け、どちらでも仮想時刻 `tick.ts_event` の独立性を保つ。

- **headless / 決定論性検証**: 既存の `BacktestEngine.run(start, end)` 自走をそのまま使う（N0.6 / N1.9 の wall clock 非参照テストはこの経路で維持）
- **UI 駆動 viewer**: streaming=True ループ（[Tpre.1 spike](./implementation-plan.md#tpre1-clock-注入-feasibility-プロトタイプh4--完了-2026-04-26) 案 A）を採用し、bar/tick を 1 件ずつ `add_data([item])` → `run(streaming=True)` → `clear_data()` で進める
- **`SetReplaySpeed` の作用範囲**: streaming ループ間の sleep のみを操作する。pacing 式は D7 の

  ```
  sleep_sec = min(max(dt_event_sec, MIN_TICK_DT_SEC) / multiplier, SLEEP_CAP_SEC)
  ```

  - `MIN_TICK_DT_SEC = 0.001`（同一マイクロ秒バーストでも UI 描画整合のため最低 1ms 刻む）
  - `SLEEP_CAP_SEC = 0.200`（1 sleep の上限）
  - セッション境界（前場-後場 11:30〜12:30 / 引け後 / 営業日跨ぎ）は multiplier に依存せず `sleep=0` で**即時通過**
  - 仮想時刻 `tick.ts_event` は J-Quants オリジナル値をそのまま流し、wall clock から独立で multiplier にも依存しない
- **Pause / Seek は本フェーズでは実装しない**。streaming ループの suspend / 中間 tick の skip は決定論性テストの仮定や fill in-flight UX に影響するため、Q14（open-questions.md）で N2 以降に再評価する

## 7. 既存計画との衝突点と整理

| 衝突点 | 解消方針 |
|---|---|
| Phase 2「自作 Virtual Exchange Engine」 | **破棄**。nautilus `BacktestEngine` で代替 |
| 立花 Phase 2 発注経路 | **書き直し**。`tachibana_nautilus.py` 実装タスクに置換 |
| Rust 側発注 adapter（暗号資産） | **段階廃止**。Phase N3 で nautilus 側に新規実装後、Rust の発注経路は削除 |
| ナラティブの `outcome` 自動連携 | **そのまま**。書き込み元が `FillEvent` から nautilus `OrderFilled` に変わる |
| **N0 の EventStore 直読み Bar ローダ** | **両立**。日足長期テスト用に N0 ローダは残し、N1 の J-Quants tick ローダを並列追加 |

## 8. Python 単独モードへの含み

Rust（iced）を外す将来モードでは:

```
Python: nautilus_trader + jquants_loader + 既存 venue worker + narrative store + (任意) FastAPI
```

- nautilus 関連コードは `engine-client` IPC を介さず直接 Python から叩けるよう、`engine_runner.py` に CLI / library 二系統のエントリを切る
- 立花 Phase 1 の tkinter ログイン UI は **subprocess 隔離経由**で再利用（[tachibana/architecture.md §7.3](../✅tachibana/architecture.md#73-プロセスモデル-ログインヘルパー-subprocess)）
- J-Quants ローダは Rust に依存しないので Python 単独モードで完全に独立して動く
