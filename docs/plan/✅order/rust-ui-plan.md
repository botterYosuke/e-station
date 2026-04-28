# 立花注文機能: Rust UI 実装計画

**位置づけ**: [docs/wiki/orders.md](../../wiki/orders.md) に示す注文パネル群（Order Entry / Order List / Buying Power）を iced GUI に追加する。  
Python 側発注経路（[implementation-plan.md](./implementation-plan.md)）の各フェーズと**並行して先行実施**することで、Python 側の各フェーズ完了直後に UI を使った手動テストが行える。

---

## フェーズ対応表

```
Python 側                Rust UI 側（本計画）
──────────               ────────────────────────────────────────────
O-pre（IPC 型定義）  ←→  U-pre（パネルシェル・サイドバー構造）★先行実施可
         │                       │  ← Tpre.2 完了で IPC 配線が通る
O0（発注実装）       ←→  U0（Order Entry IPC 配線 + modal）
O1（訂正・取消）     ←→  U1（Order List パネル + 訂正取消 UI）
O2（EC 約定通知）    ←→  U2（Toast + リアルタイム更新）
O3（信用・余力）     ←→  U3（Buying Power + フォーム拡張）
```

**U-pre は Python 側に依存しない**。O-pre と並行して着手でき、Tpre.2 が完了した時点で U0 の IPC 配線を行えばすぐに Python O0 実装の手動テストに使える。

---

## 前提条件

- **U-pre**: 依存なし。O-pre 着手と同時に開始できる
- **U0 以降**: `engine-client/src/dto.rs` への IPC DTO 追加（Python 側 Tpre.2）が完了していること

---

## アーキテクチャ概要

### 通信方式

Rust UI ↔ Python エンジン間は **既存の IPC WebSocket** を使用する。UI が注文ボタンを押すと `Command::SubmitOrder` を IPC で Python に送り、結果は `Event::OrderAccepted` / `Event::OrderRejected` として返ってくる。HTTP 経路は使わない。

```
iced UI
  │ Message::SubmitOrderClicked
  ▼
main.rs update()
  │ engine_connection.send(Command::SubmitOrder { ... })
  ▼
Python tachibana_orders.py
  │ CLMKabuNewOrder API
  ▼
Event::OrderAccepted / Event::OrderRejected
  ▼
main.rs update() → Message::OrderIpcEvent(...)
  ▼
OrderEntryPanel 状態更新
```

### パネル方式

既存の iced `pane_grid` に `ContentKind::OrderEntry` / `ContentKind::OrderList` / `ContentKind::BuyingPower` を追加する。

既存パターンに従い:
- `data/src/layout/pane.rs` に `ContentKind` variant を追加
- `src/screen/dashboard/pane.rs` の `Content` enum に対応する variant を追加
- `src/screen/dashboard/panel/` 以下に panel struct を新規作成

### 起動方式

サイドバーに 🖊（鉛筆）ボタンを追加し、クリックで注文インラインメニューを開く。メニューから選択すると **フォーカス中のペインが水平分割**されて注文パネルが開く（wiki §「注文パネルを開く」と一致）。

---

## Phase U-pre: パネルシェルとサイドバー構造（O-pre と並行実施）

**ゴール**: IPC に依存しない Rust 構造を先に完成させる。Tpre.2 が完了した瞬間に U0 の IPC 配線だけで動作確認できる状態にする。

### Tupre.1 `ContentKind` 3 variants の追加 ✅（2026-04-27 完了）

- `data/src/layout/pane.rs` に以下を追加:
  ```rust
  ContentKind::OrderEntry,
  ContentKind::OrderList,
  ContentKind::BuyingPower,
  ```
  `ALL` 配列・`Display` impl（`"Order Entry"` / `"Order List"` / `"Buying Power"`）も更新

- `src/screen/dashboard/pane.rs` の `Content` enum に対応 variant を追加し、`view` でスタブ表示（`"実装中..."` テキスト）を返す

受け入れ条件: `ContentKind::OrderEntry` ペインを開くと `"実装中..."` と表示されビルドが通る

### Tupre.2 サイドバー 🖊 ボタンとインラインメニュー ✅（2026-04-27 完了）

`data/src/config/sidebar.rs` の `Menu` enum に `Order` variant を追加。

