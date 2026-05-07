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

フッターチップのボタン表示は **`class.action()` に従う**。
`Error { .. }` で常に「再ログイン」にするのではなく、既存の `VenueErrorClass` 契約を尊重する。

| VenueState | ドット | 色 | ボタン |
|------------|--------|-----|--------|
| `Idle` | `○` | dim gray | `ログイン` |
| `LoginInFlight` | `⟳` | amber `(0.9, 0.6, 0.1)` | なし |
| `Ready` | `●` | green `(0.2, 0.75, 0.3)` | `再ログイン` |
| `Error { class, .. }` かつ `class.action() == Relogin` | `●` | severity に応じた赤/橙 | `再ログイン` |
| `Error { class, .. }` かつ `class.action() == Dismiss` | `●` | severity に応じた赤/橙 | なし（バナー担当） |
| `Error { class, .. }` かつ `class.action() == Hidden` | `●` | severity に応じた赤/橙 | なし |

### 色の決め方

- `VenueErrorSeverity::Error` → red `(0.9, 0.2, 0.2)`
- `VenueErrorSeverity::Warning` → amber `(0.9, 0.6, 0.1)`
- `Idle` → dim gray（alpha 0.4 の白）
- `LoginInFlight` → amber（ドット文字 `⟳` で代用、アニメーションなし）
- `Ready` → green

---

## 変更一覧

### 0. `engine-client/src/error.rs` — `local_app_down` をコード表に追加

**背景**: Python は `kabu_station` 接続失敗時に `code: "local_app_down"` を emit する
（`server.py:2964`）。現状は `Unknown` フォールバックで `(Error, Hidden)` に落ちるため、
フッターのボタンが消える。kabuステーション本体が起動していない典型的なエラーに
再ログイン導線を提供するため `(Error, Relogin)` でマップする。

```rust
// VenueErrorCode enum に追加
LocalAppDown,

// from_code に追加
"local_app_down" => VenueErrorCode::LocalAppDown,

// classify() に追加
VenueErrorCode::LocalAppDown => VenueErrorClass { severity: Error, action: Relogin },
```

`mode_mismatch`（replay mode での拒否）は `Unknown` → `(Error, Hidden)` のまま許容する。
replay 中はログインボタンを出さないことが正しい挙動。

---

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

`local_app_down` は §0 の追加後に `(Error, Relogin)` へ分類される。
`mode_mismatch` は `Unknown` → `(Error, Hidden)` — replay 中は再ログイン導線を出さない。

---

### 5. `src/main.rs` — `engine_status_stream()`

`EngineConnected` を yield する 2 箇所で `TachibanaVenueEvent(EngineRehello)` の直後に追加:

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
フィールド名・venue 名・ログメッセージを差し替える。

```rust
Message::RequestKabuLogin(trigger) => { /* mirror of RequestTachibanaLogin */ }
Message::KabuLoginIpcResult(result) => { /* mirror of TachibanaLoginIpcResult */ }
Message::KabuVenueEvent(event) => { /* mirror of TachibanaVenueEvent */ }
```

**差分（Tachibana との相違点）**:
- `KabuVenueEvent` ハンドラでは `sidebar.tickers_table.set_tachibana_ready()` は呼ばない
  （kabu はサイドバーのティッカーフィルタを持たない）
- `handles.bump_generation()` は **呼ぶ**
  （kabu Ready でもデータ購読を再起動する必要がある）
- toast メッセージは `kabuステーション…` に読み替え

---

### 8. `src/main.rs` — `restart()`

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

#### フッターレイアウト

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

#### `venue_login_chip` の実装方針

```rust
fn venue_login_chip(
    label: &'static str,
    state: VenueState,
    on_press: Message,
) -> Element<'static, Message> {
    // 1. ドット文字・色を state に応じて決定
    // 2. ボタンの有無:
    //    - Idle          → on_press あり、ラベル「ログイン」
    //    - LoginInFlight → on_press なし
    //    - Ready         → on_press あり、ラベル「再ログイン」
    //    - Error { class, .. } → class.action() == Relogin なら on_press あり
    //                            Dismiss / Hidden          → on_press なし
}
```

---

## Python 側との契約（確認済み）

- `RequestVenueLogin { venue: "kabu_station" }` は既存定義を使用
- Python が emit する error code:
  - `local_app_down` — kabuステーション本体が起動していない（`server.py:2964`）→ `(Error, Relogin)` ※§0 で追加
  - `login_failed`  — 認証失敗（`server.py:2981`）→ 既存 `(Error, Relogin)`
  - `mode_mismatch` — replay mode での拒否（`server.py:3097`）→ `(Error, Hidden)` のまま許容
