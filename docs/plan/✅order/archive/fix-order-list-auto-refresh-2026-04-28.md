# Fix: 注文一覧ペインの自動更新欠落

**作業ブランチ**: 新規ブランチ推奨（`fix/order-list-auto-refresh`）  
**日付**: 2026-04-28

---

## 問題

注文入力ペインで注文が受付・約定・取消されても、**注文一覧ペインが自動更新されない**。
「更新」ボタンを手動で押すまで一覧に反映されない。

### 動作の差異

| イベント | 期待動作 | 現在の動作 |
|---|---|---|
| 注文受付（`OrderAccepted`） | 注文一覧に新規行が出る | Toast のみ |
| 約定（`OrderFilled`） | 注文一覧の行が「約定」状態に変わる | Toast のみ |
| 取消完了（`OrderCanceled`） | 注文一覧の行が「取消」状態に変わる | Toast のみ |
| 手動「更新」ボタン | ✅ 機能する | ✅ 機能する |

---

## 根本原因

### 制約：`map_engine_event_to_tachibana()` は `Option<Message>` を 1 つしか返せない

`src/main.rs:881` の `map_engine_event_to_tachibana()` はストリーム内で呼ばれ、
イベントごとに **最大 1 つの `Message`** しか `yield` できない。

```rust
// main.rs:828-833
if let Some(msg) = map_engine_event_to_tachibana(ev) {
    yield msg;
}
```

このため、`OrderFilled` などの EC イベントを受け取ったとき：

```rust
// main.rs:906-920 — OrderFilled を Toast 1 つにしか変換できない
EngineEvent::OrderFilled { ... } => {
    Some(Message::OrderToast(Toast::info(body)))
    // GetOrderList のトリガーを同時に返す手段がない
}
```

### 各トリガーポイントの現状

| イベント | 現在のメッセージ | handler の戻り値 |
|---|---|---|
| `OrderAccepted`（main.rs:1847） | `Message::OrderAccepted` | `Task::none()` 相当（明示 return なし） |
| `OrderFilled`（main.rs:906） | `Message::OrderToast` | `Task::none()` 相当（OrderToast ハンドラは push のみ） |
| `OrderCanceled`（main.rs:922） | `Message::OrderToast` | 同上 |
| `OrderExpired`（main.rs:927） | `Message::OrderToast` | 同上 |

---

## 修正方針

### アプローチ

`map_engine_event_to_tachibana()` の戻り値を `Option<Message>` から `Vec<Message>` に変更し、
1 つのエンジンイベントから **複数の `Message` を yield できるようにする**。
これにより、EC イベント受信時に Toast と `OrderListNeedsRefresh` を同時に発行できる。

あわせて `Message::OrderListNeedsRefresh` を新設し、
このメッセージを受けた `update()` ハンドラが `GetOrderList` IPC を送信する。

### 修正対象ファイル

| ファイル | 変更内容 |
|---|---|
| `src/main.rs` | ①ストリーム改修、②`Message::OrderListNeedsRefresh` 追加、③3 ハンドラへの配線 |

変更は `src/main.rs` 1 ファイルのみ。dto.rs / Python 側は変更不要。

---

## 実装ステップ

### Step 1: `map_engine_event_to_tachibana()` の戻り値を `Vec<Message>` に変更

```rust
// 変更前
fn map_engine_event_to_tachibana(ev: EngineEvent) -> Option<Message> { ... }

// 変更後
fn map_engine_event_to_tachibana(ev: EngineEvent) -> Vec<Message> { ... }
```

ストリーム側の yield も合わせて変更：

```rust
// 変更前（main.rs:831-833）
if let Some(msg) = map_engine_event_to_tachibana(ev) {
    yield msg;
}

// 変更後
for msg in map_engine_event_to_tachibana(ev) {
    yield msg;
}
```

既存の `Some(Message::X)` → `vec![Message::X]`、`None` → `vec![]` に置換するだけで
既存動作に変化はない。

### Step 2: `Message::OrderListNeedsRefresh` の追加

```rust
// Message enum に追加（既存の OrderListUpdated の近くに置く）
/// OrderAccepted / OrderFilled / OrderCanceled 受信後に自動発行。
/// エンジンに GetOrderList を送って注文一覧ペインを更新させる。
OrderListNeedsRefresh,
```

### Step 3: EC イベントのマッピングに `OrderListNeedsRefresh` を追加

```rust
// 変更前
EngineEvent::OrderFilled { ... } => {
    Some(Message::OrderToast(Toast::info(body)))
}
EngineEvent::OrderCanceled { client_order_id, .. } => {
    Some(Message::OrderToast(...))
}
EngineEvent::OrderExpired { client_order_id, .. } => {
    Some(Message::OrderToast(...))
}

// 変更後（vec! で複数返す）
EngineEvent::OrderFilled { ... } => {
    vec![
        Message::OrderToast(Toast::info(body)),
        Message::OrderListNeedsRefresh,
    ]
}
EngineEvent::OrderCanceled { client_order_id, .. } => {
    vec![
        Message::OrderToast(...),
        Message::OrderListNeedsRefresh,
    ]
}
EngineEvent::OrderExpired { client_order_id, .. } => {
    vec![
        Message::OrderToast(...),
        Message::OrderListNeedsRefresh,
    ]
}
```

