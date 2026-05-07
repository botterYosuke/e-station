# GUI から Live 戦略を起動する UX 実装プラン

## Context

`NautilusRunner.start_live()` / `LiveSession.run()` は実装済みだが、
GUI（Rust / iced）から `StartEngine{engine: "Live"}` を送る経路が存在しない。

導線が塞がれている箇所は 2 段階ある:

1. **入口**: `Action::OpenFile` は replay のときだけ `.py` picker を出す。
   live では JSON（`saved-state.json`）を開く経路が走る。
   ([main.rs L3823](../../src/main.rs#L3823))
2. **ドロップ**: `NativeOpenStrategyPicked` ハンドラは live モードで `.py` を
   意図的に drop する。([main.rs L4031](../../src/main.rs#L4031))

このプランは両方を修正し、GUI から live 戦略を起動・停止できる最小 UX を実装する。

---

## ゴール / 非ゴール

### ゴール

- "File > 開く…" で `.py` を選択 → **LiveStrategyFormModal** に事前入力 → Submit → `StartEngine{engine: "Live"}` 送信
- **`EngineStarted` 受信後、メニューバーに 2 段目を展開**: ▶ / ⏸ ボタン + 現在時刻表示（Replay bar と同じ高さ構造）
- `EngineStopped{strategy_id}` を受信し、実行中の live 戦略に対応するものだけ UI 状態をクリア（2 段目も収納）
- `SecondPasswordRequired` → 既存 `SecondPasswordModal` で自動処理（追加実装不要）
- `LiveBuyingPower` → 既存 `BuyingPowerPanel` / `distribute_live_buying_power()` を再利用（追加実装不要）

### 非ゴール

- 複数 live 戦略の同時実行
- Live pause/resume の Python エンジン側実装（本プランは UI のみ。IPC コマンドは stub 扱い）
- ログイン UI の変更

---

## アーキテクチャ概要

```
"File > 開く..." (live mode)
       │
       │  ← Action::OpenFile に live 分岐を追加（".py" picker）
       ▼
NativeOpenStrategyPicked(Some(path))
       │
       │  ← ドロップガード削除、live 分岐を追加
       ▼
live_strategy_form_modal = Some(LiveStrategyFormModal { strategy_file: path, .. })

ユーザーがフォーム入力 → Submit
       │
       ▼
Command::StartEngine {
    engine: EngineKind::Live,
    strategy_id: "<session-uuid>",   // フォーム submit 時に Uuid::new_v4() で生成
    config: EngineStartConfig {
        instrument_id, strategy_file,
        max_qty: Some(n), max_notional_jpy: Some(m),
        start_date/end_date/initial_cash/granularity: None,
    }
}
       │
       ▼  (server.py が SecondPasswordRequired を emit)
SecondPasswordModal    ← 既存実装がそのまま機能する
       │
       ▼
EngineStarted { strategy_id: "<session-uuid>", ts_event_ms }
       │
       ▼
Message::LiveStrategyStarted { strategy_id, ts_event_ms }
live_strategy_running = true
live_strategy_id = Some("<session-uuid>")   // セッションごとに一意
menu_bar.live_bar.current_time = Some(format_time(ts_event_ms))

メニューバー 2 段目が展開される（bar_height: 32px → 64px）:
┌──────────────────────────────────────────────────────┐
│ ファイル（File）▼                                      │  ← 1 段目（既存）
├──────────────────────────────────────────────────────┤
│  <file_stem>   HH:MM:SS   ⏸   ▶   ■                 │  ← 2 段目（新規）
└──────────────────────────────────────────────────────┘

       │  ⏸ クリック
       ▼
Command::PauseLiveStrategy { strategy_id }   ← stub（engine 側未実装）
live_bar.live_paused = true

       │  ▶ クリック（paused 時）
       ▼
Command::ResumeLiveStrategy { strategy_id }  ← stub（engine 側未実装）
live_bar.live_paused = false

       │  ■ クリック
       ▼
Command::StopEngine { strategy_id: "<session-uuid>" }   // live_strategy_id から取得
       │
       ▼
EngineStopped { strategy_id: "<session-uuid>" }
       │
       │  ← strategy_id が live_strategy_id（UUID）と一致する場合のみ UI をクリア
       ▼
live_strategy_running = false
menu_bar.live_bar をリセット
メニューバー 2 段目が収納される（bar_height: 64px → 32px）
```

---

## Phase 1: `LiveStrategyFormModal` 実装

### 新規ファイル: `src/modal/live_strategy_form.rs`

`replay_form.rs` と同構造。

```rust
pub struct LiveStrategyFormModal {
    pub instrument_id: String,
    pub strategy_file: std::path::PathBuf,
    pub max_qty: String,
    pub max_notional_jpy: String,
    pub validation_error: Option<String>,
    pub submitting: bool,
}

pub enum Message {
    InstrumentChanged(String),
    MaxQtyChanged(String),
    MaxNotionalChanged(String),
    Submit,
    Cancel,
}

pub enum Action {
    Submit {
        instrument_id: String,
        strategy_file: std::path::PathBuf,
        max_qty: u32,
        max_notional_jpy: u64,
    },
    Cancel,
}
```

**`validate()`** の条件:

| フィールド | 検証ルール |
|---|---|
| `instrument_id` | 非空、`"."` を含む（例: `"8306.T"`） |
| `strategy_file` | ファイルが存在し `.py` 拡張子 |
| `max_qty` | `1..=10_000` の整数 |
| `max_notional_jpy` | `1..=100_000_000` の整数（1億円上限） |

**`view()`** — `replay_form.rs` の view と同パターン:

- テキスト: "Live 戦略を起動"
- 銘柄コード入力（`text_input`）
- 戦略ファイル表示（編集不可の灰色テキスト、`strategy_file.file_name()` のみ表示）
- 最大株数入力（`text_input`）
- 最大金額（円）入力（`text_input`）
- エラーバナー（`validation_error` が Some の場合）
- ▶ ライブ実行 ボタン + キャンセルボタン

---

## Phase 2: `main.rs` の状態拡張

### App 状態フィールド追加

```rust
// N4-live: ライブ戦略フォーム modal
live_strategy_form_modal: Option<modal::live_strategy_form::LiveStrategyFormModal>,
// N4-live: ライブ戦略実行中フラグ（true = EngineStarted 受信済み）
live_strategy_running: bool,
// N4-live: 実行中の strategy_id（StopEngine 送信・EngineStopped 突き合わせに使う）
live_strategy_id: Option<String>,
```

`live_strategy_file_stem` と `live_bar.current_time` は `menu_bar.live_bar`
（`LiveBarState`）に持たせるため、`App` 直下には置かない（Phase 9 参照）。

初期値:
```rust
live_strategy_form_modal: None,
live_strategy_running: false,
live_strategy_id: None,
```

### Message 追加

```rust
/// N4-live: live strategy フォーム modal 内部メッセージ。
LiveStrategyFormMsg(modal::live_strategy_form::Message),
/// N4-live: live EngineStarted を受信した。
LiveStrategyStarted { strategy_id: String, ts_event_ms: i64 },
/// N4-live: live 戦略の EngineStopped を受信した（突き合わせ前）。
LiveEngineStoppedEvent { strategy_id: String },
/// N4-live: ■ ボタンから StopEngine を送信する。
StopLiveStrategy,
```

---

## Phase 3: `Action::OpenFile` に live 分岐を追加

現行の `Action::OpenFile` ハンドラ ([main.rs L3823](../../src/main.rs#L3823)) は
replay のときだけ `.py` picker を出す。live のときは JSON picker へ fallthrough する。

**After** — live 側に `.py` picker 分岐を先に置く:

```rust
Action::OpenFile => {
    if app_mode() == engine_client::dto::AppMode::Live {
        // live mode: .py 戦略ファイルを選択して LiveStrategyFormModal に渡す
        return Task::perform(
            async {
                rfd::AsyncFileDialog::new()
                    .add_filter("Python", &["py"])
                    .set_title("戦略ファイルを開く")
                    .pick_file()
                    .await
                    .map(|h| h.path().to_owned())
            },
            Message::NativeOpenStrategyPicked,
        );
    }
    // replay mode: 既存の .py picker → LoadStrategyScenario 経路
    if app_mode() == engine_client::dto::AppMode::Replay {
        // ... 既存コードそのまま ...
    }
    // live で .py 以外 (JSON) を開く経路は廃止 — live は戦略ファイルのみ
}
```

> **注意**: live モードの JSON（`saved-state.json`）を開く経路は、
> このプランでは `.py` picker に一本化する。
> saved-state を手動で読む操作は別途 "File > 設定を開く" として分離するか、
> 後続プランで検討する。

---

## Phase 4: `NativeOpenStrategyPicked` ドロップガード削除と live 分岐追加

### 変更箇所: `main.rs` — `Message::NativeOpenStrategyPicked` ハンドラ

**Before** ([main.rs L4031](../../src/main.rs#L4031)):
```rust
// モードガード: live で誤って `.py` が選ばれても無視する。
if app_mode() != engine_client::dto::AppMode::Replay {
    log::warn!("NativeOpenStrategyPicked received outside replay mode — dropping");
    return Task::none();
}
```

**After** — モードで分岐する:
```rust
if app_mode() == engine_client::dto::AppMode::Live {
    // live mode: フォーム modal を開く
    let form = modal::live_strategy_form::LiveStrategyFormModal {
        strategy_file: path,
        ..Default::default()
    };
    self.live_strategy_form_modal = Some(form);
    return Task::none();
}
// replay mode: 既存の LoadStrategyScenario 経路へ続く
```

---

## Phase 5: `EngineStarted` の live 分岐追加

`map_engine_event()` 内（[main.rs 近傍](../../src/main.rs#L1580)）:

```rust
EngineEvent::EngineStarted { strategy_id, ts_event_ms, .. } => {
    let is_live = app_mode() == engine_client::dto::AppMode::Live;
    if is_live {
        Some(Message::LiveStrategyStarted { strategy_id, ts_event_ms })
    } else {
        None  // replay は ReplayDataLoaded で処理済み
    }
}
```

### `Message::LiveStrategyStarted` ハンドラ

```rust
Message::LiveStrategyStarted { strategy_id, ts_event_ms } => {
    self.live_strategy_running = true;
    self.live_strategy_id = Some(strategy_id);
    // live_bar の current_time を EngineStarted のタイムスタンプで初期化
    self.menu_bar.live_bar.current_time = Some(format_live_time(ts_event_ms));
    self.menu_bar.live_bar.live_paused = false;
    Task::none()
}
```

---

## Phase 6: `EngineStopped` — strategy_id 突き合わせで live 戦略停止を検知

> **背景**: 現行コードは live モードの `EngineStopped` に対して `None`（no-op）を返す。
> コメントには「live の EngineStopped は replay 完了ではなく engine restart を意味する」
> とある。([main.rs L1691](../../src/main.rs#L1691))
>
> 一律 `LiveStrategyStopped` に変えると、mode switch や engine 再接続の停止イベントでも
> UI 状態を誤ってクリアするリスクがある。
>
> **解決策**: Phase 7 で StartEngine を送信する際に `strategy_id` を
> `uuid::Uuid::new_v4().to_string()` で生成する。
> セッションごとに異なる UUID を使うことで、mode switch / engine restart 由来の
> `EngineStopped` は常に UUID 不一致となり、UI をクリアしない。
> `EngineStopped.strategy_id` と `self.live_strategy_id`（UUID）を突き合わせ、
> 一致する場合のみ UI をクリアする。
>
> **前提条件**: Python engine は `EngineStarted` / `EngineStopped` に
> 受信した `strategy_id` をそのままエコーバックする（既存動作の確認が要る）。

`map_engine_event()` 内:

```rust
EngineEvent::EngineStopped { strategy_id, .. } => {
    let is_replay = app_mode() == engine_client::dto::AppMode::Replay;
    if is_replay {
        Some(Message::ReplayFinished)
    } else {
        // live: strategy_id を持ち越し、ハンドラ側で突き合わせる
        Some(Message::LiveEngineStoppedEvent { strategy_id })
    }
}
```

### `Message::LiveEngineStoppedEvent` ハンドラ

```rust
Message::LiveEngineStoppedEvent { strategy_id } => {
    // 実行中の live 戦略の strategy_id と一致するときだけ UI をクリアする。
    // engine restart / mode switch 由来の停止イベントには反応しない。
    if self.live_strategy_running
        && self.live_strategy_id.as_deref() == Some(strategy_id.as_str())
    {
        self.live_strategy_running = false;
        self.live_strategy_id = None;
        // live_bar をリセット → メニューバー 2 段目が収納される
        self.menu_bar.live_bar = LiveBarState::default();
    }
    Task::none()
}
```

---

## Phase 7: `LiveStrategyFormMsg` ハンドラ + `StartEngine` 送信

```rust
Message::LiveStrategyFormMsg(msg) => {
    if let Some(form) = &mut self.live_strategy_form_modal {
        match form.update(msg) {
            Some(modal::live_strategy_form::Action::Submit {
                instrument_id,
                strategy_file,
                max_qty,
                max_notional_jpy,
            }) => {
                // セッション固有 UUID — EngineStopped との突き合わせに使う
                let session_id = uuid::Uuid::new_v4().to_string();
                self.menu_bar.live_bar.strategy_file_stem = strategy_file
                    .file_stem()
                    .map(|s| s.to_string_lossy().into_owned());
                self.live_strategy_form_modal = None;

                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    let strategy_file_str = strategy_file.to_string_lossy().into_owned();
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::StartEngine {
                                request_id: uuid::Uuid::new_v4().to_string(),
                                engine: engine_client::dto::EngineKind::Live,
                                strategy_id: session_id,   // UUID（セッションごとに生成）
                                config: engine_client::dto::EngineStartConfig {
                                    instrument_id: Some(instrument_id),
                                    instrument_ids: None,
                                    strategy_file: Some(strategy_file_str),
                                    strategy_init_kwargs: None,
                                    max_qty: Some(max_qty),
                                    max_notional_jpy: Some(max_notional_jpy as u64),
                                    start_date: None,
                                    end_date: None,
                                    initial_cash: None,
                                    granularity: None,
                                },
                            }).await.map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::Noop,
                            Err(e) => Message::OrderToast(Toast::error(format!(
                                "Live 起動失敗: {e}"
                            ))),
                        },
                    );
                }
            }
            Some(modal::live_strategy_form::Action::Cancel) => {
                self.live_strategy_form_modal = None;
            }
            None => {}
        }
    }
    Task::none()
}
```

---

## Phase 8: `StopLiveStrategy` ハンドラ

```rust
Message::StopLiveStrategy => {
    if !self.live_strategy_running {
        return Task::none();
    }
    let strategy_id = self.live_strategy_id.clone().unwrap_or_default();
    let Some(conn) = self.engine_connection.as_ref().cloned() else {
        return Task::none();
    };
    Task::perform(
        async move {
            conn.send(engine_client::dto::Command::StopEngine {
                request_id: uuid::Uuid::new_v4().to_string(),
                strategy_id,
            }).await.map_err(|e| e.to_string())
        },
        |res| match res {
            Ok(()) => Message::Noop,
            Err(e) => Message::OrderToast(Toast::error(format!("Live 停止失敗: {e}"))),
        },
    )
}
```

---

## Phase 9: メニューバー — `LiveBarState` + 2 段目レイアウト

> **ビルド依存注意**: Phase 9 は 2 段階に分割する。
>
> - **Phase 9a** (`menu_bar_state.rs` のみ): `LiveBarState` struct・`live_bar` フィールド・`BarMessage::LivePress*` 追加。  
>   main.rs の呼び出し箇所を変更しないため、単独でコンパイルが通る。**Phase 1 と同様に先行着手可能**。
> - **Phase 9b** (`widget_menu_bar.rs`): `bar_height()` / `view()` シグネチャ変更・`live_control_row()` 追加。  
>   シグネチャ変更により main.rs の全呼び出し箇所が一斉にコンパイルエラーになる。  
>   **Phase 2 完了後、Phase 9c（main.rs 呼び出し箇所の更新）と同一コミットで実施すること**。

### `menu_bar_state.rs` — `LiveBarState` 追加

```rust
/// Live 戦略実行中の 2 段目バー状態。
/// `live_strategy_running == true` のときのみ意味を持つ。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LiveBarState {
    /// 実行中の戦略ファイル名（stem のみ）。メニューバー表示用。
    pub strategy_file_stem: Option<String>,
    /// 現在時刻文字列（"HH:MM:SS" 形式）。IPC タイムスタンプから更新。
    pub current_time: Option<String>,
    /// true = 一時停止中。⏸/▶ のアクティブ状態に使う。
    pub live_paused: bool,
}
```

`State` にフィールド追加:
```rust
pub struct State {
    pub open: Option<TopMenu>,
    pub replay_bar: ReplayBarState,
    pub live_bar: LiveBarState,   // 追加
}
```

`BarMessage` に追加:
```rust
// ── Live bar button actions ───────────────────────────────────────────────────
LivePressPlay,   // ▶（paused → running）
LivePressPause,  // ⏸（running → paused）
LivePressStop,   // ■（StopEngine を発行）
```

`update()` に追加（state は変化しない — `main.rs` がディスパッチする）:
```rust
BarMessage::LivePressPlay
| BarMessage::LivePressPause
| BarMessage::LivePressStop => state,
```

### `widget_menu_bar.rs` — `bar_height()` 修正

live 戦略実行中は 2 段目を追加するため、`live_strategy_running` を引数に受け取る:

```rust
pub fn bar_height(mode: AppMode, live_strategy_running: bool) -> f32 {
    match mode {
        AppMode::Replay => ROW_HEIGHT * 2.0,
        AppMode::Live if live_strategy_running => ROW_HEIGHT * 2.0,
        AppMode::Live => ROW_HEIGHT,
    }
}
```

> `bar_height()` の呼び出し箇所は `main.rs` 内に複数ある（`with_dropdown_overlay` の
> `top_offset` 計算を含む）。全呼び出しで `live_strategy_running` を渡すよう更新する。

### `widget_menu_bar.rs` — `view()` シグネチャ拡張

```rust
pub fn view<'a>(
    state: &'a State,
    mode: AppMode,
    replay_running: bool,
    replay_paused: bool,
    mode_switch_in_progress: bool,
    live_strategy_running: bool,  // 追加
) -> Element<'a, BarMessage>
```

`view()` の分岐を拡張:

```rust
if mode == AppMode::Replay {
    // 既存: replay 2 段目
    let col = column![
        mouse_area(top_row_container).on_press(BarMessage::Dismiss),
        replay_input_row(&state.replay_bar, ctrl),
    ];
    container(col)
        .height(Length::Fixed(bar_height(AppMode::Replay, false)))
        .width(Length::Fill)
        .into()
} else if live_strategy_running {
    // live 戦略実行中: 2 段目を展開
    let col = column![
        mouse_area(top_row_container).on_press(BarMessage::Dismiss),
        live_control_row(&state.live_bar),
    ];
    container(col)
        .height(Length::Fixed(bar_height(AppMode::Live, true)))
        .width(Length::Fill)
        .into()
} else {
    mouse_area(top_row_container).into()
}
```

### `widget_menu_bar.rs` — `live_control_row()` 新規追加

Replay の `replay_input_row()` に相当する関数:

```rust
fn live_control_row<'a>(bar: &'a LiveBarState) -> Element<'a, BarMessage> {
    let file_label = bar
        .strategy_file_stem
        .as_deref()
        .unwrap_or("--")
        .to_string();

    let time_display = text(bar.current_time.as_deref().unwrap_or("--:--:--").to_string());

    // ▶ : paused のときだけ有効
    let play_btn = {
        let b = button(text("▶")).style(button::primary);
        if bar.live_paused {
            b.on_press(BarMessage::LivePressPlay)
        } else {
            b  // 実行中は無効（視覚的にグレー）
        }
    };

    // ⏸ : 実行中（非 paused）のときだけ有効
    let pause_btn = {
        let b = button(text("⏸")).style(button::secondary);
        if !bar.live_paused {
            b.on_press(BarMessage::LivePressPause)
        } else {
            b
        }
    };

    // ■ : 常に有効
    let stop_btn = button(text("■"))
        .on_press(BarMessage::LivePressStop)
        .style(button::danger);

    row![
        text(file_label),
        time_display,
        Space::new().width(Length::Fill).height(Length::Shrink),
        pause_btn,
        play_btn,
        stop_btn,
    ]
    .spacing(4)
    .height(Length::Fixed(ROW_HEIGHT))
    .into()
}
```

### `main.rs` — `BarMessage::LivePress*` のディスパッチ追加

```rust
BarMessage::LivePressStop => {
    // StopLiveStrategy と同じ経路
    return Task::done(Message::StopLiveStrategy);
}
BarMessage::LivePressPause => {
    self.menu_bar.live_bar.live_paused = true;
    // TODO: Command::PauseLiveStrategy stub（engine 側未実装）
    Task::none()
}
BarMessage::LivePressPlay => {
    self.menu_bar.live_bar.live_paused = false;
    // TODO: Command::ResumeLiveStrategy stub（engine 側未実装）
    Task::none()
}
```

### 現在時刻の更新

`Message::LiveBuyingPowerUpdated` ハンドラと `Message::LiveStrategyStarted` ハンドラで
`menu_bar.live_bar.current_time` を更新する:

```rust
// 既存の LiveBuyingPowerUpdated ハンドラに 1 行追加
Message::LiveBuyingPowerUpdated { cash, equity, ts_event_ms } => {
    self.menu_bar.live_bar.current_time = Some(format_live_time(ts_event_ms));
    // ... 既存の distribute_live_buying_power() 呼び出し ...
}
```

`format_live_time()` — `ts_event_ms`（Unix ミリ秒）を JST "HH:MM:SS" に変換する
ヘルパー関数。`chrono` で実装（すでにワークスペース依存に入っているか確認）:

```rust
fn format_live_time(ts_ms: i64) -> String {
    use chrono::{TimeZone, Utc};
    let dt = Utc.timestamp_millis_opt(ts_ms).single()
        .unwrap_or_else(Utc::now);
    // JST = UTC+9
    let jst = dt + chrono::Duration::hours(9);
    jst.format("%H:%M:%S").to_string()
}
```

### フォーム submit 時に `live_bar.strategy_file_stem` を保存

Phase 7 のコードに含まれる（`self.menu_bar.live_bar.strategy_file_stem = ...`）。
`App` 直下に別フィールドを作らないこと（Phase 2 参照）。

`main.rs` — `BarMessage::LivePressStop` → `Message::StopLiveStrategy` にマップ。

---

## Phase 10: `view()` — modal レイヤー追加

`second_password_modal` → `replay_form_modal` → `live_strategy_form_modal` の順で重ねる:

```rust
// 既存:
let after_second_password = if let Some(modal) = &self.second_password_modal { ... };
let after_replay_form = if let Some(form) = &self.replay_form_modal { ... };
// 追加:
let after_live_form = if let Some(form) = &self.live_strategy_form_modal {
    let form_view = form.view().map(Message::LiveStrategyFormMsg);
    main_dialog_modal(
        after_replay_form,
        form_view,
        Message::LiveStrategyFormMsg(modal::live_strategy_form::Message::Cancel),
    )
} else {
    after_replay_form
};
// 以降は after_live_form を使う
```

---

## Phase 11: `LiveBuyingPower` — 既存実装を再利用（`clear_live_strategy_portfolio()` 追加のみ）

以下は **既に実装済み** であり、本プランで変更は不要。

| 実装済み要素 | 場所 |
|---|---|
| `BuyingPowerPanel.live_strategy_cash/equity/ts_ms` フィールド | [buying_power.rs L30](../../src/screen/dashboard/panel/buying_power.rs#L30) |
| `set_live_strategy_portfolio()` メソッド | [buying_power.rs L91](../../src/screen/dashboard/panel/buying_power.rs#L91) |
| `Dashboard::distribute_live_buying_power()` | [dashboard.rs L820](../../src/screen/dashboard.rs#L820) |
| `Message::LiveBuyingPowerUpdated` → `distribute_live_buying_power()` | [main.rs L3327](../../src/main.rs#L3327) |

本プランで **唯一追加が必要な実装**:

```rust
// buying_power.rs に clear メソッドを追加（フィールドを None に戻すだけ）
pub fn clear_live_strategy_portfolio(&mut self) {
    self.live_strategy_cash = None;
    self.live_strategy_equity = None;
    self.live_strategy_ts_ms = None;
}
```

`Dashboard` 側に全パネルへ clear を配布するメソッドを追加し、
`LiveEngineStoppedEvent` ハンドラで UI クリア時に呼ぶ:

```rust
// dashboard.rs に追加
pub fn clear_live_strategy_portfolio(&mut self, main_window: window::Id) {
    for panel in &mut self.panels_for(main_window) {
        panel.buying_power.clear_live_strategy_portfolio();
    }
}

// main.rs — LiveEngineStoppedEvent ハンドラの UI クリアブロックに追加
self.active_dashboard_mut()
    .clear_live_strategy_portfolio(main_window);
```

> **注意**: `distribute_live_buying_power(..., String::new(), String::new(), 0)` は
> 空文字の余力値をセットするだけであり、clear（None 化）ではない。
> 必ず `clear_live_strategy_portfolio()` 経路を使うこと。

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `src/modal/live_strategy_form.rs` | **新規**: `LiveStrategyFormModal` / `Message` / `Action` / `view()` / `validate()` |
| `src/modal/mod.rs` | `pub mod live_strategy_form;` 追加 |
| `src/menu_bar_state.rs` | `LiveBarState` 構造体新規追加、`State.live_bar` フィールド追加、`BarMessage::LivePressPlay/LivePressPause/LivePressStop` 追加、`update()` 拡張 |
| `src/widget_menu_bar.rs` | `bar_height()` に `live_strategy_running: bool` 引数追加、`view()` に `live_strategy_running: bool` 引数追加、live 2 段目分岐追加、`live_control_row()` 新規追加 |
| `src/main.rs` | 状態フィールド 3 個（`live_strategy_form_modal` / `live_strategy_running` / `live_strategy_id`）追加、Message 4 個（`LiveStrategyFormMsg` / `LiveStrategyStarted` / `LiveEngineStoppedEvent` / `StopLiveStrategy`）追加、各ハンドラ追加、`Action::OpenFile` live 分岐追加、`NativeOpenStrategyPicked` live 分岐修正、`EngineStarted`/`EngineStopped` live 分岐追加、`LiveBuyingPowerUpdated` ハンドラに current_time 更新を追加、`bar_height()` 呼び出し全箇所に `live_strategy_running` 渡すよう修正、`view()` modal スタック追加 |
| `src/screen/dashboard/panel/buying_power.rs` | `clear_live_strategy_portfolio()` 追加（フィールドを None に戻す専用メソッド） |
| `src/screen/dashboard.rs` | `clear_live_strategy_portfolio()` 追加（全パネルへ配布）、`LiveEngineStoppedEvent` UI クリアで呼び出す |

---

## テスト戦略

### Unit テスト（`#[cfg(test)]` in `src/modal/live_strategy_form.rs`）

| テスト ID | 内容 |
|---|---|
| LG-1 | `validate()`: 全フィールド正常 → `Ok(ValidatedForm)` |
| LG-2 | `validate()`: `instrument_id` に `.` なし → `Err` |
| LG-3 | `validate()`: `max_qty = "0"` → `Err` |
| LG-4 | `validate()`: `max_notional_jpy = "abc"` → `Err` |
| LG-5 | `validate()`: `strategy_file` 拡張子が `.rs` → `Err` |

### Source-inspection テスト（`#[test]` in `main.rs` 末尾）

#### 更新が必要な既存テスト

| 既存テスト | 変更内容 |
|---|---|
| `open_file_replay_mode_uses_py_filter` | live 分岐が先に `.py` picker を出すことを保証するアサーションを追加（または live 専用の新テストに分離） |
| `open_strategy_picked_some_sends_load_strategy_scenario` | "live must drop the .py" アサートを削除し、「live 分岐が `live_strategy_form_modal` を設定する」アサートに置き換える |

#### 新規テスト（`main.rs` source-inspection）

| テスト ID | 内容 |
|---|---|
| LG-10 | `Action::OpenFile` ハンドラに `AppMode::Live` 分岐があり `.py` filter を使う |
| LG-11 | `NativeOpenStrategyPicked` live 分岐が `live_strategy_form_modal = Some(...)` を設定する |
| LG-12 | `EngineStarted` が live モードで `Message::LiveStrategyStarted` を生成する |
| LG-13 | `EngineStopped` が live モードで `Message::LiveEngineStoppedEvent` を生成する |
| LG-14 | `LiveEngineStoppedEvent` ハンドラで strategy_id 不一致時は `live_strategy_running` が変化しない |
| LG-15 | `StopLiveStrategy` ハンドラが `Command::StopEngine` を送信するコードを含む |
| LG-16 | `python/engine/server.py`（または `engine_runner.py`）が `StartEngine` の `strategy_id` を `EngineStarted` / `EngineStopped` イベントにそのままエコーバックすることをソース上で確認する（Phase 6 前提条件の検証）|

#### 新規テスト（`menu_bar_state.rs` unit tests）

| テスト ID | 内容 |
|---|---|
| LG-20 | `LivePressPlay` / `LivePressPause` / `LivePressStop` が `update()` で state を変化させない（main.rs がディスパッチするため）|
| LG-21 | `State::default()` の `live_bar` が `LiveBarState::default()` と等しい |

#### `bar_height()` のテスト（`widget_menu_bar.rs` または `menu_bar_state.rs`）

| テスト ID | 内容 |
|---|---|
| LG-30 | `bar_height(AppMode::Live, false)` == `ROW_HEIGHT` |
| LG-31 | `bar_height(AppMode::Live, true)` == `ROW_HEIGHT * 2.0` |
| LG-32 | `bar_height(AppMode::Replay, false)` == `ROW_HEIGHT * 2.0`（既存動作の回帰保証） |

---

## 受け入れ条件

1. live モードで "File > 開く…" → `.py` の OS ファイルダイアログが開く
2. ファイル選択後、`LiveStrategyFormModal` が開き `strategy_file` 欄に選択パスが表示される
3. フォームに `instrument_id` / `max_qty` / `max_notional_jpy` を入力して Submit → `StartEngine{engine: "Live"}` が engine に届く
4. `SecondPasswordRequired` が来たら `SecondPasswordModal` が自動で現れる（既存動作）
5. `EngineStarted` 受信後、メニューバーが 2 段になり、2 段目に `<file_stem>` / `HH:MM:SS` / ⏸ / ▶ / ■ が表示される
6. `LiveBuyingPower` 受信のたびに 2 段目の時刻表示が更新される
7. ⏸ 押下 → ▶ がアクティブになり ⏸ がグレーになる（UI のみ、engine 側は stub）
8. ■ 押下 → `StopEngine` 送信 → `EngineStopped{strategy_id: "<session-uuid>"}` 受信（UUID が `live_strategy_id` と一致する場合のみ）→ 2 段目が収納される
9. engine restart / mode switch 由来の `EngineStopped` では 2 段目が消えない
10. `LiveBuyingPower` イベント受信 → `BuyingPowerPanel` に余力が表示される（既存動作）
11. `cargo test` が通る、`cargo clippy -- -D warnings` が無警告

---

## 実装順序と依存関係

```
【独立先行可能】
Phase 1  (LiveStrategyFormModal 新規)
Phase 9a (LiveBarState / BarMessage 追加 — menu_bar_state.rs のみ)
Phase 11 (BuyingPower clear_live_strategy_portfolio)

【Phase 2 以降、順次実施】
Phase 2 (main.rs 状態追加)
    ├─ Phase 3  (Action::OpenFile 修正)
    ├─ Phase 4  (NativeOpenStrategyPicked 修正)
    ├─ Phase 5  (EngineStarted live 分岐 → live_bar 初期化)
    ├─ Phase 6  (EngineStopped → LiveEngineStoppedEvent → live_bar リセット)
    ├─ Phase 7  (LiveStrategyFormMsg / StartEngine 送信 → live_bar.strategy_file_stem 保存)
    ├─ Phase 8  (StopLiveStrategy)
    ├─ Phase 9b+9c (widget_menu_bar.rs シグネチャ変更 + main.rs 呼び出し箇所更新 — 同一コミット必須)
    └─ Phase 10 (view() modal スタック)
```

> **Phase 9b と Phase 9c は必ず同一コミット**: `bar_height()` / `view()` のシグネチャ変更だけを
> 先にコミットするとビルドが壊れる。main.rs の全呼び出し箇所の更新（9c）と同時に入れること。

**推奨着手順**: Phase 1 → Phase 9a（`menu_bar_state.rs`）→ Phase 2 → Phase 3〜11 並行（Phase 9b+9c は Phase 9a 完了後、Phase 2 以降と同時）。
