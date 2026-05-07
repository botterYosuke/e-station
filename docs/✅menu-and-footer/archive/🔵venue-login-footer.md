# フッター — 取引所ログイン状態バッジ

サイドバーを開かなくても 立花・kabuステーション のログイン状態を常時確認し、
ボタン 1 クリックでログインできるようにする。

---

## 目標 UI

```
┌──────────────────────────────────────────────────────────────────┐
│  sidebar │               dashboard                                │
├──────────┴───────────────────────────────────────────────────────┤
│ [立花 ○ ログイン]  [kabu ○ ログイン]               ● LIVE       │
└──────────────────────────────────────────────────────────────────┘
```

- 左側: 各取引所のログイン状態バッジ＋ボタン
- 右側: 既存の LIVE/REPLAY モードバッジ（変更なし）
- 高さ・背景色は既存の `STATUS_BAR_HEIGHT` / `STATUS_BAR_BG` を維持

---

## バッジ仕様（取引所ごと）

| VenueState | 表示テキスト | ドット色 | ボタン |
|------------|-------------|---------|--------|
| `Idle` | `立花 ○` | dim gray | `ログイン` |
| `LoginInFlight` | `立花 ⟳` | amber `(0.9, 0.6, 0.1)` | なし |
| `Ready` | `立花 ●` | green `(0.2, 0.75, 0.3)` | `再ログイン` |
| `Error { .. }` | `立花 ●` | red `(0.9, 0.2, 0.2)` | `再ログイン` |

kabu も同じ規則。ラベルは `立花` → `kabu`。

---

## Rust 側変更一覧

### ✅ 1. `src/main.rs` — 定数

```rust
const KABU_STATION_VENUE_NAME: &str = "kabu_station";
```

既存の `TACHIBANA_VENUE_NAME` の直後に追加。

---

### ✅ 2. `src/main.rs` — `Flowsurface` フィールド

```rust
tachibana_state: VenueState,   // 既存
kabu_state: VenueState,        // 追加
```

---

### ✅ 3. `src/main.rs` — `Message` バリアント

Tachibana の 3 バリアントをそのままミラー:

```rust
KabuVenueEvent(VenueEvent),
RequestKabuLogin(Trigger),
KabuLoginIpcResult(Result<(), String>),
```

---

### ✅ 4. `src/main.rs` — `map_engine_event_to_message()`

Tachibana のルーティングに続けて `kabu_station` 用を追加:

```rust
EngineEvent::VenueReady { venue, .. } if venue == KABU_STATION_VENUE_NAME
    => Some(Message::KabuVenueEvent(VenueEvent::Ready)),
EngineEvent::VenueLoginStarted { venue, .. } if venue == KABU_STATION_VENUE_NAME
    => Some(Message::KabuVenueEvent(VenueEvent::LoginStarted)),
EngineEvent::VenueLoginCancelled { venue, .. } if venue == KABU_STATION_VENUE_NAME
    => Some(Message::KabuVenueEvent(VenueEvent::LoginCancelled)),
EngineEvent::VenueError { venue, code, message, .. } if venue == KABU_STATION_VENUE_NAME
    => { let class = classify_venue_error(&code);
         Some(Message::KabuVenueEvent(VenueEvent::LoginError { class, message,
             market_closed: code == "market_closed" })) }
```

---

### ✅ 5. `src/main.rs` — `engine_status_stream()`

`EngineConnected` を yield する 2 箇所（初回起動・再接続）で
`TachibanaVenueEvent(EngineRehello)` の直後に追加:

```rust
yield Message::TachibanaVenueEvent(VenueEvent::EngineRehello);
yield Message::KabuVenueEvent(VenueEvent::EngineRehello);   // 追加
yield Message::EngineConnected(conn);
```

---

### ✅ 6. `src/main.rs` — `Flowsurface::new()` 初期化

```rust
tachibana_state: VenueState::Idle,   // 既存
kabu_state: VenueState::Idle,        // 追加
```

---

### ✅ 7. `src/main.rs` — `update()` ハンドラ

Tachibana の 3 ハンドラをそのままコピーし、
フィールド名・venue 名・ログメッセージだけ差し替える。

```rust
Message::RequestKabuLogin(trigger) => { /* TACHIBANA 版を複製、KABU_STATION_VENUE_NAME を使用 */ }
Message::KabuLoginIpcResult(result) => { /* TACHIBANA 版を複製 */ }
Message::KabuVenueEvent(event) => { /* TACHIBANA 版を複製、sidebar 側の set_tachibana_ready は不要 */ }
```

`KabuVenueEvent` ハンドラでは `set_tachibana_ready` 相当の sidebar 通知は**不要**
（kabu はサイドバーのティッカーテーブルフィルタを持たない）。

`GetBuyingPower` IPC 送信ブロックは**複製しない**（kabu は buying power 非対応）。