`src/screen/dashboard/sidebar.rs` の変更:
- サイドバーに 🖊 ボタンを追加。クリックで `Message::ToggleSidebarMenu(Some(Menu::Order))`
- 鉛筆ボタンと検索ボタン（🔍）は相互排他（既存 `ToggleSidebarMenu(None)` パターンに従う）
- インラインメニューの項目（全項目を最初から表示し、未実装フェーズは disabled）:
  ```
  [ Order Entry  ]   ← U0 完了で有効化
  [ Order List   ]   ← U1 完了で有効化
  [ Buying Power ]   ← U3 完了で有効化
  ```
- 項目選択 → `Action::OpenOrderPanel(ContentKind)` を返し、Dashboard 層でフォーカスペインを水平分割して該当パネルを開く

受け入れ条件:
- 🖊 ボタンクリック → メニュー展開（全項目 disabled で表示される）
- 🔍 ボタンクリック中は 🖊 メニューが閉じる（相互排他）
- `cargo build` が通る

### Tupre.3 Order Entry フォームのシェル実装 ✅（2026-04-27 完了 / 2026-04-28 銘柄選択ボタン追加）

新規作成: `src/screen/dashboard/panel/order_entry.rs`

```rust
pub struct OrderEntryPanel {
    instrument_id: Option<String>,  // 注文 API に渡す instrument_id（例: "7203.TSE"）
    display_label: Option<String>,  // タイトルバー表示用のティッカー表示名（例: "TOYOTA"）
    venue: Option<String>,          // set_instrument() が自動設定（例: "tachibana"）
    side: OrderSide,                // BUY のみ有効（Phase O0）
    quantity: String,               // テキスト入力（正の整数）
    price_kind: PriceKind,          // Market / Limit
    price: String,                  // 指値時のみ有効
    submitting: bool,               // U0 IPC 配線後に機能。それまでは false 固定
    last_error: Option<String>,     // reject / timeout 時のエラー表示
    pending_request_id: Option<String>,  // SecondPasswordRequired 待機中
}
```

`Message`:
- `SideChanged(OrderSide)` — Phase O0 では BUY のみ許容
- `QuantityChanged(String)`
- `PriceKindChanged(PriceKind)`
- `PriceChanged(String)`
- `SubmitClicked` → バリデーション → 確認 modal を起動（**U-pre 段階では modal を開くだけで IPC は送らない**）
- `ConfirmSubmit` → `Command::SubmitOrder` を IPC 送信（U0 IPC 配線後に有効）、`submitting = true`
- `Submitted(Result<String, String>)` — `venue_order_id` or `reason_code`

フォーム表示制御（Phase O0）:
- 「売り」ボタンは disabled + tooltip `"Phase O1 で実装予定"`
- 口座種別・期日・逆指値は grayed out + 同様の tooltip

銘柄選択（2026-04-28 実装 ✅ / レビュー収束済み）: タイトルバーに「銘柄未選択」ボタンを追加。クリックで既存の `MiniTickersList` モーダルが開き、ティッカーを選択すると `instrument_id`（`<code>.TSE` 形式）と `display_label` および `venue`（`"tachibana"` 固定）がセットされる。`pane.rs` の `MiniTickersListInteraction` ハンドラで `RowSelection::Switch(ti)` を受け取ったとき、`Content::OrderEntry` の場合は `Exchange::TachibanaStock` ガードを通過したものだけ `panel.set_instrument(id, display)` を呼んでモーダルを閉じる（`SwitchTickersInGroup` には進まない。非対応取引所は `Toast::warn` を表示）。`venue` はハードコードせず `set_instrument()` がフィールドに自動設定し `build_submit_action` が参照する。エンジン切断時（`EngineRestarting(true)`）は `layout_manager.iter_dashboards_mut()` 経由で全レイアウトの `on_engine_disconnected()` を呼び `submitting` をリセット。再接続時（`EngineConnected`）は `on_engine_reconnected()` で `last_error` をクリア。レビュー詳細は [review-fixes-2026-04-28.md §銘柄選択](./review-fixes-2026-04-28.md) を参照。

受け入れテスト: `src/screen/dashboard/panel/order_entry.rs` の `#[cfg(test)]` 内で
- バリデーション（数量 0 → エラー）
- フォーム入力値が `Message` に正しく反映される

### Tupre.4 第二暗証番号 modal のシェル実装 ✅（2026-04-27 完了）

新規作成: `src/modal/second_password.rs`

```rust
pub struct SecondPasswordModal {
    request_id: String,
    input: String,
    visible: bool,     // false = マスク表示
    submitting: bool,
}
```

