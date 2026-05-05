# メニューバー実装（iced widget・全 OS 統一）

メインウィンドウの上部メニューバー（File / Mode / Tools）は **iced widget で
自前実装**し、Windows・macOS・Linux のすべてで同一コードを使う。muda などの
OS ネイティブメニュー統合は廃止済み。

二系統構成（旧: Win/macOS = muda、Linux = widget）から **単一系統**に統合した。
背景・移行手順は archive: [widget-menu-bar-windows.md](./archive/widget-menu-bar-windows.md)。

---

## アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│ src/menu.rs                                          │
│   Action enum / MenuEntry / actions_for_mode /       │
│   mode_menu_items / tools_actions_for_state          │  cross-platform
└──────────┬───────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────┐
│ src/widget_menu_bar.rs（全 OS）                      │
│   view() / with_dropdown_overlay()                   │
│   iced widget bar + dropdown overlay                 │
└──────────┬───────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────┐
│ src/native_menu.rs（keyboard accelerator のみ）      │
│   widget_keyboard_subscription()                     │
│   iced::keyboard::listen() + filter_map              │
│   (Subscription::with で is_live を非キャプチャ渡し) │
└──────────┬───────────────────────────────────────────┘
           ▼
   Message::NativeMenuAction(Action)  （単一経路）
```

**不変条件**：

- メニュー項目の表示計算（`actions_for_mode` / `mode_menu_items` /
  `tools_actions_for_state`）は `src/menu.rs` に集約。**プラットフォームを
  問わず同じ集合を返す**。
- アクション dispatch は **`Message::NativeMenuAction(Action)` 単一経路**。
  widget 側の `to_native_action()` で Pick → Action に正規化する。
- ファイル全体に `#[cfg(target_os = ...)]` ゲートはなし。`menu_bar_state` /
  `widget_menu_bar` / `native_menu` の 3 ファイルとも全 OS 共通でビルドされる。
- `native_menu::attach()` / `refresh_tools_enable()` は **no-op**（呼び出しは
  互換のため残してある）。

---

## 状態モデル

`src/menu_bar_state.rs`（全 OS で compile）に以下を定義：

```rust
pub enum TopMenu { File, Mode, Tools }

pub enum BarMessage {
    Toggle(TopMenu),         // 上位メニューの開閉トグル
    Pick(Action),            // ドロップダウン項目選択
    Dismiss,                 // Esc / 外側クリック
    DismissFocusLost,        // ウィンドウが unfocus
}

pub struct State { /* どの top-level menu が開いているか */ }
pub fn update(state: State, msg: BarMessage) -> State;
```

- `update()` は純関数。ソースインスペクション方式のテスト
  （`tests/widget_menu_bar_state.rs`）が cfg gate なしで全 OS から実行可能。
- `Dismiss` と `DismissFocusLost` は分離（ログ理由を区別する）。

---

## レイアウト

`src/widget_menu_bar.rs`：

| 要素 | サイズ | 役割 |
|------|--------|------|
| 各メニューボタン | `BTN_WIDTH = 155.0` 固定 | `File ▼` / `Mode ▼` / `Tools ▼` |
| バー高 | `BAR_HEIGHT = 32.0` 固定 | ドロップダウン anchor を bar 下端に固定 |
| 残り領域 | flex | `mouse_area` で `Dismiss` を発火（DoD-4） |

ドロップダウンは `with_dropdown_overlay()` が `stack` で重ねる。水平位置は
`BTN_WIDTH + spacing` から決定論的に計算（マジックピクセルなし）。垂直位置は
定数 `BAR_HEIGHT`（F8 R2 / H3' で動的 Y アンカー機構を廃止）。

`view()` / `with_dropdown_overlay()` の `mode: AppMode` 引数は **値渡し**
（`Copy` 型）。一時値（`app_mode()` 戻り値）への参照によるライフタイムエラーを
避けるため。

---

## アクセラレータ

`src/native_menu.rs::widget_keyboard_subscription()` を全 OS で登録する。
内部は `iced::keyboard::listen().with(is_live).filter_map(...)`。

**iced 0.14 制約への対応**: `filter_map` のクロージャは zero-sized（非キャプチャ）
でなければならない。`Subscription::with(is_live)` で値を bind し、closure の第一
引数として受け取ることでキャプチャを回避している。

| OS | 主修飾キー | アクセラレータ表示 |
|----|----------|------------------|
| Windows / Linux | Ctrl のみ | `Ctrl+O`, `Ctrl+S`, `Ctrl+Shift+S`, `Ctrl+Q`, `Ctrl+M` |
| macOS | Ctrl または Cmd（logo） | `Cmd+O`, `Cmd+S`, `Cmd+Shift+S`, `Cmd+Q`, `Cmd+M` |