`handles.bump_generation()` は Tachibana と同条件（`LoginInFlight` または `Error` → `Ready`）で含める。
kabu の Ready 遷移も market data 再購読のトリガーになるため必要。

---

### ✅ 8. `src/main.rs` — `restart()`

Tachibana の venue_bootstrap と同様に kabu も復元:

```rust
let kabu_bootstrap = if cached_venue_is_ready(KABU_STATION_VENUE_NAME) {
    Task::done(Message::KabuVenueEvent(VenueEvent::Ready))
} else {
    Task::none()
};
close_windows.chain(init_task).chain(venue_bootstrap).chain(kabu_bootstrap)
```

---

### ✅ 9. `src/main.rs` — `status_bar()` シグネチャ変更

```rust
fn status_bar(
    mode_toggle: crate::menu::ModeToggleState,
    tachibana: VenueState,
    kabu: VenueState,
) -> Element<'static, Message>
```

呼び出し元:

```rust
base = base.push(status_bar(mode_toggle, self.tachibana_state.clone(), self.kabu_state.clone()));
```

#### 内部レイアウト

```rust
row![
    venue_login_chip("立花", tachibana, Message::RequestTachibanaLogin(Trigger::Manual)),
    venue_login_chip("kabu", kabu,      Message::RequestKabuLogin(Trigger::Manual)),
    horizontal_space(),
    mode_badge_el,
]
.align_y(Alignment::Center)
.height(STATUS_BAR_HEIGHT)
```

`venue_login_chip` はローカル関数（クロージャ可）:

```rust
fn venue_login_chip(
    label: &'static str,
    state: VenueState,
    on_press: Message,
) -> Element<'static, Message> {
    // ドット文字・色を state に応じて決定
    // ボタンは LoginInFlight のときのみ on_press を渡さない
}
```

---

### ✅ 10. `src/main.rs` — `EngineConnected` ハンドラ

`Message::EngineConnected` の処理末尾（`src/main.rs:2471-2483` 付近）で、
Tachibana の ready 合成ブロックと対称に kabu を追加する。

```rust
let is_kabu_ready_from_manager = self
    .engine_manager
    .as_ref()
    .is_some_and(|m| m.try_is_venue_ready(KABU_STATION_VENUE_NAME));
let is_kabu_ready_from_bridge = cached_venue_is_ready(KABU_STATION_VENUE_NAME);
```

既存の Tachibana ブロック（直接 `return Task::batch(...)` していた部分）を以下のパターンに置き換える:

```rust
// Tachibana の ready 合成ブロックの後に追加
let kabu_synthetic = if (is_kabu_ready_from_manager || is_kabu_ready_from_bridge)
    && !self.kabu_state.is_ready()
{
    Some(Task::done(Message::KabuVenueEvent(VenueEvent::Ready)))
} else {
    None
};

// 既存の Tachibana ブロックを batch に統合
match (tachibana_synthetic, kabu_synthetic) {
    (None, None) => return sidebar_refetch,
    _ => return Task::batch(
        [Some(sidebar_refetch), tachibana_synthetic, kabu_synthetic]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>()
    ),
}
```

両方 ready の場合は 3 要素の `Task::batch` になる。

---

## Python 側との契約

- IPC コマンド `RequestVenueLogin { venue: "kabu_station" }` は既存定義を使用
- Python 側は `kabusapi_login_flow.py` でこのコマンドを受け取り、
  kabuステーション TOKEN API 経由のログインを実行する（実装中）
- ログイン完了後、`VenueReady { venue: "kabu_station" }` を emit する

---

## テスト方針

| テスト | 検証内容 |
|--------|---------|
| `kabu_state_idle_then_login_started` | `KabuVenueEvent(LoginStarted)` で `LoginInFlight` に遷移 |
| `kabu_state_engine_rehello_resets_to_idle` | `EngineRehello` で `Idle` に戻る |
| `kabu_footer_badge_idle_shows_login_button` | `Idle` 時にボタンが on_press を持つ |
| `kabu_footer_badge_in_flight_no_button` | `LoginInFlight` 時にボタンがない |
| `request_kabu_login_sends_ipc` | `RequestKabuLogin(Manual)` が IPC コマンドを送信する |
| `restart_restores_kabu_state_when_cached` | `restart()` 後に `cached_venue_is_ready` が true なら `Ready` に戻る |
| `kabu_login_ipc_send_failure_returns_idle` | IPC 送信失敗（`KabuLoginIpcResult(Err)`）で `LoginInFlight` → `Idle` に戻る |
| `engine_connected_restores_kabu_state_when_cached` | `EngineConnected` 受信時に `cached_venue_is_ready("kabu_station")` が true なら `KabuVenueEvent(Ready)` が発火する |

---

## 変更しないもの

- `src/venue_state.rs` — `VenueState` は既に汎用設計。変更不要。ただしモジュールコメント先頭行を「Venue lifecycle state machine（立花・kabu 共用）」に更新すること。
- `src/screen/dashboard/tickers_table.rs` — kabu はティッカーフィルタを持たない
- `src/widget/venue_banner.rs` — 立花バナーはそのまま（kabu バナーは別フェーズ）