`Message`:
- `InputChanged(String)`
- `ToggleVisibility`
- `Submit` → `Command::SetSecondPassword { value: Zeroizing(input.clone()) }` を IPC 送信し、pending request を再試行（**U0 IPC 配線後に有効**）
- `Cancel` → `Command::ForgetSecondPassword` を送信し modal を閉じる

UI 要件:
- `text_input` をパスワードモード（`is_secure(true)`）で表示、`Toggle` ボタンで平文切替
- `Enter` キーで `Submit`、`Escape` キーで `Cancel`
- `Submit` 後は `input` を即時 zeroize（`Zeroizing<String>` を使用）
- modal 外クリックで閉じない（意図しない dismissal 防止）

受け入れテスト（U-pre 段階で実施可能）:
- `test_second_password_modal_cancel_does_not_send_value` — Cancel 後に IPC フレームに `value` が載らないことを assert
- `test_second_password_modal_submit_clears_local` — Submit 後に `input` が zeroize されることを assert

### Tupre.5 発注確認 modal のシェル実装 ✅（2026-04-27 完了・ConfirmDialog 再利用、U0 で配線）

既存の `screen::ConfirmDialog<Message>` を **再利用**する。

- 「注文確認」ボタン押下 → `ToggleDialogModal(Some(dialog))` で起動
- dialog body に注文内容（銘柄 / 売買 / 数量 / 価格）を表示
- `[キャンセル]` → `ToggleDialogModal(None)` で閉じる
- `[注文を発注する]` → `Message::ConfirmSubmit` → `Command::SubmitOrder` IPC 送信（U0 IPC 配線後に有効）

既存 `ConfirmDialog` で表現できない場合のみ `src/modal/order_confirm.rs` を新規作成する。

**U-pre 完了時点の状態**: フォームに入力 → 確認 modal が開く → 「注文を発注する」を押しても IPC は送信されない（`todo!()` または noop）。UI の外観・操作フローを早期に確認できる。

---

## Phase U0: Order Entry IPC 配線（Tpre.2 完了後すぐ着手）

**ゴール**: U-pre で作った UI シェルに IPC を配線し、Python O0 実装の手動テストに使える状態にする。

**前提**: Python 側 Tpre.2（`engine-client/src/dto.rs` への `Command::SubmitOrder` 等の追加）が完了していること。

### Tu0.1 IPC 受信ハンドラの追加 ✅（2026-04-27 完了）

- `engine-client/src/dto.rs` に Tpre.2 で追加された Order 系 `Event` を `main.rs` で受信して処理する
  - `Event::SecondPasswordRequired { request_id }` → `Message::SecondPasswordRequired(request_id)` へ変換して modal を起動
  - `Event::OrderAccepted { client_order_id, venue_order_id, ts_ms }` → `OrderEntryPanel` の `submitting` を false に戻し、成功表示
  - `Event::OrderRejected { client_order_id, reason_code, reason_text }` → エラーを `OrderEntryPanel.last_error` にセット
- `main.rs` の `Message` enum に追加:
  ```rust
  SecondPasswordRequired(String),           // request_id
  OrderIpcEvent(engine_client::dto::OrderEvent),
  DismissSecondPasswordModal,
  ```

受け入れテスト: `cargo test -p flowsurface-engine-client --test order_ipc_event_dispatch`

### Tu0.2 `ConfirmSubmit` → `Command::SubmitOrder` IPC 配線 ✅（2026-04-27 完了）

- `OrderEntryPanel::ConfirmSubmit` が `Command::SubmitOrder` を送信するよう実装（U-pre では noop だった箇所）
- `SecondPasswordModal::Submit` が `Command::SetSecondPassword` を送信するよう実装
- サイドバー `[ Order Entry ]` 項目を有効化（disabled 解除）

受け入れ条件: フォーム入力 → 確認 → 「注文を発注する」→ Python エンジンに IPC が届き `Event::OrderAccepted` または `Event::OrderRejected` が返る（**Python O0 手動テスト可能**）

### Tu0.3 `OrderEntryPanel` の状態更新（submitting / error 表示） ✅（2026-04-27 完了）

- `submitting = true` 中はボタン disabled
- `OrderRejected` 受信時は `last_error` にセットしてフォーム内に表示
- `SecondPasswordRequired` 受信時は second_password modal を起動

---

## Phase U1: Order List パネル + 訂正・取消 UI（O1 対応）

**ゴール**: 注文一覧が表示でき、行から訂正・取消が行える。

### Tu1.1 `ContentKind::OrderList` の追加 ✅（2026-04-27 完了）

