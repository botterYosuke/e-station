# nautilus_trader 統合計画（案 A：nautilus を中核エンジンに据える）

## 何をするか

[NautilusTrader](https://github.com/nautechsystems/nautilus_trader) を本アプリの中核に据え、**live モード**（立花証券で実弾発注）と **replay モード**（J-Quants 過去データでバックテスト）の両方を nautilus のエンジンに任せる。

ユーザーが書く `Strategy` は **TradeTick（歩み値）一本のインタフェース**で受け、live / replay のどちらでも **同じコードがそのまま動く**ことを設計上の不変条件とする。

## なぜ案 A か

- **リプレイ・発注エンジンを自作しない**: nautilus は `BacktestEngine`（決定論的イベントドリブン）と `LiveExecutionEngine`（実取引）の両方を持つ
- **将来の Python 単独モード方針と整合**: nautilus は Python ファースト・Rust コアのライブラリ。Rust（iced）を外しても戦略・発注ループはそのまま動く（メモリ `project_python_only_mode.md`）
- **ASI / ナラティブ層との配線がシンプル**: `Strategy` クラスの `on_event` / `on_trade_tick` フックでナラティブ生成 → 既存 `/api/agent/narrative` に POST するだけ

## 2 つのモードと制約

| モード | データソース | 板情報 | 歩み値 | Bar |
|---|---|---|---|---|
| **live** | 立花 EVENT WebSocket（FD frame） | ○（FD frame 上位 10 段） | ○（FD frame から `FdFrameProcessor` で合成） | ○（履歴 API + tick 集約） |
| **replay** | J-Quants CSV（`S:\j-quants\`） | **× 過去板データなし** | ○（`equities_trades_*.csv.gz`） | ○（`equities_bars_daily/minute_*.csv.gz`） |

> **D1（不変条件・確定 2026-04-28）**: **板（OrderBook / QuoteTick）は live 専用**。
> J-Quants には板履歴がないため、replay モードでは OrderBook / QuoteTick を提供しない。
> これは計画レベルの不変条件であり、後から「分足から板を合成」方向への drift を防ぐ。
> 詳細は [archive/replay-ui-role-revision-2026-04-28.md §D1](./archive/replay-ui-role-revision-2026-04-28.md) を参照。

> **D8（モード切替セマンティクス・確定 2026-04-28）**: モードはアプリ起動時に
> `--mode {live|replay}` で 1 回だけ決定し、プロセス寿命中は変更しない。ランタイムの
> `live ⇄ replay` 切替コマンド（HTTP / IPC）は提供せず、モード変更は `StopEngine` →
> プロセス再起動でのみ行う。in-memory state（chart 履歴・order list・overlay・
> ClientOrderId → OrderState map など）はモード境界を跨いで保持しない（`saved-state.json`
> による UI レイアウト復元のみ引き継ぐ）。N1 で `--mode live` を起動しても nautilus
> `LiveExecutionEngine` は N2 まで起動しない（Hello capabilities `nautilus.live=false`
> を維持）。詳細は [archive/replay-ui-role-revision-2026-04-28.md §D8](./archive/replay-ui-role-revision-2026-04-28.md) を参照。

### 重要な制約（confirmed 2026-04-28）

1. **板に依存する戦略は live 専用**。replay では再現できない。CI で「`Strategy` が `on_order_book_*` を実装している場合 replay モードを禁止」する lint を入れる（[spec.md §3.5](./spec.md#35-livereplay-互換不変条件)）
2. **live と replay の TradeTick は粒度差がある**:
   - live（立花）: `aggressor_side` は前 trade との価格比較で推定（[tachibana_ws.py:183](../../../python/engine/exchanges/tachibana_ws.py#L183) で ambiguous 警告）
   - replay（J-Quants）: `aggressor_side` 情報なし（`SessionDistinction` のみ）
   - → `aggressor_side` に依存する戦略は live / replay で挙動がズレる旨を **戦略開発者向けドキュメントに明示**
3. **InstrumentId 写像**: J-Quants の `Code` 5 桁（例 `13010`）は末尾 0 を切って 4 桁にし、`{4 桁}.TSE` 形式（`1301.TSE`）に揃える（[data-mapping.md §1.1](./data-mapping.md#11-instrumentid-写像)）

## 文書構成

- [spec.md](./spec.md) — ゴール・スコープ・Phase 切り
- [architecture.md](./architecture.md) — プロセス境界・データフロー・配置原則
- [implementation-plan.md](./implementation-plan.md) — タスク分解（N-pre / N0〜）
- [data-mapping.md](./data-mapping.md) — nautilus ↔ 立花 / J-Quants の写像表（TradeTick・Bar・Instrument・Account・Position・OrderType）
- [open-questions.md](./open-questions.md) — 未解決事項

## 既存計画との関係

| 既存計画 | この計画との関係 |
|---|---|
| `docs/✅python-data-engine/` | Python サイドの IPC・ワーカー基盤を再利用。nautilus エンジンは新ワーカーとして同居 |
| `docs/✅tachibana/` | Phase 1（閲覧）はそのまま。Phase 2（発注）は本計画 N2 に置換 |
| `docs/✅order/`（立花注文機能） | **N2 で `LiveExecutionClient` に置き換え対象**。`tachibana_orders.py` は再利用 |
| `docs/✅order/` REPLAY モード仮想注文 | **N1 で実装**: `BacktestEngine` の `SimulatedExchange` に流す。下記 §「REPLAY モード仮想注文の取り込み」参照 |
| `docs/plan/README.md` Phase 2 仮想売買エンジン | **nautilus の `BacktestEngine` で代替**。自作 Virtual Exchange Engine は破棄 |
| `docs/plan/README.md` Phase 4a ナラティブ | nautilus `Strategy` フックから既存 HTTP API に書き込む配線のみ追加 |

## ユーザー定義 Strategy（N4 — 実装済み 2026-04-29）

ユーザーが書いた `.py` ファイルを REPLAY エンジンに流す基盤を実装済み。

### API フロー（2 ステップ）

```
POST /api/replay/load   → データ件数確認のみ（strategy_file は不要）
POST /api/replay/start  → strategy_file + strategy_init_kwargs を指定してバックテスト開始
```

`strategy_file` は **`/api/replay/start`** にのみ渡す。`/api/replay/load` は受け付けない。

### 実装ポイント

| 箇所 | 内容 |
|---|---|
| `ReplayStartBody` (`src/replay_api.rs`) | `strategy_file`, `strategy_init_kwargs` フィールドを追加。未知フィールドは `deny_unknown_fields` で HTTP 400 |
| `EngineStartConfig` (`engine-client/src/dto.rs`) | 同フィールドを `Option` で保持。`None` は wire 上省略（`skip_serializing_if`） |
| `EngineStartConfig` (`python/engine/schemas.py`) | Pydantic モデルで `extra="forbid"` 適用済み。`model_validate()` で検証して `invalid_config` エラーを返す |
| `strategy_init_kwargs` の型 | `serde_json::Map<String, Value>` — object 以外（配列・スカラー）は HTTP 境界で即拒否 |
| `_handle_start_engine` (`python/engine/server.py`) | `EngineStartConfig.model_validate()` → `strategy_loader.load_strategy_from_file()` の順で処理 |

### サンプル

```bash
# 1. データだけ読み込む
curl -X POST http://127.0.0.1:9876/api/replay/load \
  -d '{"instrument_id":"7203.TSE","start_date":"2024-01-01","end_date":"2024-12-31","granularity":"Daily"}'

# 2. 戦略を指定してバックテスト開始
curl -X POST http://127.0.0.1:9876/api/replay/start \
  -d '{"instrument_id":"7203.TSE","start_date":"2024-01-01","end_date":"2024-12-31","granularity":"Daily","strategy_id":"user-defined","initial_cash":"1000000","strategy_file":"docs/example/buy_and_hold.py","strategy_init_kwargs":{"instrument_id":"7203.TSE","lot_size":100}}'
```

詳細は [docs/wiki/backtest.md](../../wiki/backtest.md) を参照。

---

## REPLAY モード仮想注文の取り込み

`docs/✅order/` で **スコープ外**とした REPLAY モード仮想注文（[wiki UX](../../wiki/orders.md#replay-モード中の動作)）は本計画の **Phase N1** に集約する:

- `POST /api/order/submit` 等を REPLAY モード時は **nautilus `BacktestEngine` の `SimulatedExchange` に流す**
- 発注入力 UI は **Python tkinter 側**で live / replay の判定に応じて文言を切替
  （例: バナー「⏪ REPLAYモード中 — 実注文は送信されません」、確認文言「仮想注文確認」）。
  iced は監視・表示のみを担い、注文入力責務は持たない
- Python 側は同じ `tachibana_orders.NautilusOrderEnvelope` を使い、live なら `LiveExecutionClient`、replay なら `BacktestExecutionEngine` にディスパッチ → **API 契約・envelope 型は live / replay で完全共有**
- 約定通知イベント型は live と同じ IPC `Event::Order*` を再利用するが、UI ストアは
  `venue="replay"` で view を分離し、REPLAY 注文一覧・REPLAY 買付余力にのみ反映する
- 第二暗証番号入力 modal は REPLAY モードでは出さない（[order/architecture.md §5](../✅order/architecture.md#5-第二暗証番号の取扱い) の取得タイミングを REPLAY ガードで skip）

## 長期方針

- nautilus を入れることで「Rust（iced）を将来外しても、Python だけで戦略 + 発注 + バックテストが回る」状態を確保する
- Rust 側の `exchange/` クレート（暗号資産 adapter）は **データ取得・チャート描画用途**に役割を絞り、発注経路は nautilus に一本化する
- **Strategy は TradeTick 一本で書く**規約により、replay で書いた戦略を live にデプロイできる（[spec.md §3.5](./spec.md#35-livereplay-互換不変条件)）

## Rust UI の役割境界（2026-04-28 確定）

Rust UI（iced）は matplotlib 代替ではなく、**「再生中の市場の様子」と「戦略の挙動」の
目視**に役割を絞る。詳細は [archive/replay-ui-role-revision-2026-04-28.md](./archive/replay-ui-role-revision-2026-04-28.md) を参照。

- **D2（採用機能）**: replay 中に Rust UI が担うのは以下 3 点のみ:
  1. 時系列再生中のローソク足・歩み値表示（既存 `EngineEvent::Trades` /
     `EngineEvent::KlineUpdate` を再利用、新規 market data IPC は足さない）
  2. 戦略マーカーのオーバーレイ（`ExecutionMarker`＝OrderFilled 由来の自動レイヤー /
     `StrategySignal`＝Strategy が `emit_signal()` で明示送出する 2 系統に分離）
  3. 再生速度コントロール（1x / 10x / 100x。streaming=True 経路で wall-clock pacing。
     pause / seek は N1 では含めず [Q14](./open-questions.md#q14) で再評価）
- **D3（不採用領域）**: PnL 曲線・パラメータヒートマップ・最適化結果の可視化は iced で
  実装しない。これらは AI ナラティブ層（`POST /api/agent/narrative`）に保存し、必要なら
  marimo / Jupyter から narrative store を読んで分析する運用とする
- **D9（REPLAY 銘柄追加時の自動配線）**: REPLAY 対象に銘柄を追加（`POST /api/replay/load`
  成功）すると、Rust UI は当該銘柄の **Tick pane と Candlestick(1m) pane を自動生成**
  する。同 identity（`mode=replay, instrument_id, pane_kind, granularity?`）の pane が
  既存ならば再利用し重複生成しない。同時表示銘柄数の hard limit は
  `MAX_REPLAY_INSTRUMENTS = 4`（超過時は `/api/replay/load` を 400 で拒否）。あわせて
  REPLAY 専用の **注文一覧 pane**（`venue="replay"` フィルタ・`⏪ REPLAY` バナー付き・
  `tachibana_orders_replay.jsonl` と整合）と **買付余力表示**（新規 IPC
  `EngineEvent::ReplayBuyingPower`、現物のみ・`cash` ベース）を自動生成し、live と分離
  表示する。**REPLAY 中に立花 `CLMZanKaiKanougaku` を一切参照しない**ことを
  `order_router.py` のコードガードで強制し、実残高で発注可能と誤解する事故を防ぐ