---

## フェーズ境界

このタスクは **UI + Rust FSM 配線のみ**。Python 側の `kabusapi_login_flow.py` 完成は前提にしない。
Rust 側を先行マージしてよい（IPC を送っても Python がまだ応答しなければ `LoginInFlight` のまま止まるだけ）。

---

## 実装ログ

### 2026-05-07 — §1〜§10 完了 (cargo check/clippy/fmt/test すべて緑)

**実装した変更点:**
- `src/venue_state.rs`: モジュールコメントを「立花・kabu 共用」に更新
- `src/main.rs`:
  - §1: `KABU_STATION_VENUE_NAME` 定数は既存で存在していた
  - §2〜§10: 全セクション実装完了
  - `venue_login_chip()` ローカル関数を `status_bar()` の直前に追加
  - `status_bar()` シグネチャを `(state, tachibana, kabu)` の 3 引数に変更

**設計判断・Tips:**

1. **`iced::widget::horizontal_space()` は存在しない** — プロジェクト内では `Space::new().width(Length::Fill)` を使う

2. **`EngineConnected` ハンドラの `match (t, k)` パターンは所有権で詰まる** — `Option<Task<_>>` は Copy でないため `match (tachibana_synthetic, kabu_synthetic)` でアームに移動した後に再利用できない。代わりに `[t, k]` 配列を作り `.any(Option::is_some)` で分岐してから flatten する方式に変更

3. **既存テスト `tt5_status_bar_body_contains_bar_message_pick`** — `fn status_bar(state: crate::menu::ModeToggleState)` を文字列検索していた。引数が複数行に展開されたため、シグネチャ全体を検索するのではなく `fn status_bar(` 位置を起点にブロック内で `ModeToggleState` 検索する方式に更新

4. **`multiinst_replay_pane_routing.rs` の 8000 バイト固定スライス** — main.rs の `─` 等の多バイト UTF-8 文字がオフセット 8000 に当たって panic。`safe_window()` helper で char boundary を探して安全にスライスするよう修正

5. **kabu FSM テスト (テスト 1・2)** — `flowsurface` はバイナリクレートなので統合テストから `VenueState` を直接参照できない。`main.rs` 末尾に `#[cfg(test)] mod kabu_fsm_tests` を追加して内部テストとして実装

6. **`GetBuyingPower` は `KabuVenueEvent` ハンドラに含めない** — 計画書通り。立花と共用の `VenueState` FSM を使うが、kabu は buying power 非対応

7. **`set_tachibana_ready()` は kabu ハンドラで呼ばない** — kabu はサイドバーのティッカーテーブルフィルタを持たない

**最終確認（実装直後 R0）:**
- `cargo check --workspace` ✅
- `cargo clippy --workspace -- -D warnings` ✅
- `cargo fmt --check` ✅
- `cargo test --workspace` ✅（272 unit + 統合テスト全通過）

---

### 2026-05-07 — review-fix-loop R1〜R3 完了

**R1 指摘（HIGH 1 / MEDIUM 2）修正済み:**
- H1: `venue_login_chip` が `LoginInFlight` 時も空テキストウィジェットを生成 → `in_flight` フラグで row_content を条件分岐、ホバースタイルも抑制
- M2: `handler_body_str` がインデント依存（`\n            Message::` 境界検索）→ ブレースカウント方式に変更
- M3: `KabuVenueEvent` の `LoginError` / `EngineRehello` がサイレント（log/Toast なし）→ 明示的アームに `log::warn!` / `log::info!` + `Toast::error` 追加

**R2 指摘（HIGH 1 / MEDIUM 1）修正済み:**
- HIGH-1: Tachibana `EngineRehello` ハンドラに log がない（kabu との非対称）→ `log::info!` 追加
- MEDIUM-2: kabu_footer_badge テストが `\nfn ` 境界依存 → `fn_body_brace()` helper（ブレースカウント）を追加し 3 テストを移行

**R3 指摘（HIGH 1 / MEDIUM 2）修正済み:**
- HIGH: `KabuVenueEvent` ハンドラの `_ => {}` ワイルドカード → `VenueEvent::Dismissed => {}` に明示化
- MEDIUM: `VENUE_NAMES` 配列で `"kabu_station"` ハードコード → `KABU_STATION_VENUE_NAME` 定数を参照に変更
- MEDIUM: `fn_body_brace` と `handler_body_str` にブレーススキャンが重複 → 共通関数 `scan_brace_body()` に抽出

**R3 後最終確認:**
- `cargo check --workspace` ✅
- `cargo clippy --workspace -- -D warnings` ✅
- `cargo fmt --check` ✅
- `cargo test --workspace` ✅（全スイート 0 failed）
- MEDIUM+ 指摘ゼロ → review-fix-loop 収束