新規作成: `src/screen/dashboard/panel/order_list.rs`

```rust
pub struct OrderListPanel {
    orders: Vec<OrderRecord>,
    loading: bool,
    expanded_row: Option<String>,  // 展開中の client_order_id
}

pub struct OrderRecord {
    client_order_id: String,
    venue_order_id: Option<String>,
    instrument_id: String,
    side: OrderSide,
    order_qty: u64,
    filled_qty: u64,
    order_price: Option<f64>,
    avg_fill_price: Option<f64>,
    status: OrderStatus,
    ts_ms: i64,
    fills: Vec<FillRecord>,
}
```

`Message`:
- `RefreshClicked` → `Command::GetOrderList` IPC 送信
- `OrderListReceived(Vec<OrderRecord>)` → `orders` を全更新
- `RowClicked(String)` → `expanded_row` をトグル
- `ModifyClicked(String)` → 訂正 modal を起動
- `CancelClicked(String)` → 取消確認 modal を起動

テーブル表示: wiki §「注文照会パネル」の列構成（銘柄 / 売買 / 注文株数 / 約定株数 / 注文単価 / 約定単価 / 状態 / 注文日時）に従う。状態の色分けは `style.rs` に追加する。

受け入れテスト: `GetOrderList` IPC 送信後に `OrderListReceived` を受けて行数が更新されることを確認

### Tu1.2 訂正 modal ✅（2026-04-27 完了 — UI stub のみ、IPC 配線はスキップ）

新規作成: `src/modal/order_modify.rs`

```rust
pub struct OrderModifyModal {
    client_order_id: String,
    venue_order_id: String,
    new_price: String,
    new_quantity: String,
    submitting: bool,
}
```

`Message`:
- `PriceChanged(String)` / `QuantityChanged(String)`
- `Submit` → 第二暗証番号未保持時は `SecondPasswordRequired` フローに合流 → `Command::ModifyOrder` IPC 送信
- `Cancel` → modal を閉じる

### Tu1.3 取消確認 modal ✅（2026-04-28 完了）

既存 `ConfirmDialog` を再利用:
- body: `"注文番号 XXXXXXXX を取消しますか？"`
- 「取消する」→ 第二暗証番号未保持チェック → `Command::CancelOrder` IPC 送信
- 実装: `Action::CancelOrder` → `ConfirmDialog` → `Message::ConfirmCancelOrder` → `Command::CancelOrder` IPC 送信（`src/main.rs:1780-1930`）

### Tu1.4 IPC 受信ハンドラの追加（O1 分） ✅（2026-04-28 完了）

- `Event::OrderModified` / `Event::OrderCanceled` の行差分更新は**不採用**。代わりに `GetOrderList` → `Event::OrderListUpdated` → `Dashboard::distribute_order_list()` → 全 `OrderList` ペインに `set_orders()` 配信（全リスト更新方式）。
- `GetOrderList` レスポンスで `orders` を全更新（`Message::OrderListUpdated`、`src/main.rs:1818`）

### Tu1.5 サイドバー `Order List` メニューの有効化 ✅（2026-04-27 完了）

---

## Phase U2: Toast 通知 + 注文一覧リアルタイム更新（O2 対応） ✅（Tu2.1 完了済み、Tu2.2 自動更新は将来改善）

**ゴール**: 約定通知が Toast で出て、注文一覧が自動更新される。

### Tu2.1 約定 Toast 通知

- `Event::OrderFilled` 受信 → `dashboard::Message::Notification(Toast { ... })` を既存 `widget::toast` で表示
  - 全部約定 (`leaves_qty == 0`): `"{instrument} {side}: {qty}株 全部約定 @{price}"`
  - 部分約定: `"{instrument}: {qty}株 部分約定 @{price} (残{leaves_qty}株)"`
- `Event::OrderCanceled` 受信 → 取消完了の Toast

既存の `dashboard::Message::Notification(Toast)` パターンをそのまま使う（新しいコードを追加しない）。

### Tu2.2 注文一覧のリアルタイム更新（自動更新は将来改善）

現時点の実装: `RefreshClicked` ボタンで手動更新が可能。
`OrderCanceled` / `OrderFilled` 受信時の自動リフレッシュは、`map_engine_event_to_tachibana()` が
`Option<Message>` しか返せないため、単純な実装ではトースト＋自動更新を同時に発行できない。
将来の改善タスク: `Message::OrderListNeedsRefresh` を追加して複合イベントに対応する。
詳細設計: `docs/plan/✅order/fix-order-list-auto-refresh-2026-04-28.md`（未実装）。

