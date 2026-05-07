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

### 1. `src/main.rs` — 定数

```rust
const KABU_STATION_VENUE_NAME: &str = "kabu_station";
```

既存の `TACHIBANA_VENUE_NAME` の直後に追加。

---

### 2. `src/main.rs` — `Flowsurface` フィールド

```rust
tachibana_state: VenueState,   // 既存
kabu_state: VenueState,        // 追加
```

---

### 3. `src/main.rs` — `Message` バリアント

Tachibana の 3 バリアントをそのままミラー:

```rust
KabuVenueEvent(VenueEvent),
RequestKabuLogin(Trigger),
KabuLoginIpcResult(Result<(), String>),
```

---

### 4. `src/main.rs` — `map_engine_event_to_message()`

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

### 5. `src/main.rs` — `engine_status_stream()`

`EngineConnected` を yield する 2 箇所（初回起動・再接続）で
`TachibanaVenueEvent(EngineRehello)` の直後に追加:

```rust
yield Message::TachibanaVenueEvent(VenueEvent::EngineRehello);
yield Message::KabuVenueEvent(VenueEvent::EngineRehello);   // 追加
yield Message::EngineConnected(conn);
```

---

### 6. `src/main.rs` — `Flowsurface::new()` 初期化

```rust
tachibana_state: VenueState::Idle,   // 既存
kabu_state: VenueState::Idle,        // 追加
```

---

### 7. `src/main.rs` — `update()` ハンドラ

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

### 8. `src/main.rs` — `restart()`

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

### 9. `src/main.rs` — `status_bar()` シグネチャ変更

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

### 10. `src/main.rs` — `EngineConnected` ハンドラ

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
