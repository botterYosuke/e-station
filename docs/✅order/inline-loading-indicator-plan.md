# 注文一覧 / 買余力 取得中インジケータをペイン内に移す

作成日: 2026-05-01
ステータス: planning

## 1. 背景と問題

現状、注文一覧 (`OrdersPanel`) と買余力 (`BuyingPowerPanel`) の取得トリガ
（自動 fetch / 手動 Refresh / venue ready 直後の auto fetch）が走ると、
完了通知ではなく「送信成功」直後に以下の **toast** が `self.notifications`
に push される:

- 「注文一覧を取得中...」
- 「余力情報を取得中...」

toast は画面**左上**のグローバル通知エリアに数秒だけ出るため、
ユーザーがペイン本体（しばしば画面中央〜右側）を注視していると気付かない。

該当箇所（シンボル名併記。[A2] 反映）:

- [src/main.rs:1571](../../src/main.rs#L1571) — venue ready 時の auto buying-power fetch（`Message::EngineConnected` 内 `BuyingPowerAction` 経路）
- [src/main.rs:1607](../../src/main.rs#L1607) — venue ready 時の auto order-list fetch（同上 `OrderListAction::RequestOrderList` 経路）
- [src/main.rs:2093](../../src/main.rs#L2093) — `BuyingPowerAction` ハンドラ内 `Task::perform` 直前の toast push
- [src/main.rs:2143](../../src/main.rs#L2143) — `OrderListAction::RequestOrderList` ハンドラ内 `Task::perform` 直前の toast push
- [src/main.rs:2541](../../src/main.rs#L2541) — `OrderAccepted` 受信後の order-list 自動更新
- [src/main.rs:2564](../../src/main.rs#L2564) — `OrderAccepted` 受信後の buying-power 自動更新
- [src/main.rs:3014](../../src/main.rs#L3014) — エンジン再接続時の auto buying-power fetch（`Message::EngineConnected` ハンドラ内）

## 2. ゴール

- 「取得中…」は対象ペイン**内**に表示する（バッジ文言は「更新中…」に統一。[A1] 反映）
- 既存データ（前回の一覧 / 余力値）は消さず、上に「⟳ 更新中…」インジケータを重ねる
- 完了 (`OrderListUpdated` / `BuyingPowerUpdated`) またはエラー (`IpcError`) で自動的に消える
- toast 経由の「取得中…」通知はすべて廃止する
- エラー toast（送信失敗・エンジン未接続など）は **残す**（通知性が必要なため）

## 3. 設計

### 3.1 ペイン状態の追加

#### `BuyingPowerPanel` ([src/screen/dashboard/panel/buying_power.rs](../../src/screen/dashboard/panel/buying_power.rs))

```rust
pub struct BuyingPowerPanel {
    // ... 既存フィールド
    loading: bool,    // ★ 追加
}

impl BuyingPowerPanel {
    pub fn set_loading(&mut self, loading: bool) {
        self.loading = loading;
    }
}
```

完了系メソッド (`set_cash_buying_power` / `set_credit_buying_power` /
`set_replay_portfolio` / `set_error`) の末尾で `self.loading = false` を実行。

**[stale-error 反映 — エラークリア条件（HIGH）]**:
失敗 → 再 Refresh で error が残り続けると view が error 優先表示モードに留まり、
新しい「⟳ 更新中…」バッジが出ない（§3.2 view の `error.is_some()` 優先分岐）。
これを防ぐため、以下のクリア規約を **必ず** 守る:

- `set_loading(true)` で `self.error = None` を実行（再試行開始時にエラー履歴を消す）
- 成功 setter (`set_cash_buying_power` / `set_credit_buying_power` /
  `set_replay_portfolio`) は既存実装で `self.error = None` を実施済み（[src/screen/dashboard/panel/buying_power.rs:68](../../src/screen/dashboard/panel/buying_power.rs#L68) 周辺）。本フェーズで規約として明文化する

```rust
pub fn set_loading(&mut self, loading: bool) {
    if loading {
        self.error = None;   // 再試行開始時にエラーを消す
    }
    self.loading = loading;
}
```

**[C2] 反映 — `set_error` の解除単位**:
`set_error` 末尾の `loading=false` は **同 pane 単位**で行う。
ただし error 配信ヘルパ `distribute_buying_power_error`（src/screen/dashboard.rs:850 周辺）は
**全 BuyingPower ペイン broadcast** であるため、結果として「他ペインの in-flight loading
も巻き添え解除する」挙動になる。これは pane 単位の request_id を持たない設計上の必然であり
**許容仕様** とする（§6 / §7 にも明記）。

#### `OrdersPanel` ([src/screen/dashboard/panel/orders.rs](../../src/screen/dashboard/panel/orders.rs))

```rust
pub struct OrdersPanel {
    // ... 既存フィールド
    loading: bool,    // ★ 追加
}

impl OrdersPanel {
    pub fn set_loading(&mut self, loading: bool) {
        self.loading = loading;
    }
}
```

`set_orders` の末尾で `self.loading = false`。

**[R02] 反映 — `OrdersPanel::set_error` を本フェーズで必須新設する**:

```rust
pub struct OrdersPanel {
    // ... 既存フィールド
    loading: bool,
    last_error: Option<String>,   // ★ R02 で新設
}

impl OrdersPanel {
    pub fn set_loading(&mut self, loading: bool) {
        if loading {
            self.last_error = None;     // 再試行開始時にエラーを消す（stale-error 対策）
        }
        self.loading = loading;
    }

    pub fn set_orders(&mut self, orders: Vec<OrderRecordWire>) {
        self.orders = orders;
        self.last_error = None;          // 成功時もエラーをクリア（stale-error 対策）
        self.loading = false;
    }

    pub fn set_error(&mut self, message: String) {
        self.last_error = Some(message);
        self.loading = false;            // setter 一元化（統一決定 2）
    }
}
```

view 側は `last_error.is_some()` のとき `BuyingPowerPanel` と同様に
「注文一覧取得エラー」+ 詳細を表示する。これに対応する dashboard ヘルパ
`distribute_order_list_error(main_window, message)` も §3.3 に追加する。

**直接 `distribute_order_list_loading(false)` を error 経路で叩く形は採らない**
（統一決定 2 / [R02]）。

### 3.2 view への反映

#### `BuyingPowerPanel::view`

既存データ表示の上に `loading` バッジを差し込む。レイアウト崩れを避けるため、
ヘッダー行に `text("⟳ 更新中…").size(11)` を inline 表示する。
`error` がある場合は error を優先表示し、loading は重ねない。

```rust
let header: Element<_> = if panel.loading {
    row![text("余力").size(12), text("⟳ 更新中…").size(11)]
        .spacing(8).into()
} else {
    text("余力").size(12).into()
};
```

#### `OrdersPanel::view`

ヘッダー行（タイトル + Refresh ボタン）の右隣に `⟳ 更新中…` を表示する。
既存の orders テーブルはそのまま描画し続ける（古いデータを保持したまま「更新中」
を示す）。

**[C5] フォントフォールバック注記**:
`⟳`（U+27F3）が iced 既定フォントで欠ける（tofu 表示になる）場合は、
半角矢印「↻」または `[更新中]` プレフィックスにフォールバックする。
実機確認で欠ける場合のみ後者へ切替。

### 3.3 Dashboard 側のブロードキャストヘルパ

[src/screen/dashboard.rs](../../src/screen/dashboard.rs) に 2 つ追加:

```rust
pub fn distribute_order_list_loading(
    &mut self, main_window: window::Id, loading: bool,
) {
    self.iter_all_panes_mut(main_window).for_each(|(_, _, state)| {
        if let pane::Content::OrderList(panel) = &mut state.content {
            panel.set_loading(loading);
        }
    });
}

pub fn distribute_buying_power_loading(
    &mut self, main_window: window::Id, loading: bool,
) {
    self.iter_all_panes_mut(main_window).for_each(|(_, _, state)| {
        if let pane::Content::BuyingPower(panel) = &mut state.content {
            panel.set_loading(loading);
        }
    });
}

// [R02] 反映 — OrderList 用エラー配信ヘルパ（既存 distribute_buying_power_error と対称）
pub fn distribute_order_list_error(
    &mut self, main_window: window::Id, message: String,
) {
    self.iter_all_panes_mut(main_window).for_each(|(_, _, state)| {
        if let pane::Content::OrderList(panel) = &mut state.content {
            panel.set_error(message.clone());
        }
    });
}
```

### 3.4 main.rs の fetch トリガ修正

#### 3.4.1 in-flight tracking（OrderList も追加）

統一決定 1 に従い、`main.rs` の App state に **両方の** request_id を持つ:

```rust
buying_power_request_id: Option<String>,   // 既存
order_list_request_id: Option<String>,     // ★ 新設
```

`order_list_request_id` の役割:
- `OrderListAction::RequestOrderList` 受信時に Some をセット → 送信
- `OrderListUpdated` 受信時 / `IpcError` 経路で照合して None に戻す
- Some 中の重複 Refresh は撥ねる（Python 側に冗長 IPC を送らないため。**[B5] 反映**）

#### 3.4.2 送信前ガード順序（**[B2] 統一決定 4**）

7 箇所すべてで以下の順序を守る:

1. **engine 接続確認**（`engine_conn` が `Some` か）
   - 未接続なら error toast を push して `Task::none()` で early return。
     **`distribute_*_loading(true)` は呼ばない**
2. （OrderList のみ）`order_list_request_id.is_some()` なら早期 return（重複抑止）
3. `distribute_*_loading(main_window, true)` で全該当ペインを loading 状態にする
4. `*_request_id = Some(req_id)` を立てる
5. `Task::perform` で送信

#### 3.4.3 送信完了 Message（**[B3] 統一決定 6**）

新規 Message:

```rust
Message::OrderListSendCompleted(Result<(), String>),
Message::BuyingPowerSendCompleted(Result<(), String>),
```

ハンドラ（**[R01] 反映** — 全ブランチが `Task<Message>` を返すよう明示）:

```rust
Message::OrderListSendCompleted(Ok(()))   => Task::none(),  // 完了は OrderListUpdated 待ち
Message::OrderListSendCompleted(Err(err)) => {
    // [R02] 反映: 専用 setter 経由で loading=false にする（統一決定 2 完全準拠）。
    self.active_dashboard_mut()
        .distribute_order_list_error(main_window, err.clone());
    self.order_list_request_id = None;
    self.notifications.push(Toast::error(format!("注文一覧取得失敗: {err}")));
    Task::none()
}
Message::BuyingPowerSendCompleted(Ok(()))   => Task::none(),
Message::BuyingPowerSendCompleted(Err(err)) => {
    // BuyingPower は set_error 経由で loading=false が立つため、
    // distribute_buying_power_error(err) を呼ぶだけで完結する（[B4] 反映）。
    self.active_dashboard_mut()
        .distribute_buying_power_error(main_window, err.clone());
    self.buying_power_request_id = None;
    self.notifications.push(Toast::error(format!("余力情報取得失敗: {err}")));
    Task::none()
}
```

`Message::Noop` の新設は不要（**[B3] 反映**）。

#### 3.4.4 IpcError 経路（統一決定 1 / 2）

現状 `IpcError` ハンドラは `buying_power_request_id` でしか request_id 照合していない
（src/main.rs:2282-2305 周辺）。ここに **OrderList 側の照合** を追加する:

- request_id が `buying_power_request_id` と一致 → `distribute_buying_power_error` 経由で
  setter が loading=false にする（**`distribute_buying_power_loading(false)` を別途呼ばない**。
  **[B4] 反映**）
- request_id が `order_list_request_id` と一致 → `distribute_order_list_error` 経由で
  setter（後述 §3.1 の `OrdersPanel::set_error`）が loading=false にする
  （**[R02] 反映** — 暫定実装は採用しない。統一決定 2 完全準拠）

両 None 化も同所で実施。

#### 3.4.5 EngineConnected / 切断時の loading 解除（**[C1] 統一決定 3**）

`Message::EngineConnected` ハンドラ（src/main.rs:3014 周辺）で:

```rust
self.buying_power_request_id = None;
self.order_list_request_id = None;
self.active_dashboard_mut()
    .distribute_buying_power_loading(main_window, false);
self.active_dashboard_mut()
    .distribute_order_list_loading(main_window, false);
```

これにより socket 切断 → 再接続のサイクルで「永久 loading」状態に陥らない。

**[R03] 反映 — 切断検知点の特定**:
既存コードで切断時に経由するのは `dashboard.notify_engine_disconnected(main_window)`
（src/main.rs:1395 周辺、`Message::EngineRestarting` 系の経路）。同所で
`distribute_*_loading(main_window, false)` を併せて呼び、`*_request_id = None` にする。
`Message::EngineConnected`（再接続成功時）と `notify_engine_disconnected`（切断検知時）
の **2 箇所両方**で解除すれば、再接続失敗で `EngineConnected` が再発火しないケースでも
loading が永久残存しない。

### 3.5 廃止する文字列

`"注文一覧を取得中..."` / `"余力情報を取得中..."` を含む `Toast::info` 生成は
全部削除する。grep 検証は **[A3] / [R05] 反映** で §5.3 のリグレッションテストと
同一の正規表現 `Toast::info\([^)]*取得中` を使って統一する:

```bash
rg 'Toast::info\([^)]*取得中' src/
```

新バッジ語「更新中…」とは語が異なるため衝突しない（統一決定 5）。

## 4. 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| [src/screen/dashboard/panel/buying_power.rs](../../src/screen/dashboard/panel/buying_power.rs) | `loading: bool` + `set_loading()`（**stale-error 対策**: `loading=true` で `error=None` クリア）+ view にバッジ + 各 setter (`set_cash_buying_power` / `set_credit_buying_power` / `set_replay_portfolio` / `set_error`) で `loading=false`（成功 setter は既存実装で `error=None` 済み） |
| [src/screen/dashboard/panel/orders.rs](../../src/screen/dashboard/panel/orders.rs) | `loading: bool` + `last_error: Option<String>` フィールド新設 + `set_loading()`（**stale-error 対策**: `loading=true` で `last_error=None` クリア）+ `set_error()`（**[R02]** — `loading=false` も同時実施）+ view にバッジと error 表示 + `set_orders` で `loading=false` + `last_error=None` |
| [src/screen/dashboard.rs](../../src/screen/dashboard.rs) | `distribute_order_list_loading` / `distribute_buying_power_loading` / `distribute_order_list_error`（**[R02]** 新規）を追加 |
| [src/main.rs](../../src/main.rs) | 7 箇所の fetch トリガ修正 + `OrderListSendCompleted` / `BuyingPowerSendCompleted` Message 追加 + `order_list_request_id` フィールド新設 + `IpcError` 経路で OrderList も `distribute_order_list_error` 経由で解除 + `EngineConnected` で全 loading 解除 + `notify_engine_disconnected`（**[R03]** src/main.rs:1395 周辺）でも `distribute_*_loading(false)` + `*_request_id = None` |
| `tests/regression_loading_toast_strings.rs`（新規） | grep リグレッションガード（§5.3） |

## 5. テスト計画

実行コマンド: `cargo test --workspace`（統一決定 10）

対象ユニットテストファイル:
- `src/screen/dashboard/panel/buying_power.rs`
- `src/screen/dashboard/panel/orders.rs`

### 5.1 ユニットテスト（ライフサイクル網羅。**[D2] 反映**）

#### `buying_power.rs`
- `set_loading(true)` → `loading == true`
- `set_loading(true)` 後に `set_cash_buying_power` → `loading == false`
- `set_loading(true)` 後に `set_credit_buying_power` → `loading == false`
- `set_loading(true)` 後に `set_replay_portfolio` → `loading == false`
- `set_loading(true)` 後に `set_error` → `loading == false`
- **stale-error クリア（HIGH 反映）**: `set_error("...")` 後に `set_loading(true)` →
  `error == None` かつ `loading == true`（再試行で stale エラーが消える）

#### `orders.rs`
- `set_loading(true)` 後に `set_orders` → `loading == false`
- `set_loading(true)` 後に `set_error("...")` → `loading == false` かつ `last_error == Some(_)`（**ラウンド 3 追加**）
- **stale-error クリア（HIGH 反映）**: `set_error("...")` 後に `set_loading(true)` →
  `last_error == None` かつ `loading == true`
- **成功時の stale-error クリア（HIGH 反映）**: `set_error("...")` 後に `set_orders(...)` →
  `last_error == None` かつ `loading == false`

#### Negative test（**[D4] 反映**）
- `BuyingPowerPanel::set_loading(false)` の初期状態で `set_replay_portfolio` を呼んでも
  `loading == false` のまま（streaming push 経路は loading フィールドを **触れない**）
  ことを assert

#### ライフサイクル統合テスト（main.rs / dashboard.rs 経路。**5 経路すべて**）

**設計上、error 経路の loading 解除は setter 一元化**（統一決定 2 / [B4] / [R02]）。
よってテストも「`distribute_*_loading(false)` が直接呼ばれること」ではなく
「**setter 経由で `panel.loading == false` になること**」を assert する（test-wording 反映）。

1. **IpcError 経路**: `IpcError(req_id)` 受信で当該 request_id 一致時に
   `distribute_*_error` 経由で `set_error` が呼ばれ、結果として
   `panel.loading == false` かつ `panel.error / last_error == Some(_)` になること
2. **SendCompleted Err 経路**: `Message::*SendCompleted(Err(_))` で同様に
   `distribute_*_error` 経由で setter が呼ばれ `panel.loading == false` になること
   + error toast が push されること
3. **EngineConnected による解除経路**: `Message::EngineConnected` で
   `buying_power_request_id` / `order_list_request_id` が None になり、
   `distribute_*_loading(false)` が両方呼ばれて `panel.loading == false` になること
   （切断 → 再接続パスのみ `distribute_*_loading(false)` を直接呼ぶ — error メッセージは残さない設計）
4. **切断検知経路（[R03]）**: `dashboard.notify_engine_disconnected(main_window)` の
   呼び出し前後で同様に `*_request_id = None` + `distribute_*_loading(false)` が
   実行され `panel.loading == false` になること
5. **stale-error クリア経路（HIGH 反映）**: `set_error("...")` で error 状態にした後、
   `set_loading(true)` を呼ぶと `panel.error / last_error == None` になり、
   view が再び loading バッジを表示する状態になること

（main.rs のハンドラを直接ユニットテストで叩く構造ではないため、現実的には
`buying_power.rs` / `orders.rs` の setter 単体テスト + dashboard 配信ヘルパの
`iter_all_panes_mut` 経由テストで等価検証する。）

### 5.2 ログ観測点 + 画面目視（**[D3] 反映**）

実コマンド: `cargo test --workspace` 実行後、debug ビルドで以下を目視:

```bash
cargo build
FLOWSURFACE_ENGINE_TOKEN=dev-token cargo run -- --mode live --data-engine-url ws://127.0.0.1:19876/
```

**ログ観測点**（INFO レベルで出力されている前提。無ければ追加してよい）:
- `buying_power: loading=true (req_id=...)` 送信時
- `buying_power: loading=false (req_id=...)` 完了時
- `order_list: loading=true / false (req_id=...)` 同上

**画面目視チェックリスト**:
1. live モードで起動 → BuyingPower / OrderList ペインを置く
2. venue ready 直後にペイン内に「⟳ 更新中…」が出て、データ到着で消えることを確認
   （**[R06] 反映** — `⟳`(U+27F3) が tofu（□表示）になっていないかを併せて確認。
   tofu が出る場合は §3.2 のフォールバックに切替）
3. Refresh ボタン押下 → ペイン内インジケータが点灯 → 完了で消灯
4. エンジンを停止した状態で Refresh → エラー toast は出る、loading は解除される
5. 左上の toast に「取得中…」系が出ないことを確認
6. エンジン停止 → 再起動（自動再接続）で loading が永久点灯にならないことを確認
7. **[R07 / replay-refresh-scope 反映]** — replay モードで起動:
   - **BuyingPower** ペイン (`is_replay=true`) は手動 Refresh ボタンが無く
     「⟳ 更新中…」バッジは常に出ないことを確認
   - **OrderList** ペイン (`is_replay=true`) は Refresh ボタンが live と同じく動作し、
     押下時にバッジが点灯 → 更新完了で消灯することを確認
8. 一度 fetch を失敗させた後 (engine 停止時の Refresh) → engine 再起動して再 Refresh:
   ペイン内のエラー表示が消え「⟳ 更新中…」バッジが出ることを確認
   （**stale-error 対策の動作確認**）

### 5.3 grep リグレッションガード（**[D1] 統一決定 7**）

新規ファイル `tests/regression_loading_toast_strings.rs` を追加。
src 配下に廃止文字列が残っていないことを assert する Rust 統合テスト:

```rust
//! 廃止文字列「取得中」が `Toast::info` 生成箇所に残らないことをガードする
//! リグレッションテスト。新バッジ文言「更新中…」とは衝突しない。
//!
//! [R04] 注記: この regex は `Toast::info("...取得中...")` のような静的リテラル
//! 前提で書かれている。将来 `Toast::info(format!("...{x}...取得中..."))` のように
//! `format!` 経由で動的生成された場合、`[^)]*` が format! 内側の `(` で
//! マッチを切り、検知漏れする可能性がある。動的生成版を導入する際は、
//! 別途「format! 内 引数文字列の取得中検知」を AST ベースで追加すること。

use std::fs;
use std::path::Path;

fn read_rs_files_recursive(dir: &Path, out: &mut Vec<String>) {
    for entry in fs::read_dir(dir).unwrap() {
        let entry = entry.unwrap();
        let path = entry.path();
        if path.is_dir() {
            read_rs_files_recursive(&path, out);
        } else if path.extension().and_then(|s| s.to_str()) == Some("rs") {
            if let Ok(content) = fs::read_to_string(&path) {
                out.push(format!("{}\n{}", path.display(), content));
            }
        }
    }
}

#[test]
fn no_toast_info_with_torichu() {
    let mut files = Vec::new();
    read_rs_files_recursive(Path::new("src"), &mut files);
    let re = regex::Regex::new(r"Toast::info\([^)]*取得中").unwrap();
    for blob in &files {
        assert!(
            !re.is_match(blob),
            "廃止文字列『取得中』を含む Toast::info が残っています:\n{}",
            blob.lines().take(1).next().unwrap_or("")
        );
    }
}
```

（`regex` crate を `dev-dependencies` に既に持っていなければ追加する。
持っていない場合は `str::contains("取得中")` + `str::contains("Toast::info")` の
2 段階 contains で代替してよい。）

## 6. 非ゴール / 後回し

- toast の表示位置の全体的な見直し（右下移動など）は本対応の範囲外
- loading 中の Refresh ボタン無効化は派生 UX として有用だが、別タスクとする

### 6.1 loading 表示の対象範囲（replay 含む）

**replay-refresh-scope 反映 — 現行コード調査結果（MEDIUM）**:

| ペイン | live | replay | 根拠 |
|---|---|---|---|
| `BuyingPowerPanel` | loading 対象 | **対象外** | view の `refresh_btn` は live ブランチでのみ生成（[src/screen/dashboard/panel/buying_power.rs:169](../../src/screen/dashboard/panel/buying_power.rs#L169) 周辺）。replay は streaming push のみで手動 Refresh 経路なし |
| `OrdersPanel` | loading 対象 | **loading 対象（含める）** | `Message::RefreshClicked → Action::RequestOrderList` は live/replay 共通で発火する（[src/screen/dashboard/panel/orders.rs:81](../../src/screen/dashboard/panel/orders.rs#L81)）。main.rs:2118 周辺で venue を `"replay"` に切替送信される |

つまり:
- **`BuyingPowerPanel` の `is_replay=true` ペイン**は `loading=false` 固定。streaming
  push (`set_replay_portfolio`) は loading フィールドを触れない（§5.1 Negative test [D4]）
- **`OrdersPanel` の `is_replay=true` ペイン**は live と同じく loading 対象。
  Refresh 押下 → loading=true → `OrderListUpdated`（venue=replay 経由）で loading=false
- live → replay モード切替は **別プロセス起動** で発生するため、ペイン状態の引きずりは発生しない（統一決定 9 の前提）

### 6.2 新規ペイン open 時の loading 初期化

**[C3] / 統一決定 8 反映**:
- in-flight 中（`buying_power_request_id` / `order_list_request_id` が Some）に
  新規ペインを開いても、その新ペインは **`loading=false` で生成** する（in-flight 状態は引き継がない）
- 完了 event は `distribute_*` で全ペインに broadcast されるが、新規ペインは元々
  loading 表示が立っていないため視覚的変化は無し
- データ未着のまま空表示になり得るが、ユーザーが Refresh を押せば回復するため対象外

### 6.3 set_error の broadcast 副作用（許容仕様）

**[C2] 反映 / 統一決定 2 と接続**:
- `distribute_buying_power_error` は全 BuyingPower ペイン一括適用のため、
  ある pane の送信失敗が他ペインの in-flight loading も巻き添え解除する
- pane 単位 request_id を持たない設計選択上の必然であり、本フェーズでは許容
- 将来 pane 単位の request_id を導入する場合に再検討する

## 7. 想定リスク

- **同時 fetch 競合**:
  - BuyingPower は既存の `buying_power_request_id` で in-flight ガード済み
  - OrderList も `order_list_request_id` を新設して in-flight ガードを追加（**[B5] / 統一決定 1**）。
    `loading=true` の上書きは無害だが、Python 側に冗長 IPC を送る副作用があるため
    Some ガードで抑止する
- **失敗パスの loading 解除漏れ**: 送信失敗 / IpcError / engine 切断 / 再接続 の
  4 経路すべてで loading が解除されること（§5.1 / §5.2 で担保）
- **socket 切断時の永久 loading**: `Message::EngineConnected` ハンドラで全ペイン broadcast
  解除する（**[C1] / 統一決定 3**）。切断検知点でも同等処理
- **`set_error` の巻き添え解除**: §6.3 の通り許容仕様
- **MISSES.md 観点**: pending state の解除漏れは「サイレント沈黙」に該当。
  失敗経路のテストを必ず書く

## 実装ログ

実装日: 2026-05-01

### 完了作業

✅ §3.1 BuyingPowerPanel: `loading: bool` フィールド追加 + `set_loading()` 実装（stale-error クリア込み）
✅ §3.1 BuyingPowerPanel: 全 setter に `loading = false`（`set_cash_buying_power` / `set_credit_buying_power` / `set_replay_portfolio` / `set_error`）
✅ §3.2 BuyingPowerPanel::view: `loading` バッジ「↻ 更新中…」をヘッダーにインライン表示
✅ §3.1 OrdersPanel: `loading: bool` + `last_error: Option<String>` フィールド追加
✅ §3.1 OrdersPanel: `set_loading()` / `set_error()` 新設 + `set_orders()` に `loading=false` / `last_error=None`
✅ §3.2 OrdersPanel::view: `loading` バッジと `last_error` エラー表示を追加
✅ §3.3 dashboard.rs: `distribute_buying_power_loading()` / `distribute_order_list_loading()` / `distribute_order_list_error()` 追加
✅ §3.4.1 main.rs: `order_list_request_id: Option<String>` フィールド新設 + 初期化
✅ §3.4.3 main.rs: `Message::OrderListSendCompleted` / `Message::BuyingPowerSendCompleted` 追加 + ハンドラ実装
✅ §3.4.2 main.rs: 5 箇所の fetch トリガで `distribute_*_loading(true)` → `*_request_id = Some` → `Task::perform` の順序統一
✅ §3.4.4 main.rs: `IpcError` ハンドラに `order_list_request_id` 照合 + `distribute_order_list_error` 呼び出し追加
✅ §3.4.5 main.rs: `EngineConnected` ハンドラで両 request_id クリア + `distribute_*_loading(false)` 呼び出し
✅ §3.4.5 main.rs: `EngineRestarting` ハンドラ（`notify_engine_disconnected` 呼び出し前）で両 request_id クリア + `distribute_*_loading(false)` 呼び出し
✅ §3.5 main.rs: 「取得中…」toast を含む `Toast::info` を 5 箇所すべて廃止・置換
✅ §5.3 `tests/regression_loading_toast_strings.rs` 新規作成（grep リグレッションガード）
✅ `Cargo.toml` dev-dependencies に `regex = "1.11.1"` 追加
✅ `cargo test --workspace` 全件緑
✅ `cargo clippy --workspace -- -D warnings` 警告ゼロ
✅ `cargo fmt --check` pass

### 実装中の知見

- **auto_fetch_orders のガード**: 元の実装は `order_list_request_id` がなく重複ガードなし。新設時に `is_none()` ガードを venue ready / OrderAccepted の両 fetch トリガで追加した。
- **OrderListSendCompleted の closure**: `|res| Message::OrderListSendCompleted(res)` は clippy の `redundant_closure` に引っかかるため `Message::OrderListSendCompleted` に短縮。
- **set_replay_portfolio の loading フィールド**: 計画書 §4 では「全 setter で loading=false」と明記、§6.1 では「streaming push は loading フィールドを触れない」と表現。D4 テストを「start false → still false」の確認として解釈し、`set_replay_portfolio` は `loading = false` をセット（常に安全側に倒す実装）。
- **EngineConnected での main_window 変数**: 元コードは `main_window` 変数を後続ブロックで再宣言しているため、新設した `distribute_*_loading` 呼び出しでは `let main_window = self.main_window.id;` を先頭に移動した。
- **OrderListSendCompleted(Ok(())) パターン**: `match res { Ok(()) => ... }` の `Ok(())` 形式で empty tuple を使う。

### §5.2 目視チェック状況

未実施（debug ビルドでのエンジン接続が必要）。ユーザーに確認を依頼。
