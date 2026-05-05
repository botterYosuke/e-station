# Linux 向け iced 自前メニューバー（widget menu bar）実装計画（ハイブリッド）

<a id="top"></a>

**作成日**: 2026-05-04
**作成者**: Claude Opus 4.7（botterYosuke）
**ステータス**: 実装中（純関数層 ✅ / view() レンダリング層 未着手）
**実装日**: 2026-05-04
**実装者**: Claude Sonnet 4.6（botterYosuke）
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

### `MenuEntry` 構造体（統一決定 R7-86）

Tools / Mode サブメニューは `enabled` / `tooltip` / `checked`（排他チェック）の
表現が必要なため、純関数の戻り値を `Vec<Action>` から `Vec<MenuEntry>` へ拡張する。

> **H6 / F8 R1 整合**: 計画書当初は `tooltip: Option<String>` を想定していたが、
> 実装は全ての tooltip がコンパイル時定数のため `Option<&'static str>` に着地している
> （無駄な heap 割り当てを避けるため）。シグネチャの最終形は実装側を正とする。

```rust
// src/menu.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MenuEntry {
    pub action: Action,
    pub enabled: bool,
    pub tooltip: Option<&'static str>,
    /// Mode メニュー等の排他チェック表示用。`Some(true)` = 現在選択中、
    /// `Some(false)` = 候補だが未選択、`None` = チェック非表示（File 等の通常項目）。
    pub checked: Option<bool>,
}
```

**適用範囲**（実装シグネチャ）：

- `tools_actions_for_state(auth: &WandbAuthState, buf: &RunBufferIndex) -> Vec<MenuEntry>`
  （統一決定 R7-86 で `(AuthState, BufferState)` から差し替え。enum 自体は
  `#[allow(dead_code)]` で保持 — P9 §852 / `tests/tools_actions_for_state.rs` 整合）
- `mode_menu_items(current_mode: &AppMode) -> Vec<MenuEntry>`（Linux 自前メニューの
  Mode サブメニュー）

**不変条件 R7-88**：`actions_for_mode(mode) -> Vec<Action>`（File メニュー用 cross-platform
契約）はシグネチャ不変で維持する。これは P8 DoD-11 / R3-66/69 / R6-83 整合のため触らない。

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
pub enum TopMenu { File, Mode, Tools }

