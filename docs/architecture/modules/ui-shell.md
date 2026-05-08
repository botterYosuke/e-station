---
title: UI Shell — メニューバー / フッター / Replay コントロール
status: migrated
migrated_from:
  - docs/✅menu-and-footer/README.md
  - docs/✅menu-and-footer/menu-bar.md
  - docs/✅menu-and-footer/replay-control.md
  - docs/✅menu-and-footer/footer.md
  - docs/✅menu-and-footer/mode-switch.md
  - docs/✅menu-and-footer/file-menu.md
  - docs/✅menu-and-footer/venue-login-footer.md
source_commit: 5215a03
---

# UI Shell — メニューバー / フッター / Replay コントロール

メインウィンドウの **メニューバー**・**ステータスバー（フッター）**・**File メニュー**・
**モード切替**・**Replay 再生制御** の UI 構造とコンポーネント境界を定義する。
操作手順（end-user 向けマニュアル）は GitHub Wiki に分離されているため、本ドキュメントは
責務・状態遷移・他コンポーネントとの境界に絞る。

実装は `sasa/spicy-gosling` ブランチで完結済み。

---

## 全体レイアウト

!menu_layout

| モード | メニューバー高さ | 下段表示 | ステータスバー色 |
|--------|----------------|----------|----------------|
| Live   | 32 px          | なし     | 緑 `(0.2, 0.75, 0.3)` |
| Replay | 64 px          | あり     | アンバー `(0.9, 0.6, 0.1)` |

---

## コンポーネント一覧と責務分担

| コンポーネント | 主要モジュール | 責務 |
|--------------|--------------|------|
| Widget メニューバー | `src/widget_menu_bar.rs` / `src/menu_bar_state.rs` | iced widget による単一実装メニューバー（全 OS 共通） |
| Replay コントロールバー | `src/widget_menu_bar.rs`（段 2 部分） | 再生制御ボタン群と入力欄（Replay モードのみ） |
| ステータスバー（フッター） | `src/main.rs::status_bar` | モードバッジ + 取引所ログイン状態バッジ |
| File メニュー | `src/menu.rs::actions_for_mode` | Open / Save / SaveAs / Quit と CURRENT_PATH 管理 |
| モード切替 | `src/menu.rs::SwitchAppMode` / `src/main.rs::SwitchMode` ハンドラ | Live ⇄ Replay の engine 再起動 |
| Venue ログインバッジ | `src/main.rs::venue_login_chip` | フッター左側の取引所バッジ + ログインボタン |

---

## Widget メニューバー

muda などの OS ネイティブメニューは完全廃止。全 OS（Windows / macOS / Linux）で
**iced widget の単一実装**を使用する。

### モジュール構成

```
src/menu.rs             — Action enum / actions_for_mode / mode_toggle_state
src/menu_bar_state.rs   — TopMenu / BarMessage / ReplayBarState / update()
src/widget_menu_bar.rs  — view() / with_dropdown_overlay()
src/native_menu.rs      — widget_keyboard_subscription()（accelerator のみ）
```

dispatch 経路は **`Message::NativeMenuAction(Action)` の単一系統**。
`to_native_action()` でドロップダウン選択を正規化する。

### 不変条件

- `actions_for_mode` / `mode_toggle_state` は `src/menu.rs` に集約。プラットフォームを問わず同じ集合を返す。
- ファイル全体に `#[cfg(target_os = ...)]` ゲートはなし。全 OS 共通でビルドされる。
- `native_menu::attach()` / `refresh_tools_enable()` は no-op（互換のため残置）。
- `mode_menu_items` は廃止済み。

### レイアウト定数

| 要素 | 値 | 備考 |
|------|----|------|
| ボタン幅 | `BTN_WIDTH = 155.0` | `ファイル（File）▼` |
| 段 1 高さ | `bar_height(Live) = 32.0` | 常時表示 |
| 段 2 高さ | `bar_height(Replay) = 64.0` | Replay モード時のみ |
| dropdown anchor | `with_dropdown_overlay()` の `top_offset` に `bar_height(mode)` を渡す | 動的 Y アンカー廃止済み |

`view()` / `with_dropdown_overlay()` の `mode: AppMode` 引数は**値渡し**（`Copy` 型）。
一時値への参照によるライフタイムエラーを避けるため。

