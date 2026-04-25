# nautilus_trader 統合計画（案 A：nautilus を中核エンジンに据える）

## 何をするか

[NautilusTrader](https://github.com/nautechsystems/nautilus_trader) を本アプリの中核に据え、リプレイ（バックテスト）と発注（ライブ）の両方を nautilus のエンジンに任せる。立花証券は **nautilus の `ExecutionClient` / `DataClient` 実装**として組み込み、暗号資産 venue の既存実装はそのままチャート閲覧用に残す。

## なぜ案 A か

- **リプレイ・発注エンジンを自作しない**: nautilus は `BacktestEngine`（決定論的イベントドリブン）と `LiveExecutionEngine`（実取引）の両方を持つ。Phase 2 の Virtual Exchange Engine 構想（[docs/plan/README.md](../README.md)）と機能要件が完全に重なる
- **将来の Python 単独モード方針と整合**: nautilus は Python ファースト・Rust コアのライブラリで、ユーザー戦略は Python で書く。Rust（iced）を外しても戦略・発注ループはそのまま動く（メモリ `project_python_only_mode.md`）
- **ASI / ナラティブ層との配線がシンプル**: `Strategy` クラスの `on_event` フックでナラティブ生成 → 既存 `/api/agent/narrative` に POST するだけで Phase 4a の蓄積に流せる

## 文書構成

- [spec.md](./spec.md) — ゴール・スコープ・Phase 切り
- [architecture.md](./architecture.md) — プロセス境界・データフロー・配置原則
- [implementation-plan.md](./implementation-plan.md) — タスク分解（N-pre / N0〜）
- [data-mapping.md](./data-mapping.md) — nautilus ↔ 立花 / EventStore の写像表（Bar・Instrument・Account・Position・OrderType）
- [open-questions.md](./open-questions.md) — 未解決事項（N-pre ブロッカー含む）

## 既存計画との関係

| 既存計画 | この計画との関係 |
|---|---|
| `docs/plan/✅python-data-engine/` | Python サイドの IPC・ワーカー基盤を再利用。nautilus エンジンは新ワーカーとして同居 |
| `docs/plan/tachibana/` | Phase 1（閲覧）はそのまま |
| `docs/plan/order/`（立花注文機能） | **N2 で `LiveExecutionClient` に置き換え対象**。`tachibana_orders.py` は再利用、HTTP API `/api/order/*` は nautilus 経由に差し替え。詳細は本計画 [spec.md §2.3](./spec.md#23-phase-n2--立花-executionclient) |
| `docs/plan/order/` REPLAY モード仮想注文 | **N1 で実装**: 既存 wiki ([docs/wiki/orders.md](../../wiki/orders.md#replay-モード中の動作)) に書かれた REPLAY モード仮想注文 UX は本計画 N1 で `BacktestEngine` と立花注文 HTTP API の REPLAY 切替で実現する。下記 §「REPLAY モード仮想注文の取り込み」参照 |
| `docs/plan/README.md` Phase 2 仮想売買エンジン | **nautilus の `BacktestEngine` で代替**。自作 Virtual Exchange Engine は破棄 |
| `docs/plan/README.md` Phase 4a ナラティブ | nautilus `Strategy` フックから既存 HTTP API に書き込む配線のみ追加 |

## REPLAY モード仮想注文の取り込み

`docs/plan/order/` で **スコープ外**とした REPLAY モード仮想注文（[wiki UX](../../wiki/orders.md#replay-モード中の動作)）は本計画の **Phase N1** に集約する:

- `POST /api/order/submit` 等を REPLAY モード時は **nautilus `BacktestEngine` の `SimulatedExchange` に流す**（既存 `OrderSessionState` の前段で分岐）
- iced 側 UI は live / replay の判定で見た目を切替（バナー「⏪ REPLAYモード中 — 注文は無効です」、ボタンラベル「仮想注文確認」）
- Python 側は同じ `tachibana_orders.NautilusOrderEnvelope` を使い、live なら `LiveExecutionClient`、replay なら `BacktestExecutionEngine` にディスパッチ → **API 契約・envelope 型は live と replay で完全共有**
- 約定通知も同じ IPC `Event::OrderFilled` を使い、UI は live / replay を区別しない（バナーのみで判断）
- 第二暗証番号入力 modal は REPLAY モードでは出さない（[order/architecture.md §5](../order/architecture.md#5-第二暗証番号の取扱い) の取得タイミングを REPLAY ガードで skip）

この設計により order/ 計画で書く Python 関数・iced UI は **N1 段階で 1 行も書き換えずに REPLAY 対応**できる（nautilus 互換不変条件 [order/spec.md §6](../order/spec.md#6-nautilus_trader-互換要件不変条件) の副次効果）。

## 長期方針

- nautilus を入れることで「Rust（iced）を将来外しても、Python だけで戦略 + 発注 + バックテストが回る」状態を確保する
- Rust 側の `exchange/` クレート（暗号資産 adapter）は **データ取得・チャート描画用途**に役割を絞り、発注経路は nautilus に一本化する（暗号資産 venue も含めて）
