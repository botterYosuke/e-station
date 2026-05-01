# review-fixes — auto-refresh-on-order-accepted.md

対象: `docs/✅order/auto-refresh-on-order-accepted.md`
開始日: 2026-04-30

---

## ラウンド 1（2026-04-30）

### 統一決定

1. `notify_order_accepted` は void のまま。変更後イメージから `let notify = ...` を除去し `Task::batch([refresh_orders, refresh_buying_power])` を返す
2. `VenueReady` ハンドラは既存の `.chain()` 構造に従い `replay.chain(auto_fetch_buying_power).chain(auto_fetch_orders)` に修正
3. `GetBuyingPower` ガード: `OrderAccepted` パスでも `buying_power_request_id.is_none()` ガードを通す旨を実装計画に明示
4. `GetOrderList` 重複防止: `request_id` フィールド不要（idempotent）として計画書に注記
5. `OrderAccepted` パスのペインガード: ペイン有無によらず IPC 送信する（後付けペインにも即反映）と明示
6. silent failure: `engine_connection` が None の場合は `Task::none()` フォールバック（Constraint 4 の例外として許容）

### Finding 一覧

| Finding ID | 重要度 | 観点 | 対象行 | 修正概要 |
|-----------|--------|------|--------|---------|
| H1 | HIGH | B | 141-149 | `notify_order_accepted` void 呼び出しに修正、`Task::batch([refresh_orders, refresh_buying_power])` に変更 |
| H2 | HIGH | B | 100-122 | `Task::batch` → `replay.chain(auto_fetch_buying_power).chain(auto_fetch_orders)` に修正 |
| H3 | HIGH | B/C | 変更2イメージ | `buying_power_request_id.is_none()` ガード注記を追加 |
| H4 | HIGH | C | 変更2イメージ | `engine_connection=None` 時 `Task::none()` フォールバック注記を追加 |
| H5 | HIGH | D | 174-181 | 目視確認項目（`[ipc] → GetOrderList` ログ確認）を追記 |
| H6 | HIGH | D | AC | `OrderRejected` / replay モード negative test を AC に追加 |
| M1 | MEDIUM | A/B/C | 変更2イメージ | `GetOrderList` idempotent 注記を追加 |
| M2 | MEDIUM | A | AC:66 | `is_ready=true` 条件を AC に補記 |
| M3 | MEDIUM | C | 変更1イメージ | 再ログイン時 `VenueReady` 再発行の注記を追加 |
| M4 | MEDIUM | C | 変更2イメージ | `has_order_list_pane()` ガード設けない旨を明示 |
| M5 | MEDIUM | D | AC末尾 | `/bug-postmortem` を AC 項目から注記へ変更 |
| M6 | MEDIUM | D | テスト方針 | Rust ユニットテスト候補関数名を追記 |

## ラウンド 2（2026-04-30）

HIGH/MEDIUM: 0件 → **収束**

### 残存 LOW（対応不要）

| # | 観点 | 内容 |
|---|------|------|
| L1 | A | 変更2の疑似コードで `venue` パラメータを受け取るが本文中で未使用。実装時に不要なら `..` のみに変更 |
| L2 | D | テスト方針の `[ipc] → GetOrderList` ログフォーマットは仮定値。実装後に実ログと照合して修正 |
| L3 | C | session 未確立時に `OrderListUpdated{orders:[]}` で空リスト上書きが起きうるが、VenueReady 時点では session 確立済みのため通常パスでは発生しない |
| L4 | A/B | 既存インフラ表の行番号（`main.rs:2003-2034` 等）は将来陳腐化リスクあり。シンボル名参照への切替を推奨（実装時でも可） |
| L5 | B | replay モード除外の根拠（`distribute_order_list` の replay ペイン除外ロジック）が実装計画セクションに転記されていない（AC には記載済み） |

## ラウンド 3（2026-04-30）— ユーザー追加指摘による追加修正

### 追加 Findings（ユーザー指摘）

| Finding ID | 重要度 | 内容 |
|-----------|--------|------|
| U-H1 | HIGH | `EngineEvent::OrderAccepted` に venue フィールドなし（dto.rs:843, main.rs:1073 で `..` 破棄）。replay バックテストも `OrderAccepted` を emit するため、ハンドラで live/replay を判別できないまま IPC 送信すると replay 中に live 立花データを取りに行く |
| U-M1 | MEDIUM | BuyingPower の catch-up 経路（main.rs:2656）は OrderList に対して未計画。ログイン後に OrderList ペインを開いた場合は依然手動更新が必要 |
| U-M2 | MEDIUM | engine_connection=None 時の `Task::none()` を Constraint 4 例外として許容していたが、GetBuyingPower 手動ボタンは toast を出す（main.rs:1995）との非対称。engine 経由イベント受信時は engine_connection=None に到達しない（dead code）として整理 |

### 修正内容

- **Constraint 1**: live/replay 判別方法（`tachibana_state.is_ready()` または `AppMode::Live`）と「Python の replay も OrderAccepted を emit する」警告を明記
- **Constraint 4**: engine_connection=None は dead code として扱う旨を明確化（Constraint 4 例外ではない）
- **変更ファイル記述**: catch-up 経路が今回スコープ外であることを明記（main.rs:2656 の BuyingPower 相当を OrderList には追加しない）
- **変更 2 変更後イメージ**: `tachibana_state.is_ready()` ガードを疑似コードに追加、live 判別の必要性を注記
- **AC line 72**: 旧記述「distribute_buying_power の replay ペイン除外」→「tachibana_state.is_ready() ガードで早期 return」に修正