### 状態モデル

```rust
pub enum TopMenu { File }

pub enum BarMessage {
    Toggle(TopMenu),    // ドロップダウン開閉
    Pick(Action),       // 項目選択
    Dismiss,            // Esc / 外側クリック
    DismissFocusLost,   // ウィンドウ unfocus
}
```

`update()` は純関数。`tests/widget_menu_bar_state.rs` が cfg gate なしで全 OS から実行可能。
`Dismiss` と `DismissFocusLost` は分離（ログ理由を区別する）。

### アクセラレータ

`src/native_menu.rs::widget_keyboard_subscription()` を全 OS で登録。
**`physical_key`（物理キー位置）** でマッチするためレイアウト非依存（JIS / AZERTY / Dvorak 等で安定）。

| OS | 修飾キー |
|----|----------|
| Windows / Linux | Ctrl のみ |
| macOS | Ctrl または Cmd（logo） |

macOS のみ `modifiers.logo()`（Cmd）を受理。Win/Linux で受理すると WM（Win+Q 等）と衝突する。

#### モード別ガード

- `is_live` は `Subscription::with(is_live)` 経由で非キャプチャ渡し。
- Replay モードでは `is_live = false` となり、Open / Save / SaveAs は発火しない。
- `Quit` はモード非依存。
- `SwitchMode` は `crate::MODE_SWITCHING.load(Acquire)` で再入を抑制。

### 既知の制限

- **DPI スケーリング**: `BAR_HEIGHT = 32.0` / 64px 2 段組が高 DPI Windows で崩れないかは実機未確認。
- **macOS 見た目**: in-window メニューバー（スクリーン最上段ではない）。iced widget の制約による。
- **物理キー matching の盲点**: 一部ノート PC の特殊キーが `Physical::Unidentified` を返す場合はマッチしない。
- **`src/native_menu.rs` の名称**: muda 時代の歴史的経緯。中身は keyboard subscription のみ。

---

## Replay コントロールバー

メニューバーを **2 段構成** に拡張し、replay 再生制御（▶ / ⏸ / ⏭ / ⏮ / ⏹）・
現在情報表示・入力欄を常設する。モーダルダイアログ往復を排除した。

### レイアウト

```
┌─────────────────────────────────────────────────────────────────────┐
│ 段 1 (32px) │ ファイル（File）▼ │ 戦略: foo.py  Current: 2025-03-15│
│ (常時)      │                   │  ▶ 再生  ⏸ 停止  ⏭ Step+  ⏮ Step-  ⏹│
├─────────────────────────────────────────────────────────────────────┤
│ 段 2 (32px) │ 銘柄: [1301.TSE ] │ 開始: [2025-01-06] 終了: [2025-03-31]│
│ (Replay のみ)│ 粒度: [Daily ▼] │ 初期資金: [1000000]               │
└─────────────────────────────────────────────────────────────────────┘
```

- **Live モード**: 段 1 のみ（32px）。再生制御ボタンは非表示。
- **Replay モード**: 段 1 + 段 2（64px）。

### 状態モデル

```rust
pub struct ReplayBarState {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub granularity: Option<Granularity>,
    pub strategy_file: Option<PathBuf>,
    pub initial_cash: String,
    pub current_day: Option<String>,   // DateChangeMarker IPC 受信で更新
    pub replay_paused: bool,
    pub replay_has_history: bool,      // ReplayHistoryChanged IPC 受信で更新
}
```

> `current_day` は `BarMessage` 経由ではなく、`main.rs` が `DateChangeMarker` IPC を
> 受けて直接 `menu_bar.replay_bar.current_day` を更新する（境界外更新パス）。

入力欄の変更は **`▶ 再生` 押下時のみ** engine に反映（新セッションとして発行）。
再生中に変更しても即座に反映しない。

### 再生制御ボタン enable 条件

`replay_control_state(replay_running, replay_paused, replay_has_history, mode_switch_in_progress)`
（`src/menu.rs` の純関数）。`mode_switch_in_progress = true` のとき全ボタン disabled。

