# Linux 向け iced 自前メニューバー（widget menu bar）実装計画（ハイブリッド）

<a id="top"></a>

**作成日**: 2026-05-04
**作成者**: Claude Opus 4.7（botterYosuke）
**ステータス**: 未着手・実装計画
**起点課題**: [./fix-save-menu.md](./fix-save-menu.md) の F8（プラットフォーム間で File 操作経路が分断）

> 本文書では「自前メニューバー（widget menu bar）」を `widget menu bar` と表記して以降統一する。

---

<a id="adoption"></a>
## 採用案

**案 B（iced ウィジェットで作る widget menu bar）を Linux 限定で導入し、Win/macOS は
既存の muda OS-native メニューを維持する** ハイブリッド構成。

| OS | メニュー実装 | 理由 |
|----|------------|------|
| Windows | muda（既存） | OS native の慣習を維持。タイトルバー直下に表示される標準 UX |
| macOS | muda（既存） | スクリーン上端の global menu bar が macOS の慣習。iced 自前で作ると違和感大 |
| Linux | iced widget menu bar（新規） | GTK 依存を避けつつ UX 断絶を解消 |

完全置換（全 OS で iced 自前に統一）を選ばない理由：

- macOS の global menu bar は OS API でしか作れない（ウィンドウ内に置くと iOS 風で慣習違反）
- Win/macOS で動いている muda 実装を捨てると後退になる（accessibility / OS 統合）

---

<a id="sketch"></a>
## 実装スケッチ

```text
src/
├── native_menu.rs        # 既存。Win/Mac のみ（cfg ガードはそのまま）
├── widget_menu_bar.rs    # 新規。Linux でのみ rendering
└── main.rs               # view() で cfg(target_os = "linux") 分岐
```

### `widget_menu_bar.rs` の責務

- `Action`（既存 `native_menu::Action` を共通モジュールへ昇格して再利用）を発火する
  `Element<Message>` を返す
- `File ▼` ボタン押下でドロップダウンを表示
- `app_mode` に応じてメニュー項目を切り替え（live: `開く…（Open）` / `上書き保存（Save）` /
  `名前を付けて保存…（Save As）` / 終了、replay: `Replay を開始…` / `Replay を停止`）