ショートカットラベルは `widget_menu_bar.rs` の `action_label_and_shortcut()` で
`#[cfg(target_os = "macos")]` 切り替え。

### レイアウト非依存マッチ

キーマッチは **`Key::Character`（文字）ではなく `physical_key`（物理キー位置）**
を使用：

```rust
match physical_key {
    Physical::Code(Code::KeyO) if ctrl_or_cmd && !shift && is_live => Some(Action::OpenFile),
    Physical::Code(Code::KeyS) if ctrl_or_cmd && !shift && is_live => Some(Action::Save),
    Physical::Code(Code::KeyS) if ctrl_or_cmd && shift && is_live => Some(Action::SaveAs),
    Physical::Code(Code::KeyQ) if ctrl_or_cmd && !shift => Some(Action::Quit),
    Physical::Code(Code::KeyM) if ctrl_or_cmd && !shift => /* SwitchMode */,
    _ => None,
}
```

これにより JIS / AZERTY / Dvorak 等の非 US レイアウト・IME 状態下でも
Ctrl+S = Save が安定して機能する（旧 muda の `Code::KeyS` accelerator と同等）。

### macOS 限定で `logo()` を受理

```rust
#[cfg(target_os = "macos")]
let ctrl_or_cmd = modifiers.control() || modifiers.logo();
#[cfg(not(target_os = "macos"))]
let ctrl_or_cmd = modifiers.control();
```

Win/Linux で `logo()`（Super/Win キー）を受理すると WM 側のショートカット
（Win+Q 等）と衝突するため macOS 限定。

### モード別ガード

- `is_live` は `Subscription::with(is_live)` 経由で渡される。
  - live 専用キー（OpenFile / Save / SaveAs）は `is_live == true` のときのみ発火。
  - replay モードでは何も起きない。
- `Quit` はモード非依存。
- `SwitchMode` は `crate::MODE_SWITCHING.load(Acquire)` を確認し、再入を抑制
  （統一決定 64）。

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action` / `MenuEntry` / 項目集合の cross-platform 計算 |
| `src/menu_bar_state.rs` | `TopMenu` / `BarMessage` / `State` / `update`（全 OS） |
| `src/widget_menu_bar.rs` | iced widget による bar / dropdown overlay（全 OS） |
| `src/native_menu.rs` | `Action` enum / `widget_keyboard_subscription()` / `attach`/`refresh_tools_enable` の no-op shim |

> `src/native_menu.rs` の名前は muda 時代の歴史的経緯。中身は keyboard
> subscription と Action 定義のみで、OS ネイティブメニューは扱わない。
> 改名は将来のクリーンアップ候補。

---

## リグレッションガード

| テスト | 対象 |
|--------|------|
| `tests/widget_menu_bar_state.rs` | `update` の状態遷移、`widget_menu_bar.rs` に linux cfg gate がないこと |
| `tests/menu_actions_cross_platform.rs` | 全 OS で `actions_for_mode(&AppMode::Live\|Replay)` が同じ集合 |
| `tests/tools_actions_for_state.rs` | Tools サブメニューの `MenuEntry` 期待値 |
| `tests/mode_menu_items.rs` | Mode サブメニューの `MenuEntry` 期待値 |
| `tests/accelerator_bind.rs` | `Code::KeyO/S/Q/M` 物理キーマッチ・`physical_key` 使用・`logo()` の macOS gate・`keyboard::listen` 使用 |
| `tests/mode_switch_accelerator_disabled.rs` | `widget_keyboard_subscription` 内の `MODE_SWITCHING` 確認 |

---

## 既知の制限

- **テーマ統合**: ライト / ダーク両対応の最小スタイルのみ。ホバー / 押下時
  のスタイル統一は未対応。
- **DPI スケーリング**: Windows 高 DPI 環境で `BAR_HEIGHT = 32.0` の適切性は
  実機未確認。
- **macOS の見た目の非ネイティブ性**: macOS の慣習（スクリーン最上段に
  メニューバー）からは外れる。in-window バーが表示される。Cmd+Q はキーボード
  subscription で処理（`PredefinedMenuItem::quit` は使わないため、NSApp 直接
  処理ではなく `ExitRequested` 経由 = dirty チェックを通す）。
- **物理キー matching の盲点**: ノート PC の特殊キー配列（一部キーが Code に
  解決されない）では `Physical::Unidentified` フォールスルーで効かない可能性。
  実機検証は OS 別に必要。