| ボタン | enabled 条件 |
|--------|------------|
| ▶ 再生 | `!replay_running \|\| replay_paused` |
| ⏸ 一時停止 | `replay_running && !replay_paused` |
| ⏭ Step+ | `replay_paused`（PAUSED 状態のみ；RUNNING 中は `EngineBusy`） |
| ⏮ Step- | `replay_running && replay_has_history` |
| ⏹ 停止 | `replay_running` |

### IPC 拡張（SCHEMA_MINOR = 16）

| Op | 方向 | 受理状態 |
|----|------|---------|
| `PauseReplay` | Rust → Python | RUNNING |
| `ResumeReplay` | Rust → Python | PAUSED |
| `StepReplay` | Rust → Python | PAUSED のみ |
| `StepBackward` | Rust → Python | PAUSED かつ snapshot 非空 |
| `DateChangeMarker` | Python → Rust | — |
| `RestoreSnapshot` | Python → Rust | — |
| `ReplayHistoryChanged` | Python → Rust | — |

全コマンドは `request_id: String` フィールド付き。`PressPause` は IPC 失敗時に
`ReplayPauseStateChanged { paused: false }` で `replay_paused` を即座にロールバックする。

### Snapshot ring buffer

Python 側 `server.py` が `_replay_snapshots: deque[ReplaySnapshot]` (`maxlen=1000`)
で保持。各粒度境界（bar 完結時）に push。Step- は `RestoreSnapshot` を Rust に
先行送信して pane 全置換モードに切り替え、`pop()` した snapshot で `portfolio` /
`open_orders` / `strategy_state` を復元、UI イベントを再送信する。

`copy.deepcopy(strategy)` 失敗ステップは snapshot 非保存（`logger.warning`）。
その step は Step- 不可。

### 既知の制限

- **DPI スケーリング**: 64px 2 段組が高 DPI Windows で崩れないかは実機未確認。
- **deepcopy 失敗**: strategy が `copy.deepcopy` 非対応の step は Step- 不可。
- **snapshot maxlen 非対称**: `maxlen=1000` は Daily では年単位、Trade では数十分分に相当。
- **モーダル残存**: `replay_form_modal` 経路は当面 `Action::ReplayStart` 経由で残存（UI からは到達不能）。
- **Pause 中の dirty 判定**: Pause 中は engine_busy 扱いとし、フッタートグルは `disabled_reason` を返す。

---

## ステータスバー（フッター）

メインウィンドウ最下部に固定表示される 20px のバー。現在の起動モード（`LIVE` / `REPLAY`）
を常時視認でき、クリックでモードをトグルできる。`venue_login_chip` を左側に並べる。

### レイアウト定数

| 項目 | 値 |
|------|----|
| 高さ | `STATUS_BAR_HEIGHT = 20`（u32） |
| 背景色 | `STATUS_BAR_BG = Color::from_rgb(0.08, 0.08, 0.08)` |
| バッジ位置 | 左端（padding left 8px） |
| フォントサイズ | 11px |

| 状態 | ラベル | 色 | カーソル |
|------|--------|----|----------|
| Live（有効） | `● LIVE` | 緑 `(0.2, 0.75, 0.3)` | pointer |
| Replay（有効） | `● REPLAY` | アンバー `(0.9, 0.6, 0.1)` | pointer |
| 抑制中 | `● LIVE …` / `● REPLAY …` | 各色を 50% 減光 | default |

### enable 計算（`mode_toggle_state`）

抑制理由の優先順位（高 → 低）:

1. `mode_switch_in_progress` → `"Engine を再起動中…"`
2. `engine_busy` → `"Engine がビジーです"`
3. それ以外 → `enabled = true`

dirty 時は **disabled にせず**、クリック後に save/discard confirm dialog へ遷移。

### 合成位置

`status_bar` は `view_with_modal` に渡す `base` の**内側**（`active_menu` 分岐の直前）に push する。

- **popout ウィンドウには表示しない**（`id == self.main_window.id` ブロック内のみ）
- `main_dialog_modal`（全面暗転）展開中は overlay の下に隠れる（意図的トレードオフ）
- `dashboard_modal`（背景透過）展開中はフッターは見える

### 既知の制限

- **toast と重複**: toast は footer に重なる可能性あり（意図的トレードオフ）。
- **右クリック未対応**: ミニメニューは将来フェーズ。
- **`status_bar()` の `'static`**: 入力参照がないため lifetime elision 不可。

