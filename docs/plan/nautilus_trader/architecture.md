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

**Rust 直結（NativeBackend）は使わない**: `EngineClientBackend` 一本に統一。

## 2. プロセス起動とハンドシェイク

既存 IPC は `Command::Hello` の `schema_major / schema_minor` 構成。本計画は **schema 1.4**。

1. Rust → Python: `Hello { schema_major: 1, schema_minor: 4, capabilities: { nautilus: true } }`
2. Python → Rust: `Ready { schema_major: 1, schema_minor: 4, capabilities: { nautilus: { backtest: true, live: false_until_n2 } } }`
3. Rust → Python: `SetVenueCredentials`（既存）
4. Rust → Python: `Command::StartEngine { mode, ... }`（§3 参照）
   - `mode: "backtest"` → `BacktestEngine` 起動 + J-Quants ロード（`/api/replay/load` → §4）
   - `mode: "live"` → `LiveExecutionEngine` + `LiveDataEngine` 起動（venue 閉場中は `start()` 保留）

## 3. 新規 IPC メッセージ

[engine-client/src/dto.rs](../../../engine-client/src/dto.rs) に以下を追加（schema 1.4）。**`SubmitOrder` / `Order*` 系は order/ schema 1.3 で定義済み**。本計画で追加するのは backtest engine ライフサイクルと replay データロード:

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
}
```

**精度保持規約（H2）**: 数量・価格・PnL は **文字列**で運ぶ。`f64` 変換は Rust UI レンダラ層が最後に行う。

**venue フィールド（H1）**: ポジション系イベントには `venue` を必須化。値は IPC スキーマ安定名（`"tachibana"` / `"replay"`）のみ。

**clock 注入（H4 / Q3 決定）**: `AdvanceClock` Command は **実装しない**。`BacktestEngine.run(start, end)` で自走（[open-questions.md Q3](./open-questions.md#q3)）。

## 4. データフロー（replay モード）

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
```

**replay モードの約定判定**: 板履歴がないため、`SimulatedExchange` の matching engine は **直近 TradeTick の last_price ベース**で fill する。指値は `last_price <= limit_price`（買い）/ `>= limit_price`（売り）で fill する単純モデル。これは現実の板状況より楽観的だが、戦略の方向性検証には十分（[spec.md §3.5.3](./spec.md#353-既知のlivereplay差分) で利用者に明示）。

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