---

## Phase U3: Buying Power パネル + フォーム拡張（O3 対応）

**ゴール**: 余力情報を確認でき、信用取引・逆指値・期日指定が注文フォームで使える。

### Tu3.1 `ContentKind::BuyingPower` の追加 ✅（2026-04-27 完了）

新規作成: `src/screen/dashboard/panel/buying_power.rs`

```rust
pub struct BuyingPowerPanel {
    spot_buyable: Option<i64>,       // 現物株買付可能額（円）
    nisa_balance: Option<i64>,       // NISA 成長投資残高
    margin_buyable: Option<i64>,     // 信用新規建可能額
    margin_ratio: Option<f64>,       // 委託保証金率
    margin_call: bool,               // 追証フラグ
    loading: bool,
}
```

`Message`:
- `RefreshClicked` → `Command::GetBuyingPower` IPC
- `BuyingPowerReceived(BuyingPowerData)` → フィールド更新

表示: wiki §「余力情報パネル」の形式に従う。追証時は赤色で `"⚠ 追証確定"` を表示。

**後日バグ修正（2026-04-28）**: サイドバーから BuyingPower ペインを新規登録した場合、VenueReady 後でも `GetBuyingPower` が自動発行されなかった（VenueReady ハンドラは起動時 1 度しか走らないため）。`OpenOrderPanel(ContentKind::BuyingPower)` ハンドラに auto-fetch ロジックを追加して修正。VenueReady / BuyingPowerAction / OpenOrderPanel の 3 経路を `buying_power_request_id` 記録で対称化。詳細: `docs/plan/✅order/fix-buying-power-auto-fetch-on-add-2026-04-28.md`。

### Tu3.2 Order Entry フォームの拡張 ✅（2026-04-27 完了）

`src/screen/dashboard/panel/order_entry.rs` の拡張:
- 口座種別の信用区分を有効化（`sGenkinShinyouKubun = 2/4/6/8`）
- `PriceKind::StopLimit` を追加し、`trigger_price` フィールドを表示
- 期日選択を有効化（当日 / 日付指定、10 営業日以内）
- `AccountType` に信用新規（制度 6M）/ 信用新規（一般）/ 信用返済（制度）/ 信用返済（一般）を追加

### Tu3.3 サイドバー `Buying Power` メニューの有効化 ✅（2026-04-27 完了）

Phase U3 完了時に `[ Buying Power ]` 項目を有効化する。

---

## 変更ファイル一覧

| ファイル | 変更種別 | 担当フェーズ |
|---|---|---|
| `data/src/config/sidebar.rs` | 追記（`Menu::Order` variant） | **U-pre** |
| `data/src/layout/pane.rs` | 追記（`ContentKind` 3 variants + `ALL` + `Display`） | **U-pre**/U1/U3 |
| `src/main.rs` | 追記（`Message` variants、`update` ハンドラ） | U0〜U2 |
| `src/screen/dashboard/pane.rs` | 追記（`Content` variants、スタブ view/update arm） | **U-pre**/U1/U3 |
| `src/screen/dashboard/panel.rs` | 追記（module 宣言 3 つ） | **U-pre** |
| `src/screen/dashboard/sidebar.rs` | 追記（🖊 ボタン + インラインメニュー） | **U-pre**/U1/U3 |
| `src/screen/dashboard/panel/order_entry.rs` | **新規**（シェルは U-pre、IPC 配線は U0、銘柄選択ボタンは 2026-04-28） | **U-pre**/U0/U3 |
| `src/screen/dashboard/panel/order_list.rs` | **新規** | U1 |
| `src/screen/dashboard/panel/buying_power.rs` | **新規** | U3 |
| `src/modal/second_password.rs` | **新規**（シェルは U-pre、IPC 配線は U0） | **U-pre**/U0 |
| `src/modal/order_confirm.rs` | 新規 or 既存 `ConfirmDialog` 再利用確認後決定 | **U-pre** |
| `src/modal/order_modify.rs` | **新規** | U1 |

---

## テスト計画

### 単体テスト（`#[cfg(test)]`）

| テストファイル | 内容 | フェーズ |
|---|---|---|
| `panel/order_entry.rs` 内 | バリデーション（数量 0 → エラー、BUY 以外 → Phase O0 ガード）| U0 |
| `modal/second_password.rs` 内 | Cancel で IPC に `value` が載らない / Submit で `input` が zeroize | U0 |
| `panel/order_list.rs` 内 | `OrderListReceived` で `orders` が更新される | U1 |