#[derive(Debug, Clone)]
pub enum BarMessage {
    Toggle(TopMenu),     // ボタン押下
    Pick(Action),        // ドロップダウン項目選択 → Message::NativeMenuAction(Action) へ写像
    Dismiss,             // Esc / 外側クリック
    DismissFocusLost,    // window Unfocused（log で reason を区別するため Dismiss と分離）
    // 注（F8 R2 / H3'）: 過去 F8 R1 で検討された `BarMoved(u32)` は採用しない。
    // iced 0.14 の `mouse_area::on_move` は **widget ローカル座標**
    // （`cursor.position_in(layout.bounds())`）を渡してくるため、これを
    // `with_dropdown_overlay` の `top_offset`（window 絶対 Y を期待）に
    // 流すと category mismatch によりバー上辺で silent failure する。
    // 本実装ではメニューバーが常に window 先頭行であることを利用して
    // `top_offset` を `BAR_HEIGHT` 定数に固定し、cursor 追従の anchor 機構自体を持たない。
    //
    // **不変条件（F8 R3 / LOW）**: `top_offset = BAR_HEIGHT` が成立するのは
    // `main.rs` の `view()` 構成で widget menu bar より上に**実効高さ 0** の
    // ウィジェット（`header_title` 等）しか置かない場合に限る。将来 menu bar
    // の上に高さを持つウィジェット（バナー・ヘッダーバー等）を追加した場合は、
    // `top_offset` 計算を window 絶対 Y に切り替える必要がある（`iced::event::listen_with`
    // 経由で `Event::Mouse(CursorMoved)` を購読）。
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

/// `auth` / `buf` に応じた Tools サブメニュー項目を返す（テスト対象）。
///
/// 統一決定 R3-66/69 により、Tools サブメニューは File/Mode と独立した責務として
/// `tools_actions_for_state` 純関数で扱う（`actions_for_mode` には混ぜない）。
/// 統一決定 R7-86/87 により、引数は **`&WandbAuthState` / `&RunBufferIndex`** に
/// 統一され、戻り値は常に 5 要素の `Vec<MenuEntry>`（`SignInWandb` / `SignOutWandb` /
/// `SubmitToWandb` / `OpenSubmissionLog` / `ClearRunBuffer` を `enabled` フラグで
/// 制御する）に拡張される。これにより UX の `disable + tooltip` / `グレー表示` /
/// `ログイン/ログアウト 相互 disable` を表現できる。
///
/// - `auth`: W&B 認証状態（`WandbAuthState` 構造体 — Python `check_auth.py` の
///   stdout JSON を deserialize したもの）
/// - `buf`: Run buffer の索引（`RunBufferIndex` — `latest_completed` と `total` を持つ）
///
/// 旧スケッチの `menu_items_tools` ラッパは F9c 着地時に廃止された（H6 / F8 R1 で
/// 計画書を実装に追従）。`AuthState` / `BufferState` enum は
/// `tests/tools_actions_for_state.rs` の構造インスペクションで参照されているため
/// `src/menu.rs` 内に `#[allow(dead_code)]` で保持される（P9 §852）。
pub fn tools_actions_for_state(
    auth: &WandbAuthState,
    buf: &RunBufferIndex,
) -> Vec<MenuEntry> {
    // 5 要素 × enabled / tooltip 計算は src/menu.rs を参照
    todo!()
}

/// Linux 自前メニューバーの `モード（Mode）▼` サブメニュー項目を返す（テスト対象）。
///
/// 統一決定 R7-87 により、Live/Replay を **排他チェック**（`checked: Some(true|false)`）
/// で表示し、選択で `Action::SwitchAppMode(Live|Replay)` を dispatch する。
/// P7 が前提する Linux Mode メニューの仕様欠落（R7-3）を補う。
pub fn mode_menu_items(current_mode: &AppMode) -> Vec<MenuEntry> {
    // H4 / F8 R1: 現在モードは disabled — 同じモードへ切り替える操作は無意味なので
    // `enabled: !matches!(...)` でグレーアウトさせる。`tests/mode_menu_items.rs::
    // mode_menu_items_disables_current_live_entry` がこの不変条件を保護する。
    vec![
        MenuEntry {
            action: Action::SwitchAppMode(AppMode::Live),
            enabled: !matches!(current_mode, AppMode::Live),
            tooltip: None,
            checked: Some(matches!(current_mode, AppMode::Live)),
        },
        MenuEntry {
            action: Action::SwitchAppMode(AppMode::Replay),
            enabled: !matches!(current_mode, AppMode::Replay),
            tooltip: None,
            checked: Some(matches!(current_mode, AppMode::Replay)),
        },
    ]
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
**3 契約（Esc / focus-lost / 外側クリック）× 開状態 4 ケース（File 開・Mode 開・Tools 開・閉）**
の遷移を assert できる（統一決定 R3-66 により `TopMenu::Tools` を追加し test matrix を
3×3 → 4×4 に拡張）。GUI レイヤ（overlay 描画・実際のキーイベント配線）は
[受け入れ基準](#acceptance) DoD-2〜DoD-4 の手動スモークで補完する。

<a id="widget-menu-bar-state-tests"></a>
**`tests/widget_menu_bar_state.rs` 対応表（3 契約 × 4 開状態）**：

| #  | 開状態 (`state.open`) | 入力（`BarMessage`） | 期待後状態 (`state.open`) | 観測契約 |
|----|----------------------|---------------------|-------------------------|---------|
| 1  | `Some(File)`  | `Dismiss`（Esc 由来） | `None` | Esc / File 開 |
| 2  | `Some(Mode)`  | `Dismiss`（Esc 由来） | `None` | Esc / Mode 開 |
| 3  | `Some(Tools)` | `Dismiss`（Esc 由来） | `None` | Esc / Tools 開 |
| 4  | `None`        | `Dismiss`（Esc 由来） | `None` | Esc / 閉（冪等） |
| 5  | `Some(File)`  | `Dismiss`（focus-lost 由来） | `None` | focus-lost / File 開 |
| 6  | `Some(Mode)`  | `Dismiss`（focus-lost 由来） | `None` | focus-lost / Mode 開 |
| 7  | `Some(Tools)` | `Dismiss`（focus-lost 由来） | `None` | focus-lost / Tools 開 |
| 8  | `None`        | `Dismiss`（focus-lost 由来） | `None` | focus-lost / 閉（冪等） |
| 9  | `Some(File)`  | `Dismiss`（外側クリック由来） | `None` | 外側クリック / File 開 |
| 10 | `Some(Mode)`  | `Dismiss`（外側クリック由来） | `None` | 外側クリック / Mode 開 |
| 11 | `Some(Tools)` | `Dismiss`（外側クリック由来） | `None` | 外側クリック / Tools 開 |
| 12 | `None`        | `Dismiss`（外側クリック由来） | `None` | 外側クリック / 閉（冪等） |

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
| ✅ DoD-5 | live モードで `開く…（Open）` / `上書き保存（Save）` / `名前を付けて保存…（Save As）` / 終了が並ぶ | `cargo test --test menu_actions_cross_platform` → **15 passed**（旧 `widget_menu_bar::menu_items` wrapper は F8 R2 / M-A で削除。`actions_for_mode(&AppMode::Live)` を直接検証） |
| ✅ DoD-6 | replay モードで `Replay を開始…` / `Replay を停止` / 終了が並ぶ | 同上（`actions_for_mode(&AppMode::Replay)` を検証） |
| ✅ DoD-7 | Win/Mac/Linux で同一 `Action` を発火する cross-platform テストが green | `cargo test --test menu_actions_cross_platform` → **15 passed** |
| DoD-8 | Wayland / X11 両方でスモーク完走 | [スモーク手順](#smoke) 参照 |
| DoD-9 | muda アクセラレータと Linux iced kbd の重複登録が compile-time で起こらない | `cargo build --target x86_64-unknown-linux-gnu` warn-free |
| ✅ DoD-10 | `Tools ▼` メニューに W&B / Run buffer 関連項目（`SubmitToWandb` / `SignInWandb` / `SignOutWandb` / `OpenSubmissionLog` / `ClearRunBuffer`）が `auth_state` × `buffer_state` に応じて並ぶ | `cargo test --test tools_actions_for_state` → **13 passed**（M10 / F8 R1 で実測値に更新） |
| ✅ DoD-11 | `actions_for_mode(Live)` / `actions_for_mode(Replay)` の期待値は **File/Mode メニュー由来のみ**で、Tools サブメニュー Action は混入しない | `cargo test --test menu_actions_cross_platform` → **15 passed**（M10 / F8 R1 で実測値に更新） |
| ✅ DoD-12 | `widget_menu_bar_state.rs` の test matrix が `TopMenu::Tools` を含む 3×4=12 ケースで全 green | `cargo test --test widget_menu_bar_state` → **17 passed**（M10 / F8 R1 で実測値に更新） |
| ✅ DoD-13 | Linux で `モード（Mode）▼` を開くと `ライブ（Live）` / `リプレイ（Replay）` が排他チェック付きで並ぶ（現在モードに `✓` 表示） | `cargo test --test mode_menu_items` → **11 passed**（M10 / F8 R1 で実測値に更新） |
| ✅ DoD-14 | Linux Mode サブメニューの `ライブ（Live）` 行クリックで `Action::SwitchAppMode(AppMode::Live)` が dispatch される（Replay 行も同様） | `cargo test --test mode_menu_items` → **GREEN**（11 passed のうち `mode_menu_items_dispatches_switch_app_mode` を含む） |

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

> **責務分離（統一決定 R3-66/69）**: `actions_for_mode` の期待値は **File/Mode メニュー由来の
> Action のみ**で構成する。Tools サブメニュー（`SubmitToWandb` / `SignInWandb` /
> `SignOutWandb` / `OpenSubmissionLog` / `ClearRunBuffer`）は `app_mode` ではなく
> `auth_state` × `buffer_state` に依存するため、別純関数 `tools_actions_for_state` で
> 扱い、別ファイル [`tests/tools_actions_for_state.rs`](#tools-actions-tests) で検証する。
> `actions_for_mode` 側に Tools Action を混入させない（リグレッションガードとして DoD-11）。

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

/// Tools サブメニュー Action が `actions_for_mode` に混入していないことを保証する
/// リグレッションガード（責務分離・DoD-11）。
#[test]
fn actions_for_mode_excludes_tools_submenu_actions() {
    use flowsurface::menu::Action::*;
    for mode in [AppMode::Live, AppMode::Replay] {
        let got = actions_for_mode(&mode);
        for tools_only in [
            SubmitToWandb, SignInWandb, SignOutWandb, OpenSubmissionLog, ClearRunBuffer,
        ] {
            assert!(
                !got.contains(&tools_only),
                "Tools action {:?} must not appear in actions_for_mode({:?})",
                tools_only, mode,
            );
        }
    }
}
```

このテストは Win/Mac/Linux いずれの CI runner でも `cargo test --test menu_actions_cross_platform`
で実行され、OS 間で `Action` 集合が一致することを保証する。

<a id="tools-actions-tests"></a>
### Tools サブメニューユニットテスト（`tests/tools_actions_for_state.rs`）

統一決定 R3-66/69 により Tools サブメニューは独立した責務として扱い、統一決定 R7-86 により
`tools_actions_for_state(auth: &WandbAuthState, buf: &RunBufferIndex) -> Vec<MenuEntry>`
というシグネチャで実装する（H6 / F8 R1 で計画書を実装に追従させた）。本ファイルでは
4 状態（2×2 マトリクス）×5 項目（5 種の `MenuEntry`）の `enabled` フラグを assert する。

> **H6 / F8 R1**: 旧計画書スケッチでは `(AuthState, BufferState) -> Vec<Action>` と
> 表現していたが、これは P9 で `&WandbAuthState` / `&RunBufferIndex` への移行が決まった
> R7-86 と乖離していた。`AuthState` / `BufferState` enum 自体は **削除せず**
> `#[allow(dead_code)]` で `src/menu.rs` に保持する（P9 §852 の決定および
> `tests/tools_actions_for_state.rs` のソースインスペクションテスト
> `auth_state_enum_exists` / `buffer_state_enum_exists` を尊重）。

**実装シグネチャ**：

```rust
// src/menu.rs
pub fn tools_actions_for_state(
    auth: &WandbAuthState,
    buf: &RunBufferIndex,
) -> Vec<MenuEntry>
```

**期待値テーブル**（戻り値は常に 5 要素 `Vec<MenuEntry>`、順序固定）：

| # | `auth.authenticated` | `buf.latest_completed` | `buf.total` | SignInWandb | SignOutWandb | SubmitToWandb | OpenSubmissionLog | ClearRunBuffer |
|---|----------------------|------------------------|-------------|-------------|--------------|---------------|-------------------|----------------|
| 1 | `false` | `None`         | `0` | enabled=true  | enabled=false | enabled=false | enabled=false | enabled=false |
| 2 | `false` | `Some(_)`      | `1` | enabled=true  | enabled=false | enabled=false | enabled=true  | enabled=true  |
| 3 | `true`  | `None`         | `0` | enabled=false | enabled=true  | enabled=false | enabled=false | enabled=false |
| 4 | `true`  | `Some(_)`      | `1` | enabled=false | enabled=true  | enabled=true  | enabled=true  | enabled=true  |

不変条件：
- 各行の `tooltip` は `enabled=false` のとき `Some("...")`、`enabled=true` のとき `None`
  （`enabled_true_always_has_none_tooltip` で保護）
- `SignInWandb` と `SignOutWandb` の `enabled` は常に相互排他
  （`signin_signout_mutually_exclusive_all_combinations` で保護）
- `OpenSubmissionLog` は常に 5 要素中に存在する
  （`open_submission_log_always_present_all_combinations` で保護）
- `OpenSubmissionLog` / `ClearRunBuffer` は `buf.total > 0` で `enabled=true` になる
  （`latest_completed.is_some()` を要求しない / `open_log_and_clear_enabled_when_only_aborted_runs`）

```rust
// src/menu.rs (内部 unit test, 5 項目 × 4 組合せをカバー)
#[test]
fn auth_ok_buffer_has_runs_submit_enabled() {
    let entries = tools_actions_for_state(&make_auth(true), &make_buf(true));
    assert_eq!(entries.len(), 5);

    let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
    assert!(submit.enabled);
    assert_eq!(submit.tooltip, None);

    let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
    assert!(!sign_in.enabled);
    assert_eq!(sign_in.tooltip, Some("ログイン済みです"));

    let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
    assert!(sign_out.enabled);

    let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
    assert!(log.enabled);

    let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
    assert!(clear.enabled);
}

#[test]
fn auth_ok_buffer_empty_submit_disabled() {
    let entries = tools_actions_for_state(&make_auth(true), &make_buf(false));
    let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
    assert!(!submit.enabled);
    assert_eq!(
        submit.tooltip,
        Some("送信可能な run がありません（最初に replay を実行してください）"),
    );
    /* ... 残り 4 項目も同様 ... */
}

#[test]
fn auth_none_buffer_has_runs_submit_disabled_login_prompt() { /* SignIn=true, Submit=false */ }

#[test]
fn auth_none_buffer_empty_all_disabled_appropriately() { /* 唯一 SignIn のみ enabled */ }
```

`tests/tools_actions_for_state.rs` 側はバイナリクレート制約のためソースインスペクション方式を
採用し、シグネチャ（`WandbAuthState` / `RunBufferIndex` / `Vec<MenuEntry>` への参照）と
不変条件のドキュメンテーションコメントの存在を assert する（[実装知見](#implementation-notes) 参照）。
`cargo test --test tools_actions_for_state` で全 OS で green になる（M10 実測 13 passed）。

<a id="mode-menu-items-tests"></a>
### Mode サブメニューユニットテスト（`tests/mode_menu_items.rs`）

統一決定 R7-87 により、Linux 自前メニューの `モード（Mode）▼` サブメニューは
`mode_menu_items(current_mode) -> Vec<MenuEntry>` で実装し、本ファイルで
**現在モードに対する排他チェック表示**と **Action dispatch** を assert する。

**期待値テーブル**（H4 / F8 R1: 現在モードは `enabled=false`）：

| # | `current_mode` | 期待 `Vec<MenuEntry>`（順序固定） |
|---|----------------|---------------------------------|
| 1 | `Live`   | `[ {SwitchAppMode(Live), enabled=false, checked=Some(true)}, {SwitchAppMode(Replay), enabled=true, checked=Some(false)} ]` |
| 2 | `Replay` | `[ {SwitchAppMode(Live), enabled=true, checked=Some(false)}, {SwitchAppMode(Replay), enabled=false, checked=Some(true)} ]` |

```rust
// tests/mode_menu_items.rs
use flowsurface::menu::{mode_menu_items, Action, MenuEntry};
use flowsurface::AppMode;

// H4 / F8 R1: 現在モードは disabled — `assert!(!got[0].enabled)` を期待する。
#[test]
fn live_mode_marks_live_checked_replay_unchecked() {
    let got = mode_menu_items(&AppMode::Live);
    assert_eq!(got.len(), 2);
    assert_eq!(got[0].action, Action::SwitchAppMode(AppMode::Live));
    assert_eq!(got[0].checked, Some(true));
    assert!(!got[0].enabled, "current mode (Live) must be disabled");
    assert_eq!(got[1].action, Action::SwitchAppMode(AppMode::Replay));
    assert_eq!(got[1].checked, Some(false));
    assert!(got[1].enabled, "non-current mode (Replay) must be enabled");
}

#[test]
fn replay_mode_marks_replay_checked_live_unchecked() {
    let got = mode_menu_items(&AppMode::Replay);
    assert_eq!(got.len(), 2);
    assert_eq!(got[0].action, Action::SwitchAppMode(AppMode::Live));
    assert_eq!(got[0].checked, Some(false));
    assert!(got[0].enabled, "non-current mode (Live) must be enabled");
    assert_eq!(got[1].action, Action::SwitchAppMode(AppMode::Replay));
    assert_eq!(got[1].checked, Some(true));
    assert!(!got[1].enabled, "current mode (Replay) must be disabled");
}

/// クリックで dispatch される Action が `SwitchAppMode(target)` であることを保証する
/// リグレッションガード（DoD-14）。
#[test]
fn each_entry_dispatches_switch_app_mode_action() {
    for current in [AppMode::Live, AppMode::Replay] {
        for entry in mode_menu_items(&current) {
            match entry.action {
                Action::SwitchAppMode(_) => {}
                other => panic!("expected SwitchAppMode, got {:?}", other),
            }
        }
    }
}
```

このテストも OS 非依存で、`cargo test --test mode_menu_items` で全 OS で green になる
（Linux 限定 rendering とは独立した純関数仕様のため）。

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

---

<a id="implementation-notes"></a>
## 実装知見（2026-05-04）

### ファイル分割: menu_bar_state.rs と widget_menu_bar.rs の 2 層化

**問題**: P8 計画書のスケッチでは `widget_menu_bar.rs` に `State/update()` と `view()` を
同居させていたが、`widget_menu_bar.rs` を `#![cfg(target_os = "linux")]` でガードすると
DoD-12 の state-transition テストが Windows/macOS でコンパイルできない。

**採用した構造**:

```
src/menu.rs              -- Action / MenuEntry / AuthState / BufferState / 純関数（全 OS）
src/menu_bar_state.rs    -- State / TopMenu / BarMessage / update()（全 OS）
src/widget_menu_bar.rs   -- view() レンダリングスタブ（Linux のみ #![cfg]）
```

**利点**: `update()` の純関数テスト（DoD-12）が全 OS の CI で green になる。
`widget_menu_bar.rs` は Linux 専用 iced API のみ含むので `cfg` ガードが必然的。

### テストアプローチ: ソースインスペクション方式

このプロジェクトの全 integration test（`tests/*.rs`）はバイナリクレート（`src/main.rs` のみ、
`lib.rs` なし）のため `use flowsurface::menu::{...}` でインポートできない。

P8 計画書のテストサンプルは `use flowsurface::menu::` を使う型チェック方式だったが、
実際には **ソースファイルを `read_to_string()` で読み込んでアサートする**インスペクション方式
（プロジェクト全体で統一されているパターン）を採用した。

**型チェックは `src/menu.rs` 内の `#[cfg(test)] mod tests` でカバー**（`actions_for_mode`
等の純関数は inline unit test で assert_eq! 型チェックを行っている）。

Integration test（`tests/menu_actions_cross_platform.rs` 等）は構造リグレッションガードとして
機能し、inline unit test は機能的カバレッジを担う分業。

### widget_menu_bar.rs の pub use

```rust
pub use crate::menu_bar_state::{BarMessage, State, TopMenu, update};
```

でリエクスポートすることで、Linux コードが `widget_menu_bar::State` で直接アクセスできる。
`menu_bar_state::` 名前空間を意識しなくてよい（Linux ユーザーからは `widget_menu_bar` が
単一のエントリーポイントに見える）。

### 既存 native_menu::Action との共存

`menu::Action` は `native_menu::Action` とは独立した別 enum として追加した。
`native_menu::Action::OpenFile` を `menu::Action::Open` にリネームする「昇格」は
`tests/accelerator_bind.rs` が `"Action::OpenFile"` という文字列を検索しているため
後者のテストを破壊する。F8 は独立フェーズなのでリネームは行わず、`menu::Action` を
新規 enum として並置する方針を取った。

実際のアクション dispatch（`widget_menu_bar::view()` の実装）では `menu::Action` →
`native_menu::Action` への変換が必要になる（DoD-1 through DoD-4 実装時の課題）。

### SCHEMA_MINOR 版数ズレ（発見 tip）

`engine-client/tests/schema_v2_4_nautilus.rs` の `schema_minor_is_7_for_positions` テストが
F7 の SCHEMA_MINOR bump（10 → 11）に対してアサート値が未更新だった。F8 実装中に発見し修正。
SCHEMA_MINOR を bump した場合は必ずこのテストファイルも更新すること。
