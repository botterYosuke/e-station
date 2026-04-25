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
                 │ IPC (stdin/stdout JSON, schema 1.2+)
┌────────────────▼────────────────────────────────────────┐
│ Python (engine プロセス)                                  │
│                                                          │
│  既存ワーカー（venue 直結）             新ワーカー         │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │ python/engine/       │  │ python/engine/nautilus/  │ │
│  │   exchanges/         │  │  ├─ engine_runner.py     │ │
│  │   ・hyperliquid      │  │  ├─ data_loader.py       │ │
│  │   ・bybit            │  │  ├─ strategy_bridge.py   │ │
│  │   ・tachibana (P1)   │  │  └─ narrative_hook.py    │ │
│  └──────────────────────┘  └────────────┬─────────────┘ │
│                                          │ in-process    │
│                            ┌─────────────▼─────────────┐ │
│                            │ nautilus_trader (PyPI)    │ │
│                            │  ・BacktestEngine         │ │
│                            │  ・LiveExecutionEngine    │ │
│                            │  ・Strategy / OrderFactory│ │
│                            └─────────────┬─────────────┘ │
│                                          │              │
│           ┌──────────────────────────────┴────────────┐ │
│           │ ExecutionClient 実装（venue ごと）          │ │
│           │  ├─ tachibana_nautilus.py (P1 認証を再利用) │ │
│           │  └─ hyperliquid_nautilus.py (Phase N3)     │ │
│           └────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### 責務分割

| 責務 | 所在 | 備考 |
| :--- | :--- | :--- |
| HTTP API のレスポンス組立 | **Rust** | 既存 `replay_api.rs` を維持 |
| 履歴データの正本 | **Rust `EventStore`** | nautilus にコピー注入する。nautilus 側の Parquet キャッシュは使わない |
| バックテスト実行 | **Python `nautilus.BacktestEngine`** | Rust から「リプレイ開始」コマンドを受けて起動 |
| ライブ発注の意思決定 | **Python `Strategy`** | ユーザー実装（or 既定の hand-off ブリッジ） |
| ライブ発注の送信 | **Python `LiveExecutionClient`** | venue ごとに 1 実装 |
| 立花の認証・session 管理 | **Python（既存 Phase 1 コード）** | nautilus には完成済み client を渡すだけ。重複実装しない |
| ナラティブの記録 | **Python `narrative_hook.py`** | nautilus の `Strategy.on_event` から `/api/agent/narrative` を叩く |
| keyring 永続化 | **Rust `data::config`** | 既存どおり |

**Rust 直結（NativeBackend）は使わない**: 立花計画と同じく、`EngineClientBackend` 一本に統一。

## 2. プロセス起動とハンドシェイク

