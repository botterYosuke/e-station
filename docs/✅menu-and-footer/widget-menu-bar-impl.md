# Linux 自前メニューバー（widget menu bar）

Linux 環境では `muda` クレートが GTK 制約で完全動作しないため、iced の widget で
等価のメニューバーを自前で実装している。Win/macOS は OS ネイティブ（muda）、Linux は
iced widget という二系統構成。

---

## アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│ src/menu.rs                                          │
│   Action enum / MenuEntry / actions_for_mode /       │
│   mode_menu_items / tools_actions_for_state          │  cross-platform
└──────────┬───────────────────────────────────────────┘
           │
   ┌───────┴────────────────────┐
   │                            │
┌──▼─────────────┐    ┌─────────▼──────────┐
│ native_menu.rs │    │ widget_menu_bar.rs │
│ Win / macOS    │    │ Linux 限定         │
│ muda           │    │ iced widget        │
└──┬─────────────┘    └─────────┬──────────┘
   │                            │
   └────────────┬───────────────┘
                ▼
   Message::NativeMenuAction(Action)  （単一経路）
```

不変条件：

- メニュー項目の表示計算（`actions_for_mode` / `mode_menu_items` /
  `tools_actions_for_state`）は `src/menu.rs` に集約され、**プラットフォームを
  問わず同じ集合を返す**。Win/Mac と Linux で項目が食い違わない
- アクション dispatch は **`Message::NativeMenuAction(Action)` 単一経路**。
  widget 側は `to_native_action()` で同じ Message に正規化する
- `widget_menu_bar.rs` は `#![cfg(target_os = "linux")]` で完全に gate される。
  Win/macOS のビルドにはリンクされない

---

## 状態モデル

`src/menu_bar_state.rs`（cfg gate なし、全 OS で compile）に以下を定義：

```rust
pub enum TopMenu { File, Mode, Tools }

pub enum BarMessage {
    Toggle(TopMenu),         // 上位メニューの開閉トグル
    Pick(Action),            // ドロップダウン項目選択
    Dismiss,                 // Esc / 外側クリック
    DismissFocusLost,        // ウィンドウが unfocus
}

pub struct State { /* どの top-level menu が開いているか */ }
pub fn update(state: &mut State, msg: BarMessage) -> Option<Action>;
```

- `update()` は純関数。ソースインスペクション方式のテスト
  （`tests/widget_menu_bar_state.rs`）が全 OS で compile できるよう、`mod
  menu_bar_state` 自体に cfg gate を **付けない**
- `Dismiss` と `DismissFocusLost` は分離（ログ理由を区別したい）

---

## レイアウト

`src/widget_menu_bar.rs`：

| 要素 | サイズ | 役割 |
|------|--------|------|
| 各メニューボタン | `BTN_WIDTH = 155.0` 固定 | `File ▼` / `Mode ▼` / `Tools ▼` |
| バー高 | `BAR_HEIGHT = 32.0` 固定 | ドロップダウンの anchor を bar 下端に固定 |
| 残り領域 | flex | `mouse_area` で `Dismiss` を発火（DoD-4） |

ドロップダウンは `with_dropdown_overlay()` が `stack` で重ね、水平位置は
`BTN_WIDTH + spacing` から決定論的に計算（マジックピクセルなし）。

`mouse_area::on_move` の widget-local 座標（0..BAR_HEIGHT）に依存しないよう、
動的 Y アンカーは F8 R2 で削除済み。

---

## アクセラレータ

Linux でも Ctrl+O / Ctrl+S / Ctrl+Shift+S / Ctrl+Q を有効化するため、iced の
`keyboard::on_key_press` subscription を `cfg(target_os = "linux")` 限定で登録する。
詳細は [save-menu-impl.md §アクセラレータ経路](./save-menu-impl.md#アクセラレータ経路)
参照。

二重発火回避：

- Win/macOS では muda 一本化、`linux_keyboard_subscription` は登録されない
- Linux では muda は使わず、subscription が唯一の accelerator 経路
- `linux_keyboard_subscription(app_mode)` が `app_mode` を見て live 専用キーを
  replay モード時に抑制する

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action` / `MenuEntry` / 項目集合の cross-platform 計算 |
| `src/menu_bar_state.rs` | `TopMenu` / `BarMessage` / `State` / `update`（cfg gate なし） |
| `src/widget_menu_bar.rs` | iced widget による bar / dropdown overlay（Linux 限定） |
| `src/native_menu.rs` | muda 統合（Win / macOS） |

---

## リグレッションガード

| テスト | 対象 |
|--------|------|
| `tests/widget_menu_bar_state.rs` | `update` の状態遷移（全 OS で compile） |
| `tests/menu_actions_cross_platform.rs` | 全 OS で `actions_for_mode(&AppMode::Live\|Replay)` が同じ集合を返す |
| `tests/tools_actions_for_state.rs` | Tools サブメニューの `MenuEntry` 期待値 |
| `tests/mode_menu_items.rs` | Mode サブメニューの `MenuEntry` 期待値 |

---

## 既知の制限

- **テーマ統合**: 現状はライト / ダーク両対応の最小スタイル。ホバー / ボタン押下時
  のスタイル統一は未対応
- **Wayland / X11**: Wayland と X11 両環境でのスモークは手動。CI 上での再現テストは
  持たない
- **macOS / Windows での widget メニュー併存**: `cfg(target_os = "linux")` で完全
  分離されているため、Win/Mac で誤って widget bar が表示されることはない
