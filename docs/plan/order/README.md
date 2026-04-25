# 立花証券 注文機能 統合計画

## 何をするか

[.claude/skills/tachibana/SKILL.md](../../../.claude/skills/tachibana/SKILL.md) に定義された **CLMKabuNewOrder / CLMKabuCorrectOrder / CLMKabuCancelOrder / CLMKabuCancelOrderAll** 系 API を本アプリに組み込み、立花 venue で「新規注文・訂正・取消・注文一覧・約定通知（EVENT EC）」までを完結させる。

立花 Phase 1（[docs/plan/tachibana/](../tachibana/)）の閲覧基盤の上に **発注経路だけを別トラック**として立てる計画。Phase 1 完了後に着手するか、認証・session 部分が安定したタイミングで並行着手する。

**本計画は live モードのみを対象**とする。REPLAY モード仮想注文 UX は本計画スコープ外で、[nautilus_trader 統合 Phase N1](../nautilus_trader/README.md) で扱う。REPLAY モード時の `/api/order/*` は 503 + `reason_code="REPLAY_MODE_ACTIVE"` を返す（spec.md §3.2）。

## なぜ別 plan ディレクトリか

- 立花 Phase 1 は **閲覧専用**（spec.md §2.2 で発注は明示的に Phase 2+ 送り）
- 注文は **第二暗証番号の収集・保持・約定通知購読・注文台帳・冪等性**など Phase 1 と独立した懸念領域が多く、`tachibana/` plan に混ぜると章立てが破綻する
- 将来 nautilus_trader 統合（[docs/plan/nautilus_trader/](../nautilus_trader/) 案 A）に切り替える際、本計画は **「nautilus 統合前の自前注文経路」**として置き換え対象になる。境界を明確にするため別ディレクトリで管理する

## 類似プロジェクト参照

`C:\Users\sasai\Documents\flowsurface`（**ローカル clone 前提**）の立花 adapter（**Rust 実装**）に既に発注 API の型定義・送信関数・テストパターンが揃っている。本計画はこれを **Python に移植**する形で進める。

> **実在確認済み**: 下表の参照先（`exchange/src/adapter/tachibana.rs` / `src/api/agent_session_state.rs`）は本計画着手時点で実在。Phase 1 README ([docs/plan/tachibana/README.md](../tachibana/README.md) 「重要」節) と異なり、本計画の参照は **架空ではない**。flowsurface clone を前提に作業する。

| flowsurface の参照点 | 本計画での扱い |
|---|---|
| [`exchange/src/adapter/tachibana.rs:1307-1459`](../../../../flowsurface/exchange/src/adapter/tachibana.rs) `NewOrderRequest`/`CorrectOrderRequest`/`CancelOrderRequest`/`ModifyOrderResponse` | **フィールド構成・rename 名・`Debug` マスク方針をそのまま踏襲**。実装言語は Python（pydantic + `__repr__` マスク）に変える |
| 同 `submit_new_order` / `submit_cancel_order` 関数 | `python/engine/exchanges/tachibana_orders.py` の `submit_order` / `modify_order` / `cancel_order` / `cancel_all_orders` として移植（**nautilus `LiveExecutionClient` 抽象メソッド名で統一**。立花 "correct" 用語は `_compose_request_payload` 内部に閉じる） |
| 同 `serialize_order_request()`（`p_no` / `p_sd_date` / `sCLMID` / `sJsonOfmt` / 逆指値デフォルトを後付け） | Python 側 `tachibana_orders._compose_request_payload()` として再現。`tachibana_helpers.PNoCounter.next()`（呼出側で `PNoCounter` インスタンスを保持）/ `tachibana_helpers.current_p_sd_date()` を使う |
| `OrderListRequest` / `OrderListResponse` / `OrderRecord` | 注文台帳取得経路としてそのまま移植 |
| [`src/api/agent_session_state.rs`](../../../../flowsurface/src/api/agent_session_state.rs) `PlaceOrderOutcome` (`Created` / `IdempotentReplay` / `Conflict`) | **冪等性マップの正本パターン**。`client_order_id → (order_id, request_key)` を Rust 側 HTTP API レイヤで保持する設計を踏襲（API は Rust 側に出すため、ここは Rust に書く） |
| `submit_new_order` のテスト群（wrong_password / market_closed / invalid_issue_code） | pytest-httpx で **同名・同シナリオ**のテストを Python 側に複製。立花エラーコード辞書として共有 |