### Rust 統合テスト

| テスト | 実行コマンド | フェーズ |
|---|---|---|
| IPC order event dispatch | `cargo test -p flowsurface-engine-client --test order_ipc_event_dispatch` | U0 |
| second password on wire | `cargo test -p flowsurface-engine-client --test creds_no_second_password_on_wire`（Python 側 Tpre.2 と共用）| U0 |

### E2E スモークテスト

| テスト | 検査内容 | フェーズ |
|---|---|---|
| `tests/e2e/order_smoke.sh` | 🖊 ボタン表示 → Order Entry ペイン開閉が 10 秒以内に完了（**IPC 不要 → U-pre 完了後に実施可**） | **U-pre** |
| 同上（発注確認） | フォーム入力 → 確認 modal 表示 → キャンセル（U-pre 段階は IPC なしで確認可） | **U-pre** |
| 同上（IPC 発注） | 「注文を発注する」→ `Event::OrderAccepted` または `Event::OrderRejected` が 10 秒以内 | U0 |
| 同上（U1） | Order List パネル表示 → `[更新]` クリック → 行数 ≥ 0 で応答 | U1 |

---

## 制約・不変条件

1. **第二暗証番号を IPC フレームに直接含めない**: `Command::SetSecondPassword.value` は Python 側メモリ保持用。`Command::SubmitOrder` には含まない（architecture.md §2.4 参照）
2. **立花用語を UI テキストに出さない**: ラベル文字列は nautilus 用語（`OrderSide::BUY` / `SELL`）か日本語ユーザー向け表記（「買い」「売り」）のみ。`sGenkinShinyouKubun` / `sCLMID` 等は Rust UI 層に漏洩させない
3. **REPLAY モード中は注文パネルを無効化**: `REPLAY_MODE_ACTIVE` 状態（`venue_state` で検知）のとき、Order Entry の「注文確認」ボタンを disabled にし `"REPLAYモード中 — 注文は無効です"` を表示する
4. **Panel は `TickerInfo` を持たない場合がある**: `OrderListPanel` / `BuyingPower` は特定のティッカーに紐付かない。`ContentKind` に `TickerInfo` を必須にしない設計にする
5. **`second_password` の Rust 側保持は禁止**: `SecondPasswordModal.input` は modal が閉じたタイミングで zeroize。Rust 側に第二暗証番号を保持するフィールドを追加しない

---

## 既存計画との関係

| 計画 | 関係 |
|---|---|
| [implementation-plan.md](./implementation-plan.md) | Python 側 Phase O0〜O3 と 1:1 対応。Tpre.2 の IPC DTO が本計画の前提 |
| [architecture.md §5](./architecture.md#5-第二暗証番号の取扱い) | 第二暗証番号の modal / メモリ保持 / lockout 仕様の正本 |
| [spec.md §6](./spec.md#6-nautilus_trader-互換要件不変条件) | nautilus 互換不変条件（Rust UI 層に立花用語を漏洩させない） |
| [docs/wiki/orders.md](../../wiki/orders.md) | 完成形 UI の参照仕様（Phase O3 完了後の姿） |

---

## 繰り越し / 次イテレーション

### 制約 #3 REPLAY モード UI ガード（HIGH — 次フェーズ）

**内容**: `order_entry::view()` の submit ボタンを REPLAY モード中は disabled にし  
`"REPLAYモード中 — 注文は無効です"` を表示する。

**理由**: `is_replay_mode` は `order_api.rs` の `OrderApiState`（HTTP API 層）の  
`Arc<AtomicBool>` にのみ存在し、Iced メッセージループへの伝播経路がない。  
実装には以下のいずれかが必要：  
- `Arc<AtomicBool>` を共有 static 経由でサブスクリプションに公開する  
- IPC `EngineEvent::ReplayModeChanged(bool)` を dto.rs に追加する  

**代替策**: IPC 経路の `Command::SubmitOrder` が Python 側で replay mode を検査して  
拒否する（`OrderRejected { reason_code: "REPLAY_MODE_ACTIVE" }`）ことで  
致命的な誤発注は防げる。HTTP 層は既に 503 を返す。  
UI ガードは UX 改善（二重送信防止 / フィードバック）であり、安全弁としては代替策で十分。

**期限**: 次の IPC フェーズ（Tachibana REPLAY モード対応タスク）で実施。
