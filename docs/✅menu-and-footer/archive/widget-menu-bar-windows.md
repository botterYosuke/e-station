# muda 完全廃止 — 全 OS で iced widget メニューバーに統一

## 背景・目的

以前の三系統構成：
- Win / macOS → `native_menu.rs`（muda、OS ネイティブ）
- Linux → `widget_menu_bar.rs`（iced widget）

**最終方針**: muda を完全廃止し、全プラットフォーム（Windows / macOS / Linux）で iced widget メニューバーに統一。

メリット：
- コード複雑度の大幅削減（platform mod 廃止）
- Windows バイナリのシンプル化
- macOS でも一貫した見た目・挙動（OS ネイティブメニューバーではなく in-window）
- cfg ゲートの廃止 → テストが全 OS で compile

---

## アーキテクチャ（実装後）

```
┌──────────────────────────────────────────────┐
│ src/menu.rs                                  │
│ Action enum / MenuEntry / actions_for_mode  │  cross-platform
│ mode_menu_items / tools_actions_for_state   │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────┴───────────────────────────┐
│ src/widget_menu_bar.rs (全 OS)               │
│ view() / with_dropdown_overlay()             │
│ iced widget bar + dropdown overlay           │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────┴───────────────────────────┐
│ src/native_menu.rs (keyboard only)           │
│ widget_keyboard_subscription()                │
│ iced::keyboard::listen() + filter_map        │
│ (Subscription::with で is_live を非キャプ)   │
└──────────────────┬───────────────────────────┘
                   │
            Message::NativeMenuAction(Action)
            （単一経路）
```

## 実装詳細

### cfg ゲート廃止

**以前**:
- `#![cfg(target_os = "linux")]` が `widget_menu_bar.rs` 先頭に
- `#[cfg(target_os = "linux")]` が `main.rs` で 6 箇所に分散
- `#[cfg(any(target_os = "windows", target_os = "macos"))]` が `native_menu.rs` に

**実装後**:
- cfg ゲートなし — 全ファイル無条件 compile
- テスト（`tests/widget_menu_bar_state.rs` など）が全 OS で実行可能に

### iced 0.14 の制約への対応

iced 0.14 の `Subscription::filter_map()` は **非キャプチャクロージャのみ** を受け付ける（zero-sized closure check）。

**解決方法**: `Subscription::with(is_live)` で値を bind し、`filter_map` の closure parameter として受け取る：

```rust
fn widget_keyboard_subscription(app_mode: AppMode) -> Subscription<Action> {
    let is_live = app_mode == AppMode::Live;
    iced::keyboard::listen()
        .with(is_live)
        .filter_map(|(is_live, event): (bool, iced::keyboard::Event)| {
            // closure は is_live をパラメータで受け取る（キャプチャではない）
            // crate::MODE_SWITCHING は global で、キャプチャでなく参照
        })
}
```

### macOS キー対応

Win/Linux では Ctrl+O/S/Q、macOS では Cmd+O/S/Q（ユーザー期待）。

**実装**:
- `modifiers.control() || modifiers.logo()` で両方受け付け（`logo()` = Cmd on macOS）
- ショートカットラベル表示を `#[cfg(target_os = "macos")]` で切り替え：
  - macOS: "Cmd+O", "Cmd+S", "Cmd+Q"
  - Win/Linux: "Ctrl+O", "Ctrl+S", "Ctrl+Q"

### Space::new() API

iced 0.14 で `Space::new(width, height)` → `Space::new().width(...).height(...)` に変更。

**修正箇所**:
- `widget_menu_bar.rs:63`: `Space::new(Length::Fill, Length::Fill)` → `Space::new().width(Length::Fill).height(Length::Fill)`
- `widget_menu_bar.rs:143`: 類似

## 変更ファイル

| ファイル | 変更内容 |
|---------|---------|
| `Cargo.toml` | `muda` target-specific dependency 削除 |
| `src/native_menu.rs` | `platform` mod 廃止、`attach`/`refresh_tools_enable` を no-op 化、`widget_keyboard_subscription` に統一 |
| `src/widget_menu_bar.rs` | `#![cfg(target_os = "linux")]` 削除、`view`/`with_dropdown_overlay` を値渡しに、Space API 修正 |
| `src/main.rs` | `mod widget_menu_bar` など 6 箇所の cfg(linux) 削除、`pub(crate) fn app_mode()` 昇格 |
| `tests/widget_menu_bar_state.rs` | `file_is_linux_only` → `file_has_no_linux_cfg_gate` |
| `tests/accelerator_bind.rs` | 全面書き換え — muda テスト廃止、keyboard::listen 対応 |
| `tests/mode_switch_accelerator_disabled.rs` | テスト名・検索パターン更新 |

## 変更しないもの

- `src/menu.rs`（`Action` / `MenuEntry` / 集合計算）— クロスプラットフォーム不変
- `src/menu_bar_state.rs`（`State` / `update`）— cfg gate なしのまま
- `docs/✅menu-and-footer/widget-menu-bar-impl.md` — Linux 専用の経緯は歴史的価値あり（削除せず）

## 不変条件（変更後も維持）

1. **アクション dispatch は `Message::NativeMenuAction(Action)` 単一経路**
   — 呼び出し側は platform 非依存
2. **`menu_bar_state` は cfg gate なし**
   — 全 OS テストが同じコードパス
3. **キーボード subscription は MODE_SWITCHING を確認**
   — mode switch 中の重複 dispatch 防止（統一決定 64）
4. **Tools submenu は `tools_actions_for_state` で動的計算**
   — W&B auth / run-buffer state に応じた enable/disable（H5）

## 既知の制限

- **テーマ統合**: ライト / ダーク両対応の最小スタイルのまま
- **DPI スケーリング**: Windows 高 DPI での `BAR_HEIGHT = 32.0` 適切性は要確認
- **Wayland / X11**: Linux 環境での動作再確認は未実施
- **macOS 視覚的整合性**: OS ネイティブメニューバーではなく in-window のため、環境に応じた見え方差異

## テスト状況

**合格**: 
- 285 unit tests (src/main.rs)
- 12 accelerator binding tests（全 OS 対応化）
- 12 widget_menu_bar_state tests
- その他 59 tests

**既存失敗（今回と無関係）**:
- `restart_with_mode_clears_engine_connection_before_restart`（engine connection clear 順序）

## デプロイメント

全プラットフォーム向けバイナリが同一の menu bar ロジック使用。
OS 別の視覚的相違は `#[cfg(target_os = "macos")]` キーラベル表示のみ。