flowsurface 側は「Rust 集約・直結 venue」、本計画は「Python 集約・IPC backend」という配置原則の違いはあるが、**ワイヤフォーマット・パラメータ規約・Debug マスク方針**は 1:1 で写せる。

## 文書構成

- [spec.md](./spec.md) — ゴール・スコープ（Phase O0〜O3）・公開 API・非機能要件
- [architecture.md](./architecture.md) — Python 集約・IPC 拡張・冪等性・第二暗証番号の取り扱い
- [implementation-plan.md](./implementation-plan.md) — タスク分解
- [open-questions.md](./open-questions.md) — 未解決事項

## 既存計画との関係

| 計画 | 関係 |
|---|---|
| [docs/plan/tachibana/](../tachibana/) | **依存**: 認証・session・URL ビルダ・codec を全面再利用。F-H5（第二暗証番号 Phase 1 では収集しない）を本計画 Phase O0 で **解禁**する |
| [docs/plan/✅python-data-engine/](../✅python-data-engine/) | IPC スキーマ 1.2 → 1.3 へ bump。`Command::SubmitOrder` / `ModifyOrder` / `CancelOrder` および `Event::OrderAccepted` / `OrderFilled` / `OrderCanceled` / `OrderRejected` を追加（nautilus 用語に統一。立花 "correct" は IPC 層に出さない） |
| [docs/plan/nautilus_trader/](../nautilus_trader/) | **将来の置換対象 + REPLAY 仮想注文の引き取り先**: ① 本計画完了後、nautilus 統合 Phase N2 で `tachibana_nautilus.py` の `LiveExecutionClient` 実装に **本計画の `tachibana_orders.py` をそのまま再利用**する。② **REPLAY モード仮想注文 UX**（[wiki](../../wiki/orders.md#replay-モード中の動作)）は本計画のスコープ外で、nautilus N1 の `BacktestEngine` + 本計画 HTTP API の live/replay ディスパッチで実現する |
| [docs/plan/README.md](../README.md) Phase 2 仮想売買エンジン | 直接の依存はない（あちらはリプレイ専用） |

## 長期方針

- **本計画は立花証券単独**: 暗号資産 venue（Binance / Bybit / Hyperliquid 等）への発注経路は本計画に含めない。それらは [nautilus_trader 計画 Phase N3](../nautilus_trader/spec.md#24-phase-n3--暗号資産-venue-executionclient任意) で扱う
- Rust 側に発注ロジックを書かない（Python 単独モード方針との整合）
- 約定通知は EVENT WebSocket の `EC` フレームを **Python 側でパース → IPC イベント化**して Rust UI に伝達。Rust 側に立花フォーマットのパーサを書かない
- **公開 API・IPC・Python 関数シグネチャはすべて nautilus_trader のオーダーモデルに型を合わせる**。将来 nautilus_trader 統合（[docs/plan/nautilus_trader/](../nautilus_trader/) 案 A）で `LiveExecutionClient` に切り替える際、**`tachibana_orders.py` は手を入れずに nautilus から呼び出せる**ことを設計の不変条件とする。詳細は [spec.md §6](./spec.md#6-nautilus_trader-互換要件不変条件) と [architecture.md §10](./architecture.md#10-nautilus_trader-との型マッピング)
- HTTP API は nautilus の `OrderFactory` 入力と用語・field 名を揃える（`order_type` / `time_in_force` / `client_order_id` / `venue_order_id`）。立花固有の `sBaibaiKubun` / `sCondition` 等への写像は **Python 側 1 箇所**（`tachibana_orders._compose_request_payload`）に閉じる