- Python 挙動:
  - `CONNECTING` 中の重複要求 → `VenueLoginStarted` を再 emit（`server.py:3130`）
    → Rust FSM は `LoginInFlight + LoginStarted = LoginInFlight`（べき等）
  - `CONNECTED` 中の再ログイン → セッションクリア後に新規接続（`server.py:3134`）
    → Rust FSM は `Ready` → `LoginInFlight`（`try_claim_login_in_flight()` が担保）

---

## テスト方針

### `engine-client/src/error.rs`

| テスト | 検証内容 |
|--------|---------|
| `local_app_down_is_error_relogin` | `classify_venue_error("local_app_down")` → `(Error, Relogin)` |
| `local_app_down_typed_matches_str_path` | 既存の `venue_error_code_typed_classify_matches_string_path` テーブルに追加 |

### `src/main.rs` / `src/venue_state.rs` FSM

| テスト | 検証内容 |
|--------|---------|
| `kabu_state_idle_then_login_started` | `KabuVenueEvent(LoginStarted)` → `LoginInFlight` |
| `kabu_state_engine_rehello_resets_to_idle` | `EngineRehello` → `Idle` |
| `kabu_state_ready_from_in_flight` | `LoginInFlight + Ready` → `Ready` |
| `kabu_state_error_local_app_down` | `LoginError { local_app_down }` → `Error { action: Relogin }` |
| `restart_restores_kabu_state_when_cached` | `restart()` + `cached_venue_is_ready` → `Ready` |

### Python バックエンド契約（Rust FSM 視点）

| テスト | 検証内容 |
|--------|---------|
| `duplicate_request_while_in_flight_is_noop` | `try_claim_login_in_flight()` が `LoginInFlight` → `false` を返し FSM 変化なし |
| `relogin_from_ready_transitions_to_in_flight` | `Ready.try_claim_login_in_flight()` → `true`、FSM が `LoginInFlight` |
| `mode_mismatch_error_has_hidden_action` | `classify_venue_error("mode_mismatch")` → `action == Hidden`（再ログイン導線なし） |

### フッター UI

| テスト | 検証内容 |
|--------|---------|
| `footer_chip_idle_has_login_button` | `Idle` → `on_press` 付きボタンが返る |
| `footer_chip_in_flight_no_button` | `LoginInFlight` → `on_press` なし |
| `footer_chip_error_relogin_has_button` | `Error { action: Relogin }` → ボタンあり |
| `footer_chip_error_hidden_no_button` | `Error { action: Hidden }` → ボタンなし |
| `footer_chip_error_dismiss_no_button` | `Error { action: Dismiss }` → ボタンなし（バナー担当） |

---

## サイドバーログインボタンの廃止

フッターに移行したため、サイドバー側の「立花 ログイン」ボタンは削除する。

### 削除対象

| ファイル | 箇所 | 内容 |
|---------|------|------|
| `src/screen/dashboard/tickers_table.rs:778` | `fn tachibana_login_btn()` | メソッドごと削除 |
| `src/screen/dashboard/tickers_table.rs:964` | `column![exchange_filter_btn, tachibana_login_btn()]` | `tachibana_login_btn()` を除去し `exchange_filter_btn` だけにする |
| `tickers_table::Message::RequestTachibanaLogin` | バリアント定義 | 削除（Auto 自動発火は `Action` 直返しのため不要） |
| `tickers_table::update()` line 476–478 | `Message::RequestTachibanaLogin` ハンドラ | 削除 |
| `tickers_table::Action::RequestTachibanaLogin` | — | **残す**（ToggleExchangeFilter Auto 自動発火パスで使用） |
| `sidebar.rs::Action::RequestTachibanaLogin` | — | **残す**（同上、Flowsurface に転送するルート） |

### 残すもの（Auto 発火パス）

`ToggleExchangeFilter(Tachibana)` が `!ready` 時に返す `Action::RequestTachibanaLogin(Trigger::Auto)` は
サイドバーボタンとは独立したパスで、フッター移行後も必要。

```
tickers_table::ToggleExchangeFilter(Tachibana) [while !ready]
  → Action::RequestTachibanaLogin(Auto)        ← 残す
  → sidebar::Action::RequestTachibanaLogin(Auto) ← 残す
  → Flowsurface::Message::RequestTachibanaLogin(Auto) ← 変化なし
```

### 削除するテスト

| テスト | 理由 |
|--------|------|
| `sidebar_login_button_emits_request_venue_login` (`tickers_table.rs:2480`) | ボタンそのものを削除するため |

---

## 変更しないもの

- `src/venue_state.rs` — `VenueState` は既に汎用設計
- `src/widget/venue_banner.rs` — 立花バナーはそのまま（kabu バナーは別フェーズ）

---

## フェーズ境界

このタスクは **UI + Rust FSM 配線 + `local_app_down` コード追加** のみ。
Python 側の `kabusapi_login_flow.py` 完成は前提にしない。
`RequestVenueLogin` を送っても Python がまだ応答しなければ `LoginInFlight` のまま止まるだけ。