- ショートカット表記（`Ctrl+O` など）はラベル右側に淡色で併記。
  実 bind は本計画 [Q4](#q4) の Linux iced kbd 経路で同一 `Action` enum を発火する

### `main.rs` 側の統合

- `Flowsurface::view()` で `cfg(target_os = "linux")` の場合のみメインウィンドウの
  `Column` 先頭に `widget_menu_bar::view(&self.app_mode)` を挿入する
- ドロップダウンから発火する `Message` は既存の `Message::NativeMenuAction(Action)` を
  そのまま流用する → ハンドラ実装の重複ゼロ
- popout ウィンドウには表示しない（既存 muda も同様の挙動）

### Action の単一化

`native_menu::Action` を `menu::Action`（モジュール名は要検討）にリネーム or 共通モジュールに
昇格させ、muda 経路と iced widget 経路で同じ enum を発火する。これにより：

- ハンドラ（`Message::NativeMenuAction`）は実装が 1 本のまま
- テスト（`actions_for_mode` 等）がプラットフォーム横断で意味を持つ

---

<a id="design-questions"></a>
## 設計上の論点

<a id="q1"></a>
### Q1. ドロップダウンの実装方式

候補：

- **a. `iced::widget::pick_list`** — 標準だが「メニュー」UX としては挙動が異なる
  （選択値を保持してしまう）
- **b. `iced::overlay` + `mouse_area`** — 自前で押下→オーバーレイ表示。柔軟だが実装量多い
- **c. 既存の `iced_aw::menu`（依存追加）** — メニュー専用ウィジェット。最も自然な UX

#### 依存有無の確定（`cargo tree | grep iced_aw`）

```text
$ cargo tree | grep iced_aw
(no match)
```

> 注: 当該タスクの harness 上では `cargo tree` 直接実行が許可されなかったため、
> 同等の根拠として workspace の `Cargo.lock` を `iced_aw` で検索した結果 0 件であることを
> 確認している（`Cargo.lock` 全体に `iced_aw` パッケージ entry 無し）。

→ **判定**: workspace に `iced_aw` は **入っていない**。

→ **採用**: **案 b（`iced::overlay` + `mouse_area`）**。新規依存の追加は今回のスコープ外。

#### 案 b スケルトン

```rust
// src/widget_menu_bar.rs (Linux only)
#![cfg(target_os = "linux")]

use iced::{Element, Length, Point, Rectangle};
use iced::widget::{button, column, container, mouse_area, row, text};
use iced::advanced::overlay;

use crate::menu::Action;          // 共通昇格後の Action enum
use crate::AppMode;
use crate::Message;

#[derive(Default, Debug, Clone)]
pub struct State {
    /// 現在開いている Top-level メニュー（File / Mode / ...）。None = 全閉。
    pub open: Option<TopMenu>,
    /// メニューバー上の各 Top-level ボタンの絶対位置（overlay anchor 用）。
    pub anchors: Vec<(TopMenu, Rectangle)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TopMenu { File, Mode }

#[derive(Debug, Clone)]
pub enum BarMessage {
    Toggle(TopMenu),     // ボタン押下
    Pick(Action),        // ドロップダウン項目選択 → Message::NativeMenuAction(Action) へ写像
    Dismiss,             // Esc / focus-lost / 外側クリック
}

/// メニューバー全体の view。`Column` 先頭に挿入する。
pub fn view<'a>(state: &'a State, mode: &'a AppMode) -> Element<'a, Message> {
    // 1) 上段ボタン列（File / Mode / ...）
    // 2) state.open == Some(File) のとき overlay でドロップダウン
    //    - mouse_area で「外側クリック」を捕捉して BarMessage::Dismiss
    //    - on_key(Escape) でも Dismiss
    //    - ウィンドウ focus loss は iced::Subscription 側で検知して Dismiss を投げる
    todo!()
}

/// `app_mode` に応じた File メニュー項目を返す（テスト対象）。
pub fn menu_items(mode: &AppMode) -> Vec<Action> {
    match mode {
        AppMode::Live => vec![
            Action::Open, Action::Save, Action::SaveAs, Action::Quit,
        ],
        AppMode::Replay => vec![
            Action::ReplayStart, Action::ReplayStop, Action::Quit,
        ],
    }
}
```

State 管理・focus loss handling の不変条件：

1. **Esc キー**で全閉（`BarMessage::Dismiss`）
2. **ウィンドウ focus-lost**（`iced::event::Event::Window(WindowEvent::Unfocused)`）で全閉
3. **外側クリック**（`mouse_area` の外側押下）で全閉
4. ドロップダウン項目選択時は **`Pick(action)` 発火 → `state.open = None` → 親へ
   `Message::NativeMenuAction(action)` を bubble**

#### State 純関数化（統一決定 R2-39）

上記不変条件 1〜3 を GUI レイヤから検証可能にするため、`BarMessage::Dismiss` 等の
ハンドラを **純関数 `update`** として切り出し、overlay 描画層から分離する：

```rust
// src/widget_menu_bar.rs
/// 純関数。GUI レイヤ（overlay 描画）に依存せず State 遷移のみを行う。
pub fn update(state: State, msg: BarMessage) -> State {
    match msg {
        BarMessage::Toggle(top) => State {
            open: if state.open == Some(top) { None } else { Some(top) },
            ..state
        },
        BarMessage::Pick(_) | BarMessage::Dismiss => State {
            open: None,
            ..state
        },
    }
}
```

これにより [`tests/widget_menu_bar_state.rs`](#widget-menu-bar-state-tests) で
**3 契約（Esc / focus-lost / 外側クリック）× 開状態 3 ケース（File 開・Mode 開・閉）**
の遷移を assert できる。GUI レイヤ（overlay 描画・実際のキーイベント配線）は
[受け入れ基準](#acceptance) DoD-2〜DoD-4 の手動スモークで補完する。

<a id="widget-menu-bar-state-tests"></a>
**`tests/widget_menu_bar_state.rs` 対応表（3 契約 × 3 開状態）**：

| # | 開状態 (`state.open`) | 入力（`BarMessage`） | 期待後状態 (`state.open`) | 観測契約 |
|---|----------------------|---------------------|-------------------------|---------|
| 1 | `Some(File)` | `Dismiss`（Esc 由来） | `None` | Esc / File 開 |
| 2 | `Some(Mode)` | `Dismiss`（Esc 由来） | `None` | Esc / Mode 開 |
| 3 | `None`       | `Dismiss`（Esc 由来） | `None` | Esc / 閉（冪等） |
| 4 | `Some(File)` | `Dismiss`（focus-lost 由来） | `None` | focus-lost / File 開 |
| 5 | `Some(Mode)` | `Dismiss`（focus-lost 由来） | `None` | focus-lost / Mode 開 |
| 6 | `None`       | `Dismiss`（focus-lost 由来） | `None` | focus-lost / 閉（冪等） |
| 7 | `Some(File)` | `Dismiss`（外側クリック由来） | `None` | 外側クリック / File 開 |
| 8 | `Some(Mode)` | `Dismiss`（外側クリック由来） | `None` | 外側クリック / Mode 開 |
| 9 | `None`       | `Dismiss`（外側クリック由来） | `None` | 外側クリック / 閉（冪等） |

> 注: `BarMessage::Dismiss` 自身は dismiss 理由を保持しない（純関数 `update` は遷移先のみで決定論的）。
> 「理由」は呼び出し側（Subscription / overlay）でログ出力（DoD-2〜4 の `dismiss reason=...`）に
> 反映され、State 遷移の正しさはテストで、reason ログの正しさは手動スモークで担保する分業とする。

<a id="q2"></a>
### Q2. macOS / Windows での widget メニュー併存

`cfg(target_os = "linux")` で Linux 限定にする。Win/Mac で widget メニューも出すと
muda と二重表示になり混乱を招く。

<a id="q3"></a>
### Q3. テーマ統合

iced widget メニューはテーマと整合する（むしろ案 B の利点）。muda 側はテーマと無関係に
OS の見た目に従うため、Linux のみ「アプリのテーマに沿ったメニュー」になる。これは
ハイブリッドの帰結として許容する。

<a id="q4"></a>
### Q4. アクセラレータ単一経路

不変条件：

- **muda 正規（Win/Mac）**：muda の accelerator が一次経路。`Action` を発火。
- **Linux iced kbd**：`#[cfg(target_os = "linux")]` で gate された iced
  `keyboard::on_key_press` Subscription が `Ctrl+O` 等を受けて **同一の `Action` enum** を発火。
- **重複検知 guard**：`cfg(all(target_os = "linux", any(target_os = "macos", target_os = "windows")))`
  は構造的にあり得ないが、`menu::register_accelerators()` 側で
  `#[cfg(not(target_os = "linux"))]` と Linux iced kbd の `#[cfg(target_os = "linux")]` を
  排他に並べてコンパイル時に二重登録を不可能にする。
- 同一 `Action` enum を発火することで Win/Mac/Linux ハンドラは 1 本に保つ。

---

<a id="non-scope"></a>
## 非スコープ（案 B でもやらないこと）

- -（対象外） macOS でメニューバーをウィンドウ内に移す
- -（対象外） サイドバーに File 項目を追加（案 D は別タスクとして将来検討）
- -（対象外） コマンドパレット（案 E）
- -（対象外） Linux で muda + GTK 有効化（案 C）

---

<a id="acceptance"></a>
## 受け入れ基準（DoD）

| ID | 内容 | 観測コマンド / 期待ログ |
|----|------|----------------------|
| DoD-1 | Linux で `File ▼` クリックでドロップダウンが現れる | `cargo run -- --mode live`（Linux）／目視 |
| DoD-2 | `Esc` 押下でドロップダウンが閉じる | 目視 + `RUST_LOG=debug`：`widget_menu_bar: dismiss reason=esc` |
| DoD-3 | ウィンドウ focus-lost でドロップダウンが閉じる | 別アプリへ alt-tab／ログ：`dismiss reason=focus_lost` |
| DoD-4 | 外側クリックでドロップダウンが閉じる | 目視／ログ：`dismiss reason=outside_click` |
| DoD-5 | live モードで `開く…（Open）` / `上書き保存（Save）` / `名前を付けて保存…（Save As）` / 終了が並ぶ | `widget_menu_bar::menu_items` ユニットテストで集合一致 |
| DoD-6 | replay モードで `Replay を開始…` / `Replay を停止` / 終了が並ぶ | 同上 |
| DoD-7 | Win/Mac/Linux で同一 `Action` を発火する cross-platform テストが green | `cargo test --test menu_actions_cross_platform` |
| DoD-8 | Wayland / X11 両方でスモーク完走 | [スモーク手順](#smoke) 参照 |
| DoD-9 | muda アクセラレータと Linux iced kbd の重複登録が compile-time で起こらない | `cargo build --target x86_64-unknown-linux-gnu` warn-free |

<a id="testing"></a>
## テスト方針

### Linux 限定ユニットテスト

```rust
// src/widget_menu_bar.rs
#[cfg(target_os = "linux")]
#[cfg(test)]
mod tests {
    use super::*;
    use crate::AppMode;

    #[test]
    fn menu_items_live_has_file_io_actions() {
        let items = menu_items(&AppMode::Live);
        assert!(items.contains(&Action::Open));
        assert!(items.contains(&Action::Save));
        assert!(items.contains(&Action::SaveAs));
    }

    #[test]
    fn menu_items_replay_has_replay_actions() {
        let items = menu_items(&AppMode::Replay);
        assert!(items.contains(&Action::ReplayStart));
        assert!(items.contains(&Action::ReplayStop));
    }
}
```

### Cross-platform テスト（全 OS で同じ `Action` 集合を期待）

`actions_for_mode` を共通モジュール（`src/menu.rs`）に置き、OS 非依存にする。

```rust
// tests/menu_actions_cross_platform.rs
use flowsurface::menu::{actions_for_mode, Action};
use flowsurface::AppMode;

#[test]
fn live_actions_are_identical_across_os() {
    let got = actions_for_mode(&AppMode::Live);
    assert_eq!(
        got,
        vec![Action::Open, Action::Save, Action::SaveAs, Action::Quit],
    );
}

#[test]
fn replay_actions_are_identical_across_os() {
    let got = actions_for_mode(&AppMode::Replay);
    assert_eq!(
        got,
        vec![Action::ReplayStart, Action::ReplayStop, Action::Quit],
    );
}
```

このテストは Win/Mac/Linux いずれの CI runner でも `cargo test --test menu_actions_cross_platform`
で実行され、OS 間で `Action` 集合が一致することを保証する。

<a id="smoke"></a>
### Wayland / X11 両環境スモーク手順

| 環境 | 手順 | 期待 |
|------|------|------|
| X11  | `XDG_SESSION_TYPE=x11 cargo run -- --mode live` → File メニュー操作 → Esc・focus-lost で閉じる | DoD-1〜DoD-4 が満たされる |
| Wayland | `XDG_SESSION_TYPE=wayland WAYLAND_DISPLAY=wayland-0 cargo run -- --mode live` → 同上 | 同上 |

両環境のログに以下が出ること（テストファイル：手動スモーク、`docs/✅menu-and-footer/P8-widget-menu-bar-linux.md` のスモーク欄に貼付）：

- 起動時：`widget_menu_bar: enabled (target_os=linux, session=x11|wayland)`
- 開閉：`widget_menu_bar: open=File` / `dismiss reason=...`

### `iced_aw` 採用時の version pin（参考）

今回は採用しないが、将来 `iced_aw::menu` 系へ切り替える場合は **`Cargo.toml` で
`^x.y.z` ではなく `=x.y.z` で固定** する：

```toml
[dependencies]
iced_aw = { version = "=0.9.3", default-features = false, features = ["menu"] }
```

理由：`iced_aw` は `iced` の minor 更新に追従するために breaking change を頻繁に出す。
caret range で取り込むと `cargo update` で UI が壊れる事故が起きうる。

---

<a id="related"></a>
## 関連タスクとの関係

- **F2（ショートカット未実装）** とは独立。本計画は UI 表示の問題、F2 はキー bind の問題。
  併せて入れると Linux ユーザーが `Ctrl+O` でも、メニュークリックでも同じ `Action` を発火できる
- **[./P7-mode-switch-menu.md](./P7-mode-switch-menu.md)** は本計画を前提とする
  （Linux で `Mode` メニューを出すには iced widget menu bar が必要）