### Step 4: `OrderAccepted` ハンドラから `OrderListNeedsRefresh` をトリガー

```rust
// 変更前（main.rs:1847）
Message::OrderAccepted { client_order_id, venue_order_id } => {
    let main_window = self.main_window.id;
    self.active_dashboard_mut().notify_order_accepted(main_window, &client_order_id);
    let vid = venue_order_id.unwrap_or_default();
    self.notifications.push(Toast::info(format!("注文受付: {client_order_id} (venue: {vid})")));
    // Task::none() 相当
}

// 変更後
Message::OrderAccepted { client_order_id, venue_order_id } => {
    let main_window = self.main_window.id;
    self.active_dashboard_mut().notify_order_accepted(main_window, &client_order_id);
    let vid = venue_order_id.unwrap_or_default();
    self.notifications.push(Toast::info(format!("注文受付: {client_order_id} (venue: {vid})")));
    return Task::done(Message::OrderListNeedsRefresh);
}
```

### Step 5: `OrderListNeedsRefresh` ハンドラの実装

`Action::RequestOrderList` ハンドラ（main.rs:1730）と同じロジックを抽出して呼ぶ。
重複を避けるため `fn send_get_order_list(&self) -> Task<Message>` ヘルパーを抽出する。

```rust
Message::OrderListNeedsRefresh => {
    // OrderList ペインが存在しない場合は何もしない
    if !self.active_dashboard().has_order_list_pane(self.main_window) {
        return Task::none();
    }
    return self.send_get_order_list();
}
```

```rust
// ヘルパーメソッド（既存の RequestOrderList と共通化）
fn send_get_order_list(&self) -> Task<Message> {
    let Some(conn) = self.engine_connection.as_ref().cloned() else {
        return Task::none();
    };
    Task::perform(
        async move {
            conn.send(engine_client::dto::Command::GetOrderList {
                request_id: uuid::Uuid::new_v4().to_string(),
                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                filter: engine_client::dto::OrderListFilter {
                    status: None,
                    instrument_id: None,
                    date: None,
                },
            })
            .await
            .map_err(|e| e.to_string())
        },
        |res| match res {
            Ok(()) => Message::OrderToast(Toast::info("注文一覧を取得中...".to_string())),
            Err(err) => Message::OrderToast(Toast::error(format!("注文一覧取得失敗: {err}"))),
        },
    )
}
```

> **`has_order_list_pane()` 未実装の場合**: `has_buying_power_pane()` と同じパターンで
> `src/screen/dashboard.rs` に追加する（ContentKind::OrderList を探す）。
> ペインが存在しない場合に無駄な IPC を送らないための最適化。省略しても機能は成立する。

---

## 設計上の注意点

### `OrderListNeedsRefresh` の連打抑制は不要

`buying_power_request_id` のような in-flight ガードは今回は**不要**。理由：
- `GetOrderList` は読み取り専用。副作用なし
- 仮に OrderFilled → OrderCanceled が連続しても、最後の GetOrderList 応答が
  `OrderListUpdated` として全 OrderList ペインに配信されるため整合性は保たれる
- 連打の実用的リスクは小さい（EC イベント頻度は低い）

### 既存の手動「更新」ボタンとの統合

手動更新（`Action::RequestOrderList`）も同じ `send_get_order_list()` ヘルパーを呼ぶように
リファクタリングする。重複コードの削除が目的。機能の変化なし。

---

## Acceptance criteria

- [ ] 注文入力ペインで発注 → `OrderAccepted` 受信直後に注文一覧ペインが更新される
- [ ] 約定（`OrderFilled`）受信後に注文一覧ペインが更新される（約定・部分約定どちらも）
- [ ] 取消完了（`OrderCanceled`）受信後に注文一覧ペインが更新される
- [ ] 注文失効（`OrderExpired`）受信後に注文一覧ペインが更新される
- [ ] 注文一覧ペインが存在しない場合に GetOrderList IPC が飛ばないこと
- [ ] 手動「更新」ボタンも引き続き正常動作すること
- [ ] `cargo test --workspace` 全緑
- [ ] `cargo clippy --workspace -- -D warnings` クリーン
- [ ] `rust-ui-plan.md` の Tu2.2 を「将来の改善タスク」→ ✅ 完了に更新する

---

## 関連ファイル

| ファイル | 役割 |
|---|---|
| `src/main.rs:881` | `map_engine_event_to_tachibana()`（戻り値変更） |
| `src/main.rs:828` | ストリームの yield ループ（for ループに変更） |
| `src/main.rs:1847` | `OrderAccepted` ハンドラ（`OrderListNeedsRefresh` を return） |
| `src/main.rs:906` | `OrderFilled` マッピング（vec! に変更） |
| `src/main.rs:922` | `OrderCanceled` マッピング（vec! に変更） |
| `src/main.rs:927` | `OrderExpired` マッピング（vec! に変更） |
| `src/main.rs:1730` | `Action::RequestOrderList` ハンドラ（ヘルパー抽出で共通化） |
| `src/screen/dashboard.rs` | `has_order_list_pane()` 追加（任意最適化） |
| `docs/plan/✅order/rust-ui-plan.md` | Tu2.2 を ✅ 完了に更新（Phase U2 ヘッダー・本文） |
