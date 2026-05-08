---
title: iced_test による UI テスト
status: active
authored: 2026-05-08
---

# iced_test による UI テスト

`iced_test` は iced 公式のヘッドレステストクレートで、ウィジェットツリーを
実際に描画せずに操作・検証できる。GitHub Issue で報告されたバグを再現する
テストを先に書き（red）、修正してから通す（green）TDD ループに使う。

## できること / できないこと

| 操作 | API | 備考 |
|------|-----|------|
| ウィジェット検索 | `selector::text("...")` | 表示テキストで選択 |
| クリック | `sim.click(selector)` | `on_press` が発火 |
| キー入力 | `sim.tap_key(key)` | `Key::Named(Named::Tab)` 等 |
| 文字入力 | `sim.typewrite("...")` | テキストフィールドへ |
| スナップショット | `sim.snapshot()` | 文字列表現で比較 |
| 実際の描画確認 | - | ヘッドレスなので不可 |
| GPU/wgpu の挙動 | - | レンダラは使われない |

## バイナリクレートの制約と対策

flowsurface はバイナリクレートのため、`tests/` から `src/` の private な型を
直接インポートできない。`iced_test` を活用するには **view/update ロジックを
テスト可能な単位に切り出す** 設計が必要になる。

```
src/
  menu_bar_state.rs   ← pub fn update() / pub struct State を切り出し済み
  widget_menu_bar.rs  ← pub fn view() を返す — simulator に渡せる
```

すでに `pub fn update(state: State, msg: BarMessage) -> State` のような
純粋関数として切り出してあるモジュールは即テスト可能。新しくウィジェットを
追加する際も同パターンで設計する。

## GitHub Issue でのバグ修正ワークフロー

```
1. Issue を読んで再現手順を把握する
2. iced_test で再現テストを書く  → cargo test で RED を確認
3. 最小修正で通す               → cargo test で GREEN を確認
4. /bug-postmortem を実行       → 「なぜ既存テストで取れなかったか」を記録
```

### ステップ 1: 再現テストを書く（RED）

```rust
// tests/regression_menu_dismiss_on_esc.rs
//
// Issue #NNN: Esc キーでメニューが閉じない
// 再現: File メニューを開いた状態で Esc を押すと open が Some のまま残る

use iced::keyboard::{Key, Named};
use iced_test::selector;

// view() が pub で切り出されている場合の例
// use flowsurface::widget_menu_bar;  ← バイナリクレートなので直接は不可
//
// 代わりに: pub(crate) な view を lib.rs 経由で re-export するか、
// または menu_bar_state の pure function だけをテストする

use flowsurface_menu_bar_state::{BarMessage, State, TopMenu, update};
// ↑ menu_bar_state を独立クレートに切り出した場合

#[test]
fn esc_closes_open_menu() {
    let state = State { open: Some(TopMenu::File), ..State::default() };
    let next = update(state, BarMessage::Dismiss);
    assert_eq!(next.open, None, "Esc should close the open menu");
}
```

> バイナリクレートから `iced_test::simulator` を使う場合は、view 関数を
> テスト専用の小さなモジュールとして切り出す。既存の `menu_bar_state.rs` は
> その形になっており、この形式の pure-function テストなら今すぐ書ける。

### ステップ 2: simulator を使った UI 操作テスト

view 関数が `pub` で切り出せるモジュールを作った場合の例：

```rust
// tests/regression_counter_button.rs
//
// Issue #NNN: + ボタンを押しても値が増えない
// 対象: src/counter.rs の CounterView

use iced_test::{simulator, selector};

// counter モジュールが切り出されている前提
mod counter {
    pub use flowsurface_counter::{Counter, CounterMsg};
}

#[test]
fn plus_button_increments_value() {
    let model = counter::Counter { value: 0 };
    let mut sim = simulator(model.view());

    // "+" ボタンをクリック
    let messages = sim.click(selector::text("+")).unwrap();

    assert!(
        messages.iter().any(|m| matches!(m, counter::CounterMsg::Increment)),
        "+ ボタンは Increment メッセージを発行しなければならない"
    );
}

#[test]
fn value_updates_after_increment() {
    let mut model = counter::Counter { value: 3 };

    // update を適用して状態遷移を確認
    model.update(counter::CounterMsg::Increment);
    assert_eq!(model.value, 4);
}
```

### ステップ 3: snapshot テストで退行を防ぐ

```rust
#[test]
fn footer_snapshot_in_replay_mode() {
    let model = Footer::new(AppMode::Replay);
    let mut sim = simulator(model.view());

    // 初回: snapshot を文字列として保存
    insta::assert_snapshot!(sim.snapshot());
    // ↑ insta クレートと組み合わせると便利（dev-dependency に追加が必要）
}
```

## 既存テストとの使い分け

| テスト種別 | 適したケース |
|------------|------------|
| ソースインスペクション（既存パターン）| enum の網羅性・型の存在・関数シグネチャの検証 |
| `iced_test::simulator` | ボタン操作 → Message の検証・ウィジェットの存在確認 |
| pure function ユニットテスト | `update()` の状態遷移（`menu_bar_state::update` 等）|
| Python E2E（`ReplaySession`）| アプリ全体の起動・IPC 境界をまたぐフロー |

ソースインスペクションテストは「コードの形」を確認するもので、
`iced_test` は「操作したときの振る舞い」を確認するもの。
どちらか片方に寄せず、役割に応じて使い分ける。

## セットアップ確認

`iced_test` は `Cargo.toml` の `[dev-dependencies]` に追加済み：

```toml
[dev-dependencies]
iced_test = "0.14"
```

テストを書いてすぐ実行できる：

```bash
cargo test --test <テストファイル名>
cargo test regression   # regression_ prefix の全テストを実行
```

## 新しいウィジェットを追加するときの規約

`iced_test` でテスト可能にするために、以下を守る：

1. **view 関数を分離する** — `fn view(state: &State) -> Element<'_, Msg>` を
   スタンドアロン関数として切り出す（クロージャで state を捕捉しない）
2. **update 関数を pure にする** — `fn update(state: State, msg: Msg) -> State`
   の形にすると simulator を介さずにも直接ユニットテストできる
3. **Message を `Debug + PartialEq` にする** — `assert_eq!` でメッセージを比較できる

```rust
// GOOD — テストしやすい設計
pub fn view(state: &FooterState) -> Element<'_, FooterMsg> { ... }
pub fn update(state: FooterState, msg: FooterMsg) -> FooterState { ... }

// テスト側
let mut sim = simulator(view(&initial_state));
let msgs = sim.click(selector::text("Live")).unwrap();
assert!(msgs.contains(&FooterMsg::SwitchToLive));
```

## 参考リンク

- [iced_test — docs.iced.rs](https://docs.iced.rs/iced_test/index.html)
- [crates.io/crates/iced_test](https://crates.io/crates/iced_test)
- [Headless Mode Testing PR #2698](https://github.com/iced-rs/iced/pull/2698)
- [テスト戦略全体](strategy.md)
- [コーディング規約](../contributing/coding-standards.md)