---

## モード切替（live ⇄ replay）

ステータスバー（フッター）の `● LIVE` / `● REPLAY` バッジをクリックして
live / replay モードを切り替える。`モード（Mode）` トップレベルメニューは廃止済み。

### 切替フロー

1. フッタークリック / `Ctrl+Cmd+M` → `Action::SwitchAppMode(target)` を `NativeMenuAction` として dispatch
2. live → replay 切替時: **dirty チェック**（dirty かつ live モードのとき confirm dialog を表示）
3. engine プロセスを**再起動**（live engine と replay engine は内部状態が大きく異なるため再利用しない）
4. `engine-session.json` を engine プロセスの Drop で削除 → bootstrap で新トークン・新 PID で再生成
5. `APP_MODE` static を更新 → サブスクリプション・ペイン構成を新モードで再構築

### dirty チェック confirm dialog

| 選択 | Action |
|------|--------|
| 保存して切替 | `SaveAndSwitchMode` |
| 破棄して切替 | `DiscardAndSwitchMode` |
| キャンセル | `GoBack`（`pending_mode_switch` / `_mode_switch_guard` を**一括クリア**） |

dirty チェック対象は **live モードのみ**（replay モードからの切替では dialog を出さない）。

### 不変条件

- `tachibana_orders.jsonl` を**書き換えない**（重複発注防止 WAL 保護）。切替時の参照は read-only のみ。
- 再入禁止: `_mode_switch_guard: Option<ModeSwitchGuard>` で連打を防ぐ。RAII で完了 / panic 時に必ず解除。
- `--mode` CLI 引数は起動時のみ。切替後の `restart()` でも CLI 値は読まない（`APP_MODE` static が正本）。
- `MODE_SWITCHING` AtomicBool 中は再生制御ボタン・`Ctrl/Cmd+M` も disabled。
- 同モードへの切替（`SwitchAppMode(current)`）は no-op として早期リターン。
- `Action::Save` / `Action::SaveAs` / `ExitRequested` / `NativeOpenFilePendingCheck` ハンドラ冒頭は
  `confirm_dialog.is_none()` ガードを通す（切替 confirm 表示中の多重起動を防ぐ）。

---

## File メニュー / Save

Open / Save / Save As / Quit の動作・`CURRENT_PATH` 管理・dirty 判定・
replay モードの SCENARIO 経路をまとめる。

### モード別動作

| モード | `開く` | `上書き保存` | `名前を付けて保存` |
|--------|--------|-------------|-----------------|
| Live | `.json` 選択 → `saved-state.json` 上書き → `restart()` | `CURRENT_PATH` あり: 直書き / なし: SaveAs フォールバック | 任意 `.json` へ書き出し。`CURRENT_PATH` 更新 |
| Replay | `.py` 選択 → `SCENARIO` 抽出 → `ReplayBarState` prefill | 戦略 `.py` の `SCENARIO` 書き戻し | 戦略 `.py` の `SCENARIO` 書き戻し（別パス可） |

`終了`: dirty チェックを通って終了。macOS Cmd+Q もキーボード subscription 経由で
`Action::Quit` → `ExitRequested` に流れる（`PredefinedMenuItem::quit` は使わないため dirty 確認が確実に走る）。

### CURRENT_PATH

`static CURRENT_PATH: Mutex<Option<PathBuf>>`（`src/main.rs`）。

セットタイミング:

- `開く` 成功時
- `名前を付けて保存` 成功時
- 起動時 `--saved-state <PATH>` 指定時

#### 保存先の決定ロジック

| 操作 | `CURRENT_PATH = Some(p)` | `CURRENT_PATH = None` |
|------|--------------------------|------------------------|
| 明示 Save / Save As | `p` と `saved-state.json` の**両方に書く** | `saved-state.json` のみ |
| 自動保存 hook | `saved-state.json` のみ | `saved-state.json` のみ |

明示 Save が両方書くことで「Save 後にクラッシュしても任意パスだけが新しく
saved-state は古い」というスキューを排除する。

#### 不変条件

