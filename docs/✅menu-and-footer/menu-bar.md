# Widget メニューバー

muda などの OS ネイティブメニューは完全廃止。全 OS（Windows / macOS / Linux）で
**iced widget の単一実装**を使用する。

---

## アーキテクチャ

```
src/menu.rs             — Action enum / actions_for_mode / mode_toggle_state
src/menu_bar_state.rs   — TopMenu / BarMessage / ReplayBarState / update()
src/widget_menu_bar.rs  — view() / with_dropdown_overlay()
src/native_menu.rs      — widget_keyboard_subscription()（accelerator のみ）
```

dispatch 経路は **`Message::NativeMenuAction(Action)` の単一系統**。
`to_native_action()` でドロップダウン選択を正規化する。

不変条件:

- `actions_for_mode` / `mode_toggle_state` は `src/menu.rs` に集約。プラットフォームを問わず同じ集合を返す。
- ファイル全体に `#[cfg(target_os = ...)]` ゲートはなし。全 OS 共通でビルドされる。
- `native_menu::attach()` / `refresh_tools_enable()` は no-op（互換のため残置）。
- `mode_menu_items` は廃止済み。

---

## レイアウト

| 要素 | 値 | 備考 |
|------|----|------|
| ボタン幅 | `BTN_WIDTH = 155.0` | `ファイル（File）▼` |
| 段 1 高さ | `bar_height(Live) = 32.0` | 常時表示 |
| 段 2 高さ | `bar_height(Replay) = 64.0` | Replay モード時のみ |
| dropdown anchor | `with_dropdown_overlay()` の `top_offset` に `bar_height(mode)` を渡す | 動的 Y アンカー廃止済み |

`view()` / `with_dropdown_overlay()` の `mode: AppMode` 引数は**値渡し**（`Copy` 型）。
一時値への参照によるライフタイムエラーを避けるため。

---

## File メニュー項目

Live / Replay 両モード共通（`actions_for_mode` は mode 依存ロジックなし）:

| 項目 | アクセラレータ |
|------|--------------|
| `開く…（Open）` | Ctrl+O |
| `上書き保存（Save）` | Ctrl+S |
| `名前を付けて保存…（Save As）` | Ctrl+Shift+S |
| `終了` | Ctrl+Q（macOS: Cmd+Q） |

> `リプレイを開始` / `リプレイを停止` は廃止済み。再生制御はバー上ボタンに移行。

---

## アクセラレータ

`src/native_menu.rs::widget_keyboard_subscription()` を全 OS で登録。
**`physical_key`（物理キー位置）** でマッチするためレイアウト非依存（JIS / AZERTY / Dvorak 等で安定）。

```rust
match physical_key {
    Code::KeyO  if ctrl_or_cmd && !shift && is_live  => Action::OpenFile,
    Code::KeyS  if ctrl_or_cmd && !shift && is_live  => Action::Save,
    Code::KeyS  if ctrl_or_cmd && shift  && is_live  => Action::SaveAs,
    Code::KeyQ  if ctrl_or_cmd && !shift             => Action::Quit,
    Code::KeyM  if ctrl_or_cmd && !shift             => /* SwitchMode */,
}
```

### OS 別修飾キー

| OS | 修飾キー | ショートカット表示 |
|----|----------|-----------------|
| Windows / Linux | Ctrl のみ | `Ctrl+O`, `Ctrl+S`, … |
| macOS | Ctrl または Cmd（logo） | `Cmd+O`, `Cmd+S`, … |

macOS のみ `modifiers.logo()`（Cmd）を受理。Win/Linux で受理すると WM（Win+Q 等）と衝突するため:

```rust
#[cfg(target_os = "macos")]
let ctrl_or_cmd = modifiers.control() || modifiers.logo();
#[cfg(not(target_os = "macos"))]
let ctrl_or_cmd = modifiers.control();
```

### モード別ガード

- `is_live` は `Subscription::with(is_live)` 経由で非キャプチャ渡し。
- Replay モードでは `is_live = false` となり、Open / Save / SaveAs は発火しない。
- `Quit` はモード非依存。
- `SwitchMode` は `crate::MODE_SWITCHING.load(Acquire)` で再入を抑制。

---

## 状態モデル

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

---

## テスト

| テスト | 対象 |
|--------|------|
| `tests/widget_menu_bar_state.rs` | `update()` 状態遷移 / `widget_menu_bar.rs` に linux cfg gate がないこと |
| `tests/menu_actions_cross_platform.rs` | 全 OS / 全モードで `actions_for_mode` が同一集合を返すこと |
| `tests/accelerator_bind.rs` | `physical_key` 使用 / `logo()` の macOS gate / `keyboard::listen` |
| `tests/mode_switch_accelerator_disabled.rs` | `MODE_SWITCHING` 中の `Ctrl/Cmd+M` 抑止 |
| `tests/widget_menu_bar_replay_layout.rs` | Live=32px / Replay=64px / dropdown anchor 連動 |

---

## 既知の制限

- **DPI スケーリング**: `BAR_HEIGHT = 32.0` / 64px 2 段組が高 DPI Windows で崩れないかは実機未確認。
- **macOS 見た目**: in-window メニューバー（スクリーン最上段ではない）。iced widget の制約による。
- **物理キー matching の盲点**: 一部ノート PC の特殊キーが `Physical::Unidentified` を返す場合はマッチしない。
- **`src/native_menu.rs` の名称**: muda 時代の歴史的経緯。中身は keyboard subscription のみ。改名は将来のクリーンアップ候補。
