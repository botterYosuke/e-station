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
| `docs/plan/✅python-data-engine/` | Python サイドの IPC・ワーカー基盤を再利用。nautilus エンジンは新ワーカーとして同居 |
| `docs/plan/✅tachibana/` | Phase 1（閲覧）はそのまま。Phase 2（発注）は本計画 N2 に置換 |
| `docs/plan/✅order/`（立花注文機能） | **N2 で `LiveExecutionClient` に置き換え対象**。`tachibana_orders.py` は再利用 |
| `docs/plan/✅order/` REPLAY モード仮想注文 | **N1 で実装**: `BacktestEngine` の `SimulatedExchange` に流す。下記 §「REPLAY モード仮想注文の取り込み」参照 |
| `docs/plan/README.md` Phase 2 仮想売買エンジン | **nautilus の `BacktestEngine` で代替**。自作 Virtual Exchange Engine は破棄 |
| `docs/plan/README.md` Phase 4a ナラティブ | nautilus `Strategy` フックから既存 HTTP API に書き込む配線のみ追加 |

## REPLAY モード仮想注文の取り込み

`docs/plan/✅order/` で **スコープ外**とした REPLAY モード仮想注文（[wiki UX](../../wiki/orders.md#replay-モード中の動作)）は本計画の **Phase N1** に集約する:

- `POST /api/order/submit` 等を REPLAY モード時は **nautilus `BacktestEngine` の `SimulatedExchange` に流す**
- iced 側 UI は live / replay の判定で見た目を切替（バナー「⏪ REPLAYモード中 — 注文は無効です」、ボタンラベル「仮想注文確認」）
- Python 側は同じ `tachibana_orders.NautilusOrderEnvelope` を使い、live なら `LiveExecutionClient`、replay なら `BacktestExecutionEngine` にディスパッチ → **API 契約・envelope 型は live / replay で完全共有**
- 約定通知も同じ IPC `Event::OrderFilled` を使い、UI は live / replay を区別しない（バナーのみで判断）
- 第二暗証番号入力 modal は REPLAY モードでは出さない（[order/architecture.md §5](../✅order/architecture.md#5-第二暗証番号の取扱い) の取得タイミングを REPLAY ガードで skip）

## 長期方針

- nautilus を入れることで「Rust（iced）を将来外しても、Python だけで戦略 + 発注 + バックテストが回る」状態を確保する
- Rust 側の `exchange/` クレート（暗号資産 adapter）は **データ取得・チャート描画用途**に役割を絞り、発注経路は nautilus に一本化する
- **Strategy は TradeTick 一本で書く**規約により、replay で書いた戦略を live にデプロイできる（[spec.md §3.5](./spec.md#35-livereplay-互換不変条件)）