- 全 `lock()` 箇所で `Err(poisoned) => poisoned.into_inner()` パターンを使い panic 連鎖を防ぐ。
- `--saved-state` の非 UTF-8 パスは `log::error!` を出力し `SavedState::default()` を返す（`CURRENT_PATH` はセットしない）。
- `pending_save_path` のような共有スロットは持たない。Message にパスを直接埋め込む。

### dirty 判定

```text
dirty = match last_saved_bytes {
    None    => false,           // 初期状態は clean
    Some(b) => build_state_json() != b,
}
```

`build_state_json()` は `BTreeMap` ベースの**決定論的シリアライズ**。
`HashMap` / `FxHashMap` への退行は偽陽性を生むため禁止。
`AudioStream::streams` も `BTreeMap`（`SerTicker` / `Exchange` / `Ticker` に `Ord` 実装）。

`last_saved_bytes` の更新は明示 Save / 自動保存 hook の**両方で同じパスを通す**。

### confirm dialog 発火経路

Open / Quit / SwitchMode の 3 経路で dirty かつ live モード時に `confirm_dialog_overlay` を表示。

| 選択 | Action |
|------|--------|
| 保存して続行 | `SaveAndOpenFile` / `SaveAndExit` / `SaveAndSwitchMode` |
| 破棄して続行 | `DiscardAndOpenFile` / `DiscardAndExit` / `DiscardAndSwitchMode` |
| キャンセル | `GoBack`（pending 状態を**一括クリア**） |

### Save As の上書き確認

rfd `save_file()` の OS 側上書き確認に加え、アプリ層でも confirm ダイアログを出す。
パスは Message 自身が運ぶ（`pending_save_path` のような共有スロットを使わない）。

### 保存エラー分類

| エラー種別 | UI 挙動 | ログレベル |
|-----------|--------|-----------|
| `Cancelled` | 中止のみ。ダイアログなし | 出力なし |
| `IoError(kind)` | エラーダイアログ + 中止 | WARN |
| `PathGuardViolation { reason }` | エラーダイアログ + 中止 | ERROR（`BUG:` プレフィックス付き） |

`save_state_to_disk` は `log::error!` を直接呼ばず `log_save_error(...)` を通す。

### replay モードの SCENARIO 経路

`SCENARIO` は戦略 `.py` に埋め込まれた再現条件定数。

- **抽出（Open）**: `python/engine/scenario.py::extract()` が `ast.parse + ast.literal_eval` で
  `SCENARIO` 定数のみ安全抽出。任意コード実行は Run 押下時の `importlib.util.spec_from_file_location` に限定。
  抽出結果は `EngineEvent::StrategyScenarioLoaded` として GUI に届き、`ReplayBarState` に prefill。
- **書き戻し（Save / Save As）**: `libcst` で `SCENARIO = {...}` の代入文ノードのみ置換。
  戦略本体・コメント・docstring・import は一切触らない。`tempfile + os.replace()` の atomic write +
  `.bak.<UTC秒>` 形式で世代付きバックアップ + `ast.parse + extract + validate` で再検証。
- **path ガード**: `.py` 拡張子必須 / `Save` は `LoadStrategyScenario` で読み込んだ path と一致のみ /
  `Save As` は派生 path 許容（server 側で `path == loaded_path` を reject）/ 永続状態ディレクトリへの
  書き込み禁止。

### IPC

| Command / Event | 用途 |
|-----------------|------|
| `Command::LoadStrategyScenario { path }` | `.py` から `SCENARIO` を抽出 |
| `Command::SaveStrategyScenario { path, scenario, save_as }` | `SCENARIO` を書き戻し |
| `Event::StrategyScenarioLoaded { path, scenario }` | 抽出成功 → GUI が prefill |
| `Event::StrategyScenarioLoadFailed { path, reason }` | 抽出失敗 → toast 表示 |
| `Event::StrategyScenarioSaved { path }` | 書き戻し成功 |

---

## Venue ログインバッジ（フッター左側）

サイドバーを開かなくても 立花・kabuステーション のログイン状態を常時確認し、
ボタン 1 クリックでログインできるようにする。

### レイアウト

```
┌──────────────────────────────────────────────────────────────────┐
│ [立花 ○ ログイン]  [kabu ○ ログイン]               ● LIVE       │
└──────────────────────────────────────────────────────────────────┘
```

