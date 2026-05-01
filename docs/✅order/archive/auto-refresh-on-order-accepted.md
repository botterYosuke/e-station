# 注文確定・起動時に注文一覧・買余力を自動更新する

**作成日**: 2026-04-30
**優先度**: Low（UX 改善）

---

## 背景・動機

HONDA 100 株注文の動作確認（debug-honda-order-no-response 修正後）で、
以下 2 つの場面で手動「更新」ボタンが必要なことが判明。

**問題 1 — 注文確定後に注文一覧・買余力が更新されない**:
```
OrderAccepted 受信
  → notify_order_accepted()
    → OrderEntry の submitting フラグをリセット
    → toast「注文送信完了」
  ← 終了（注文一覧・買余力は更新されない）
```

**問題 2 — 起動時に注文一覧が自動更新されない**:
```
VenueReady 受信（起動時の立花ログイン完了）
  → 買余力は自動取得（GetBuyingPower IPC を自動発行）  ← 実装済み
  ← 注文一覧は取得されない（GetOrderList IPC が発行されない）
```

**理想の流れ（問題 1）**:
```
OrderAccepted 受信
  → notify_order_accepted()  （既存。変更なし）
  → GetOrderList IPC 送信 → OrderListUpdated → distribute_order_list()
  → GetBuyingPower IPC 送信 → BuyingPowerUpdated → distribute_buying_power()
```

**理想の流れ（問題 2）**:
```
VenueReady 受信（is_ready=true かつ OrderList ペインが表示中）
  → GetBuyingPower IPC 送信  （既存。変更なし）
  → GetOrderList IPC 送信   （追加）→ OrderListUpdated → distribute_order_list()
```

---

## Goal

以下 2 つの場面で自動更新を行い、ユーザーが手動「更新」ボタンを押さずとも
注文一覧・買余力が最新状態になる。

1. **`OrderAccepted` 受信時**: `GetOrderList` と `GetBuyingPower` を自動発行
2. **起動時（`VenueReady` 受信時）**: 買余力と同様に `GetOrderList` を自動発行

## Constraints

1. replay モードには影響を与えない（live 専用の自動更新）
   - **判別方法**: `Message::OrderAccepted` ハンドラで `self.tachibana_state.is_ready()` が `false` のときはスキップする、または `self.mode == AppMode::Live` をガードとして使う（どちらかを実装時に選択する）
   - Python の replay バックテストも `OrderAccepted` を emit するため、この判別は必須
2. 既存の「更新」ボタンによる手動更新は引き続き動作すること
3. `OrderRejected` 時は不要（失敗した注文は一覧に載らない。買余力も変動しない）
4. Iced Elm 逸脱・silent failure 禁止
   - `engine_connection == None` の場合: `OrderAccepted` / `VenueReady` はエンジン経由イベントであり、受信時点で engine_connection は必ず Some。`Task::none()` を返す defensive コードは記述するが、この分岐に到達することは実際には起きない（Constraint 4 の例外ではなく dead code として扱う）
5. `cargo fmt` / `cargo clippy -- -D warnings` / `cargo test --workspace` 全 PASS

---

## Acceptance criteria

- [x] 起動時（`VenueReady` 受信、`is_ready=true` かつ OrderList ペインが表示中）に注文一覧が自動更新される
- [x] `OrderAccepted` 受信後に注文一覧が自動更新される（手動「更新」不要）
- [x] `OrderAccepted` 受信後に買余力が自動更新される（手動「更新」不要）
- [x] replay モードで `OrderAccepted` が来ても `GetOrderList`/`GetBuyingPower` IPC が発行されない（`tachibana_state.is_ready()` ガードで早期 return）
- [x] `cargo fmt` / `cargo clippy -- -D warnings` / `cargo test --workspace` / `uv run pytest python/tests/` 全 PASS（pytest 失敗は既存の nautilus/strategy 関連で今回変更と無関係）
- [x] `OrderRejected` メッセージ受信時に `GetOrderList` IPC が発行されないことをログで確認
- [x] replay モードで `OrderAccepted` が来ても `GetOrderList`/`GetBuyingPower` の意図しない副作用がないことを確認（`tachibana_state.is_ready()` ガードで早期 return）

---

## 実装計画

### 変更ファイル: `src/main.rs`（2 箇所）＋ `src/screen/dashboard.rs`（1 メソッド追加）

配信インフラ（`distribute_order_list` / `distribute_buying_power`）は完成済み。
`has_buying_power_pane()` に倣って `has_order_list_pane()` を `src/screen/dashboard.rs` に追加する。

> **スコープ外: VenueReady 後に OrderList ペインを追加した場合の catch-up 経路**
> `BuyingPower` ではペイン追加時の catch-up が `src/main.rs:2656` に実装されているが、
> `OrderList` の同等経路は今回追加しない。ログイン後に OrderList ペインを新規開いた
> 場合は手動「更新」ボタンが引き続き必要になる。今後の別タスクで対応する。

---

### 変更 1 — 起動時自動更新（`VenueReady` ハンドラ, `main.rs:1478` 付近）

**現在のコード（抜粋）**:
```rust
// Auto-fetch buying power on venue ready if a pane is visible.
let auto_fetch = if is_ready
    && self.buying_power_request_id.is_none()
    && self.active_dashboard().has_buying_power_pane(main_window)
{
    // GetBuyingPower IPC 送信 ...
} else {
    Task::none()
};
```