既存 IPC は `Hello { schema_major, schema_minor }` の 2 フィールド構成（[engine-client/src/dto.rs:13-17](../../../engine-client/src/dto.rs#L13-L17)）。立花 Phase 1 が schema 1.2、order/ 計画が schema 1.3 を切るため、**本計画は schema 1.4** とする。

[python-data-engine/spec.md](../✅python-data-engine/spec.md) §4.5 のハンドシェイクに以下を追加:

1. Rust → Python: `Hello { schema_major: 1, schema_minor: 4, ..., capabilities: { ..., nautilus: true } }`
2. Python → Rust: `Ready { schema_major: 1, schema_minor: 4, capabilities: { ..., nautilus: { backtest: true, live: false_until_n2 } } }`
3. Rust → Python: `SetVenueCredentials`（既存）
4. **新**: Rust → Python: `Command::StartEngine { ... }` を送信（下記 §3）
   - `mode: "backtest"` のとき `BacktestEngine` を組み立て、`/api/replay/*` のリクエストを受け付け始める
   - `mode: "live"` のとき `LiveExecutionEngine` を組み立て、登録された ExecutionClient を `start()` する（venue 閉場中は `start()` を保留、[spec.md §3.3](./spec.md#33-パフォーマンス)）

## 3. 新規 IPC メッセージ

[engine-client/src/dto.rs](../../../engine-client/src/dto.rs) に以下を追加（schema 1.4）。

**`SubmitOrder` / `CancelOrder` / `ModifyOrder` および全 `Order*` イベントは [order/architecture.md §3](../order/architecture.md#3-ipc-スキーマ拡張schema-12--13) で schema 1.3 として定義済み**。本計画は **発注系を再定義しない**。本計画で追加するのは backtest engine ライフサイクルのみ:

```rust
pub enum Command {
    // 既存（schema 1.2 / 1.3）...
    StartEngine {
        request_id: String,
        engine: EngineKind,          // Backtest | Live
        strategy_id: String,
        config: EngineStartConfig,   // ticker, timeframe, range, initial_cash, clock_mode, ...
    },
    StopEngine { request_id: String, strategy_id: String },
    AdvanceClock {                   // Q3 案 A 採用時のみ。N-pre プロトタイプで決まる
        request_id: String,
        strategy_id: String,
        ts_event_ms: i64,
    },
}

pub enum EngineEvent {
    // 既存 + order/ 由来の Order* イベント ...
    EngineStarted { strategy_id: String, account_id: String, ts_event_ms: i64 },
    EngineStopped { strategy_id: String, final_equity: String, ts_event_ms: i64 },
    PositionOpened {
        strategy_id: String,
        venue: String,               // "tachibana" / "replay" — 立花と replay 同時稼働時の振り分け（H1）
        instrument_id: String,
        position_id: String,
        side: PositionSide,          // LONG | SHORT
        opened_qty: String,          // 文字列で精度保持（H2、既存 TradeMsg 規約に整合）
        avg_open_price: String,
        ts_event_ms: i64,
    },
    PositionClosed {
        strategy_id: String,
        venue: String,
        instrument_id: String,
        position_id: String,
        realized_pnl: String,        // 文字列、JPY は整数として扱う（立花仕様）
        ts_event_ms: i64,
    },
}
```

**精度保持規約（H2）**: 数量・価格・PnL は **文字列**で運ぶ（既存 `TradeMsg` / `KlineMsg` / `DepthLevel` の `String` 規約に揃える）。`f64` 変換は Rust UI レンダラ層が最後に行う。

**venue フィールド（H1）**: ライブ立花とリプレイ SimulatedExchange が同時に動く可能性があるため、ポジション系イベントには `venue` を必須化する。`Order*` 系は order/ schema 側ですでに `venue_order_id` で振り分け可能。

**clock 注入（H4 / Q3）**: 上記 `AdvanceClock` Command は **N-pre プロトタイプで案 A（外部 clock 駆動）が feasibility 確認できた場合のみ採用**。案 B（nautilus 自走）採用時はこの Command を入れず、`StartEngine.config.range_start_ms / range_end_ms` だけで完結する。N-pre 結果を本セクションに追記すること。

## 4. データフロー（リプレイ）

```
Rust HTTP /api/replay/order
   │ POST {side, qty, price, ...}
   ▼
Rust replay_api.rs
   │ engine_client.send(Command::SubmitOrder { venue: "replay", order })
   ▼
Python nautilus/engine_runner.py
   │ BacktestEngine.submit_order(OrderFactory.market(...))
   ▼
nautilus SimulatedExchange
   │ 仮想時刻の Trade イベントで約定判定
   ▼
Strategy.on_event(OrderFilled)
   │ narrative_hook.record(Outcome)  ──→ HTTP /api/agent/narrative
   ▼
Event::OrderFilled → IPC → Rust → HTTP レスポンス
```

決定論性のため、`BacktestEngine` の `clock` は **Rust から渡す `current_time`** を真として進め、`time.time()` を一切参照しない。

## 5. データフロー（ライブ・立花発注）

```
ユーザー UI 操作（or Python Strategy）
   ▼
Python LiveExecutionEngine.submit_order
   ▼
TachibanaExecutionClient
   │ 既存 tachibana セッション・URL ルーティングを再利用
   │ POST CLMKabuNewOrder
   ▼
EVENT WebSocket (p_evt_cmd=EC)
   ▼
parse → nautilus OrderFilled イベント
   ▼
Strategy.on_event → narrative_hook
   ▼
Event::OrderFilled → IPC → Rust → UI 反映
```

## 6. 既存計画との衝突点と整理

| 衝突点 | 解消方針 |
|---|---|
| Phase 2「自作 Virtual Exchange Engine」 | **破棄**。nautilus `BacktestEngine` で代替。`docs/plan/README.md` Phase 2 セクションは `Phase 2 = nautilus 統合` に書き換え（N1 完了時点で） |
| 立花 Phase 2 発注経路 | **書き直し**。`tachibana/` 計画の Phase 2 タスクは `tachibana_nautilus.py` 実装タスクに置換（spec.md §2.3 と整合） |
| Rust 側発注 adapter（暗号資産） | **段階廃止**。Phase N3 で nautilus 側に移植後、Rust の発注経路は削除。データ取得（subscribe）は維持 |
| ナラティブの `outcome` 自動連携 | **そのまま**。書き込み元が `FillEvent` から nautilus `OrderFilled` に変わるが、HTTP API 契約は不変 |

## 7. Python 単独モードへの含み

Rust（iced）を外す将来モードでは、以下のレイヤーだけで動く:

```
Python: nautilus_trader + 既存 venue worker + narrative store + (任意) FastAPI で HTTP API を露出
```

そのため:
- nautilus 関連コードは **`engine-client` IPC を介さず直接 Python から叩ける**よう、`engine_runner.py` に CLI / library 二系統のエントリを切る
- 立花 Phase 1 の tkinter ログイン UI は **subprocess 隔離経由**でのみ再利用する（[tachibana/architecture.md §7.4](../tachibana/architecture.md) の subprocess 隔離方針と整合）。engine 本体プロセス（nautilus が稼働するプロセス）から `import tkinter` しない。tkinter は常に `python -m engine.exchanges.tachibana_login_dialog` の独立プロセスで起動する