`status_bar` シグネチャ:

```rust
fn status_bar(
    mode_toggle: crate::menu::ModeToggleState,
    tachibana: VenueState,
    kabu: VenueState,
) -> Element<'static, Message>
```

### バッジ仕様

`venue_login_chip(label, state, on_press)` の表示は **`class.action()` に従う**。
`Error { .. }` で常に「再ログイン」にするのではなく、既存の `VenueErrorClass` 契約を尊重する。

| VenueState | ドット | 色 | ボタン |
|------------|--------|-----|--------|
| `Idle` | `○` | dim gray | `ログイン` |
| `LoginInFlight` | `⟳` | amber | なし |
| `Ready` | `●` | green | `再ログイン` |
| `Error { class: Relogin }` | `●` | severity に応じた赤/橙 | `再ログイン` |
| `Error { class: Dismiss }` | `●` | severity 色 | なし（バナー担当） |
| `Error { class: Hidden }` | `●` | severity 色 | なし |

### Python error code とのマッピング

- `local_app_down` — kabuステーション本体が起動していない（`server.py:2964`）→ `(Error, Relogin)`
  （`engine-client/src/error.rs::VenueErrorCode::LocalAppDown` で再ログイン導線を提供）
- `login_failed` — 認証失敗（`server.py:2981`）→ `(Error, Relogin)`
- `mode_mismatch` — replay mode での拒否（`server.py:3097`）→ `(Error, Hidden)`
  （replay 中はログインボタンを出さないことが正しい挙動）

### Rust 側 FSM 不変条件

- `CONNECTING` 中の重複要求 → `VenueLoginStarted` を再 emit → FSM は
  `LoginInFlight + LoginStarted = LoginInFlight`（べき等）
- `CONNECTED` 中の再ログイン → セッションクリア後に新規接続 → FSM は
  `Ready` → `LoginInFlight`（`try_claim_login_in_flight()` が担保）
- `engine_status_stream()` が `EngineConnected` を yield する箇所では venue ごとに
  `VenueEvent::EngineRehello` を発行し、FSM を `Idle` にリセット
- `restart()` では `cached_venue_is_ready(venue)` を見て `VenueEvent::Ready` を再 emit

### サイドバーログインボタンとの境界

サイドバー側の「立花 ログイン」ボタン（`tickers_table::tachibana_login_btn`）は廃止済み。
ただし `Action::RequestTachibanaLogin` バリアント自体は **残す**:

- `tickers_table::ToggleExchangeFilter(Tachibana) [while !ready]` → `Action::RequestTachibanaLogin(Auto)`
  というサイドバーボタンとは独立した Auto 発火パスがあるため。

---

## 主要ソースファイル一覧

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action` / `actions_for_mode` / `mode_toggle_state` / `replay_control_state` |
| `src/menu_bar_state.rs` | `TopMenu` / `BarMessage` / `ReplayBarState` / `update()` |
| `src/widget_menu_bar.rs` | iced widget bar / dropdown / `replay_control_row` / `replay_input_row` |
| `src/native_menu.rs` | `widget_keyboard_subscription()`（accelerator、全 OS） |
| `src/main.rs` | `NativeMenuAction` ハンドラ群 / `status_bar` / `venue_login_chip` / `build_state_json` / `CURRENT_PATH` / `_mode_switch_guard` |
| `src/modal/replay_form.rs` | `validate()` (pub) / `prefill_from_scenario` / `Granularity` |
| `src/venue_state.rs` | `VenueState` enum（汎用設計、tachibana / kabu 共通） |
| `engine-client/src/error.rs` | `VenueErrorCode` / `VenueErrorClass` (`Relogin / Dismiss / Hidden`) |
| `engine-client/src/dto.rs` | `Command` / `EngineEvent` バリアント |
| `python/engine/scenario.py` | SCENARIO 抽出・検証・atomic write |
| `python/engine/server.py` | Pause/Resume/Step IPC ハンドラ / `_replay_snapshots` / `_replay_paused_event` |
| `python/engine/replay_session.py` | pacing ループ（paused・step カウンタ・snapshot push） |
| `python/engine/schemas.py` | `PauseReplay` 等 `IpcMessage` サブクラス / `SCHEMA_MINOR = 16` |