**変更後イメージ**:
```rust
// Auto-fetch buying power on venue ready.
let auto_fetch_buying_power = if is_ready
    && self.buying_power_request_id.is_none()
    && self.active_dashboard().has_buying_power_pane(main_window)
{
    // GetBuyingPower IPC 送信（既存。変更なし）
} else {
    Task::none()
};

// Auto-fetch order list on venue ready（新規追加。buying_power と同パターン）
let auto_fetch_orders = if is_ready
    && self.active_dashboard().has_order_list_pane(main_window)
{
    // GetOrderList IPC 送信
} else {
    Task::none()
};

replay.chain(auto_fetch_buying_power).chain(auto_fetch_orders)
```

> **注記**: `VenueReady` は再ログイン時にも発火するため、再ログイン後も注文一覧が再取得される（副作用は許容する）。

`has_order_list_pane()` は `has_buying_power_pane()` と同実装パターンで
`src/screen/dashboard.rs` に追加する。

---

### 変更 2 — 注文確定後自動更新（`Message::OrderAccepted` ハンドラ, `main.rs:2235` 付近）

**現在のコード**:
```rust
Message::OrderAccepted { .. } => {
    self.notify_order_accepted(...);
    Task::none()
}
```

**変更後イメージ**:
```rust
Message::OrderAccepted { .. } => {
    self.notify_order_accepted(...);   // void。戻り値なし

    // live モードでのみ注文一覧・買余力を自動更新
    // replay バックテストも OrderAccepted を emit するため、live 判別ガードが必須
    if !self.tachibana_state.is_ready() {
        return Task::none();
    }

    let refresh_orders        = /* GetOrderList IPC 送信 */;
    let refresh_buying_power  = /* GetBuyingPower IPC 送信（buying_power_request_id.is_none() ガード付き）*/;

    Task::batch([refresh_orders, refresh_buying_power])
}
```

> **実装上の注記**:
> - `notify_order_accepted` は `()` を返す（void）。`let notify = ...` は不要。
> - **live/replay 判別**: `tachibana_state.is_ready()` が false のとき（= replay モード等で立花が未ログイン）はスキップする。`self.mode == AppMode::Live` による判別も可。どちらを使うかは実装時に決定する。
> - `GetBuyingPower` 送信前に既存の `Action::RequestBuyingPower` ハンドラと同様に `buying_power_request_id.is_none()` ガードを通すこと。
> - `GetOrderList` は `order_list_request_id` 管理フィールドを追加しない（idempotent）。高速連続発行は Python 側で安全に処理される。
> - `has_order_list_pane()` ガードは設けない（OrderList ペインが後から追加された場合も即反映するため）。
> - `engine_connection` が None のときは `Task::none()` を返す。`OrderAccepted` はエンジン経由イベントのため受信時点で engine_connection は必ず Some であり、この分岐は defensive コードとして記述するが実際には到達しない。

GetOrderList / GetBuyingPower の IPC 送信実装は
`main.rs:2003-2034` / `main.rs:1964-1998` の既存ロジックを参照する。

---

## 既存インフラの確認

| コンポーネント | ファイル | 行 | 役割 |
|---|---|---|---|
| 起動時自動取得（買余力） | `src/main.rs` | 1478-1513 | `VenueReady` → `has_buying_power_pane` → `GetBuyingPower` |
| 起動時自動取得（注文一覧）| `src/main.rs` | 1478 付近（追加） | `VenueReady` → `has_order_list_pane` → `GetOrderList` |
| 更新ボタン（注文一覧） | `src/screen/dashboard/panel/orders.rs` | 56-57, 79-81 | `RefreshClicked` → `Action::RequestOrderList` |
| 更新ボタン（買余力） | `src/screen/dashboard/panel/buying_power.rs` | 103, 108-111 | `RefreshRequested` → `Action::RequestBuyingPower` |
| GetOrderList IPC 送信 | `src/main.rs` | 2003-2034 | `Action::RequestOrderList` → `Command::GetOrderList` |
| GetBuyingPower IPC 送信 | `src/main.rs` | 1964-1998 | `Action::RequestBuyingPower` → `Command::GetBuyingPower` |
| 注文一覧 配信 | `src/screen/dashboard.rs` | 689-713 | `distribute_order_list()` |
| 買余力 配信 | `src/screen/dashboard.rs` | 722-739 | `distribute_buying_power()` |
| Python GetOrderList | `python/engine/server.py` | 611-614, 1453-1535 | Tachibana API 呼び出し → `OrderListUpdated` |
| Python GetBuyingPower | `python/engine/server.py` | 616-619, 1537-1600 | Tachibana API 呼び出し → `BuyingPowerUpdated` |

---

## テスト方針

`Message::OrderAccepted` ハンドラが `GetOrderList` と `GetBuyingPower` を
発行することの単体確認は Iced の Task ツリー検査が困難なため、
E2E（実機または smoke.sh）での動作確認を主とする。

**目視確認項目**:
- `cargo run`（debug ビルド）のログで `[ipc] → GetOrderList` と `[ipc] → GetBuyingPower` が `OrderAccepted` ログの直後に出ることを確認する
- `VenueReady` 後のログに `[ipc] → GetOrderList` が出ることを確認する

**Rust ユニットテスト候補**: `test_order_accepted_emits_get_order_list`（`cargo test --workspace`、追加要否は `/bug-postmortem` で判断）

実装後に `/bug-postmortem` を実行し、推奨テストがあれば追加して PASS を確認すること。

---

## スコープ外（今回対象外）

- `OrderRejected` 時の自動更新（不要）
- WebSocket push による注文状態の push 通知（将来タスク）
- 注文一覧のリアルタイムストリーミング（将来タスク）
