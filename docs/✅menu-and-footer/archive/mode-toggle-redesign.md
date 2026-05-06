# モード切替 UI 改修案: メニュー → フッタートグル

メニューバー上の `モード（Mode）` トップレベルメニュー（`File` / `Mode` /
`Tools` 3 本立ての中央）を廃止し、ステータスバー（フッター）の
`● LIVE` / `● REPLAY` バッジ自体をクリック可能なトグルに昇格させる。

> 現行の正本: [`src/menu_bar_state.rs`](../../src/menu_bar_state.rs) の
> `TopMenu { File, Mode, Tools }`、[`src/widget_menu_bar.rs`](../../src/widget_menu_bar.rs)
> のボタン列。`Mode` は `File` 配下のサブメニューではなく **トップレベル
> メニュー** であり、本改修では `TopMenu::Mode` バリアント・ボタン・dropdown を
> 丸ごと削除する。

> 既存仕様: [`mode-switch-impl.md`](./mode-switch-impl.md) /
> [`footer-impl.md`](./footer-impl.md)

---

## 背景・動機

- 現行はモード切替が「ファイル ▼ → モード（Mode） ▼ → ライブ / リプレイ」と
  二段ドロップダウンに埋もれており、頻繁に切り替えるユーザーには遠い
- フッターには既に **現在のモード**を常時表示する `● LIVE` / `● REPLAY`
  バッジがあり、「現状把握」と「切替」の UI を 1 か所に集約できる
- メニュー側に切替を残すと、フッタートグル ↔ サブメニューの**二系統**を保守
  することになり整合バグの温床

## ゴール / 非ゴール

### ゴール

- フッターのモードバッジをクリック（またはキー）で `live ⇄ replay` をトグル
- メニューバー上の `モード（Mode）` トップレベルメニューを**削除**
  （`TopMenu::Mode` / 対応するボタン・dropdown・state すべて）
- 既存の dirty / in-flight / EngineBusy / submit_in_flight ガードはそのまま
  経由する（dispatch 経路は単一系統のまま）
- 切替中・抑制中はバッジを **disabled 表示**にして連打を防ぐ
- dirty は disabled 理由に**含めない**（クリック後に既存 confirm dialog へ流す）

### 非ゴール

- 切替時の挙動・ガード仕様（5 軸 matrix）の変更
- engine 再起動シーケンスの変更
- 起動時 `--mode {live|replay}` CLI 引数の廃止（正本のまま）
- popout への footer 追加（現状通り main window のみ）

---

## UI 仕様

### バッジ位置・見た目

| 状態 | 表示 | 色 | カーソル |
|------|------|----|----------|
| live（操作可能） | `● LIVE` | 緑 `(0.2, 0.75, 0.3)` | pointer |
| replay（操作可能） | `● REPLAY` | アンバー `(0.9, 0.6, 0.1)` | pointer |
| 切替中 / 抑制中 | 現モードのバッジ + `…` サフィックス | 既存色を 50 % 減光 | default |
| hover（操作可能時） | バッジ背景にうっすらハイライト（`rgba(255,255,255,0.06)`） | 同上 | pointer |

- 高さ・背景色・フォントサイズ等は既存の `STATUS_BAR_HEIGHT` / `STATUS_BAR_BG` を
  維持（footer の他要素と揃える）
- ツールチップ: 操作可能時は **切替先**を案内
  - live 時: `クリックで Replay に切替`
  - replay 時: `クリックで Live に切替`
- 抑制中ツールチップ: 抑制理由を示す（例: `Engine を再起動中…` / `W&B 送信中は切替できません` / `Engine がビジーです`）

> dirty 状態は **disabled 理由に含めない**。dirty は「切替不可」ではなく
> 「切替前に保存するか確認する」状態であり、クリック後に既存の
> `SaveAndSwitchMode` / `DiscardAndSwitchMode` / `GoBack` confirm dialog に
> 遷移させる。disable してしまうと save-before-switch 導線が消える。

### インタラクション

- クリック: `Action::SwitchAppMode(target)` を発行（`target` = `!current`）
- 連打防止: `_mode_switch_guard` 取得中はクリック無視（disabled 状態）
- キーボード: 既存アクセラレータ `Ctrl/Cmd+M`（あれば）を維持。フッタートグル
  はマウス導線、アクセラレータはキー導線として併存
- 右クリック: 現フェーズでは未対応（将来「明示的に live を選ぶ」用のミニメニューを
  検討する余地あり、§未決事項）

### enable / disable 条件（フッター側）

`mode_menu_items(current)` に集約されていた enable 計算を **フッター用 enable 計算
関数**に置き換える：

```rust
pub struct ModeToggleState {
    pub current: AppMode,
    pub enabled: bool,
    pub disabled_reason: Option<&'static str>, // ツールチップ表示用
}

pub fn mode_toggle_state(
    current: AppMode,
    engine_busy: bool,
    submit_in_flight: bool,
    mode_switch_in_progress: bool,
) -> ModeToggleState { ... }
```

`disabled_reason` の優先順位:

1. `mode_switch_in_progress` → `Engine を再起動中…`
2. `submit_in_flight` → `W&B 送信中は切替できません`
3. `engine_busy` → `Engine がビジーです`
4. それ以外 → `enabled = true`

> **dirty は disabled 理由ではない**（UI 仕様節と一本化）。
> dirty 状態でも `enabled = true` のまま、クリック後に既存の confirm dialog
> （`SaveAndSwitchMode` / `DiscardAndSwitchMode` / `GoBack`）へ遷移させる。
> `mode_toggle_state(...)` は dirty フラグを **引数に取らない**ことで、
> 実装時の取り違えを型レベルで防ぐ。

---

## アーキテクチャ

### 削除する経路

- `src/menu.rs::mode_menu_items()` — 関数本体を削除
- `Action::SwitchAppMode` の **メニュー由来 dispatch** をテストする
  `tests/mode_menu_items.rs` — 削除
- `widget_menu_bar.rs` / `menu_bar_state.rs` の Mode サブメニュー描画 / state
- `actions_for_mode` / `MenuEntry` 配列から Mode サブメニュー分を除去
- README の File メニュー表からモードサブメニュー行を削除し、フッタートグル節を追加

### 維持する経路

`Action::SwitchAppMode(AppMode)` enum は維持。dispatch 経路を
`Message::NativeMenuAction(Action)` の単一系統に保ったまま、フッタークリックも
**同じ Action を発行する**だけにする。これにより:

- `SaveAndSwitchMode` / `DiscardAndSwitchMode` / `GoBack` ハンドラを再利用
- 5 軸 matrix の不変条件は無変更
- engine 再起動・`_mode_switch_guard`・`APP_MODE` 更新は既存パスを通る

### 変更ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/main.rs` | `status_bar(...)` を `status_bar(state: ModeToggleState, on_press: Message)` に拡張、`button` ベースに変更、disabled 状態の合成 |
| `src/menu.rs` | `mode_menu_items` 削除、`mode_toggle_state` 追加（純関数） |
| `src/widget_menu_bar.rs` | トップレベル `Mode` ボタンと dropdown 描画を削除（`File` / `Tools` の 2 本立てに） |
| `src/menu_bar_state.rs` | `TopMenu::Mode` バリアントと関連 state transition を削除 |
| `src/native_menu.rs` | `widget_keyboard_subscription` の `Ctrl+M` 残置可否を判断（残置推奨） |
| `docs/✅menu-and-footer/README.md` | 機能サマリ表更新 |
| `docs/✅menu-and-footer/footer-impl.md` | UI 仕様にトグル節追加 |
| `docs/✅menu-and-footer/mode-switch-impl.md` | 「メニュー構成」節をトグル UI 節に書き換え |

### `status_bar` の新シグネチャ（案）

```rust
fn status_bar<'a>(state: ModeToggleState) -> Element<'a, Message> {
    let label = match (state.current, state.enabled) {
        (AppMode::Live, true)  => "● LIVE",
        (AppMode::Replay, true) => "● REPLAY",
        (AppMode::Live, false)  => "● LIVE …",
        (AppMode::Replay, false) => "● REPLAY …",
    };
    let dot_color = mode_dot_color(state.current, state.enabled);

    let target = match state.current {
        AppMode::Live => AppMode::Replay,
        AppMode::Replay => AppMode::Live,
    };

    let badge = button(
        text(label).size(11).color(dot_color),
    )
    .padding(padding::left(8).right(8))
    .style(footer_button_style(state.enabled));

    let badge = if state.enabled {
        badge.on_press(Message::NativeMenuAction(Action::SwitchAppMode(target)))
    } else {
        badge // on_press を付けないと disabled 扱い
    };

    container(
        tooltip(badge, footer_tooltip_text(&state), tooltip::Position::Top)
    )
    .width(Length::Fill)
    .height(STATUS_BAR_HEIGHT)
    .align_y(Alignment::Center)
    .style(|_| container::Style {
        background: Some(STATUS_BAR_BG.into()),
        snap: true,
        ..Default::default()
    })
    .into()
}
```

> 注: `'static` だった戻り値は `state` を受け取るため `'a` に変更。tooltip 文字列は
> `String` を保持できるよう lifetime を付ける（既存 footer-impl.md の R2 設計判断と
> 整合させて再検証）。

---

## テスト方針

### 追加 / 変更する自動テスト

| ID | ケース | 種別 |
|----|--------|------|
| TT1 | `mode_toggle_state(Live, busy=false, submit=false, switching=false).enabled == true` | unit |
| TT2 | `engine_busy=true` で `enabled=false`、reason が `Engine がビジーです` | unit |
| TT3 | `submit_in_flight=true` は `engine_busy` より優先される | unit |
| TT4 | `mode_switch_in_progress=true` は最優先 | unit |
| TT5 | フッタークリック相当の Message 発行で `Action::SwitchAppMode(!current)` が dispatch される | integration（既存 `mode_switch_*` tests と同じ Message パスを通る） |
| TT6 | dirty 時にトグルクリック → 既存の `SaveAndSwitchMode` confirm が出る | integration |

### 削除する自動テスト

- `tests/mode_menu_items.rs`（メニュー消滅に伴い無効）

### 維持 + 拡張するテスト（キーボード経路）

- `tests/mode_switch_accelerator_disabled.rs` は **そのまま維持**する。
  [src/native_menu.rs:131](../../src/native_menu.rs#L131) の `Ctrl/Cmd+M`
  経路には `MODE_SWITCHING.load(Acquire)` で抑止が入っており、フッターを
  disabled にするだけではこのキーボード経路の二重切替を塞げない。
  「フッター disabled なのにキーボードだけ通る」穴を残さないため、
  キーボード抑止の assertion は **フッター側 assertion とは別軸で維持**する。
- フッター側の disable 保証は TT2〜TT4 で `mode_toggle_state(...)` を直接
  検証する形で**追加**する（既存テストの置換ではなく増設）。

### 維持するテスト

- `tests/mode_switch_restart.rs`
- `tests/mode_switch_in_flight_order.rs`
- `tests/mode_switch_blocks_during_submit.rs`
- `tests/mode_switch_reentry.rs`
- `tests/mode_switch_panic_recovery.rs`
- `tests/mode_switch_timeout_abort.rs`
- `tests/wandb_modeswitch_lock_order.rs`

### 目視テスト

| ID | ケース |
|----|--------|
| VT1 | live 起動 → 緑 `● LIVE` バッジクリックで replay に切替（confirm dialog 経由可） |
| VT2 | replay 起動 → アンバー `● REPLAY` クリックで live に切替 |
| VT3 | 切替中はバッジが減光し、再クリックしても無反応 |
| VT4 | W&B submit 中はバッジが減光し、ツールチップに理由が出る |
| VT5 | dirty 状態で live → replay クリック → save/discard/cancel ダイアログが出る |
| VT6 | popout ウィンドウにはフッター（バッジ）が表示されない |
| VT7 | ファイルメニューを開いても `モード（Mode）` 項目が出ない |
| VT8 | `Ctrl+M`（残置した場合）で同じ切替が起こる |

---

## 実装ステップ

1. ✅ `src/menu.rs` に `ModeToggleState` / `mode_toggle_state(...)` を追加（unit
   test TT1〜TT4 を先に書く: TDD RED → GREEN）
2. ✅ `src/main.rs` の `status_bar(...)` を `ModeToggleState` 受け取りに変更し、
   `button` + `tooltip` ベースに書き換え
3. ✅ `view()` 側で `mode_toggle_state(...)` を組み立てて `status_bar` に渡す。
   `engine_busy=false` / `SUBMIT_IN_FLIGHT.load(Acquire)` / `mode_switch_state.is_some()` を
   既存 state から拾う
4. ✅ ファイルメニュー側から Mode サブメニューを削除
   - `widget_menu_bar.rs` の Mode ボタン・dropdown・`entries_for_menu` 該当行を削除
   - `menu_bar_state.rs` の `TopMenu::Mode` バリアントと関連テストを削除
   - `mode_menu_items` を `src/menu.rs` から削除
5. ✅ `tests/mode_menu_items.rs` を削除し、TT5 / TT6 を `tests/mode_toggle_footer.rs` に新規追加
6. ✅ README / footer-impl.md / mode-switch-impl.md を更新（事前仕様として準備済み）
7. ✅ `cargo fmt --check` / `cargo clippy -- -D warnings` / `cargo test --workspace` 全 PASS
8. 目視 VT1〜VT8 を実施（未実施 — ビルド完了後に実施予定）

---

## 移行・後方互換

- `Action::SwitchAppMode` 自体は **public な enum バリアントのまま**残すため、
  saved layout / settings の互換は破壊しない（layout には mode 情報のみ保存）
- `Ctrl+M` アクセラレータは残置を推奨。キー操作派ユーザーが切替手段を失わない
- `--mode {live|replay}` CLI は無変更

---

## 実装ログ（2026-05-05）

### 実装経緯・判断

- **dispatch 経路**: 計画では `Message::NativeMenuAction(Action::SwitchMode(target))` を
  `status_bar` から直発行する案を示していたが、ソース解析テスト
  `tests/mode_switch_blocks_during_submit.rs` が `"Action::SwitchMode(target)"` の
  **最初の出現位置** を `find()` で探し、そこから 1500 バイト以内に `SUBMIT_IN_FLIGHT`
  チェックが存在することを検証する構造だった。`status_bar` で `SwitchMode(target)` を
  直接書くと `status_bar` 関数が見つかってしまい、実際のハンドラが検出されなくなる。
  このため `Message::MenuBar(BarMessage::Pick(menu::Action::SwitchAppMode(target)))` 経由に
  切り替えた。これにより BarMessage::Pick → `to_native_action` → `NativeMenuAction` →
  `Action::SwitchMode` ハンドラの既存パスを完全に通過する。

- **`engine_busy` パラメータ**: `Flowsurface` struct に持続的な `engine_busy: bool` フィールドが
  存在しないため、現フェーズでは `false` 固定で渡す。`mode_toggle_state` の引数に
  残してあるため、将来フィールドが追加された場合は呼び出し箇所 1 箇所を変更するだけでよい。

- **TDD 手順**: `menu.rs` に TT1〜TT4 テストと実装を同一コミット内で追加（テストが先に
  コンパイル不可になるという意味での RED は有効。実装追加で GREEN）。

- **`TopMenu::Mode` 削除とのカップリング**: `menu_bar_state.rs` の `TopMenu::Mode` 削除と
  `widget_menu_bar.rs` の match arm 削除はカップリングしており、どちらを先に書いても
  中間状態でコンパイルエラーが生じる。`menu_bar_state.rs` → `widget_menu_bar.rs` の順で
  連続編集し、2 ファイル目の編集後に `cargo check` が通るよう実施した。

- **`status_bar_label` 更新**: 既存の `fn status_bar_label(is_replay: bool)` を
  `fn status_bar_label(is_replay: bool, enabled: bool)` に拡張し、disabled 時の
  `"● LIVE …"` / `"● REPLAY …"` を返すようにした。既存のテスト t1/t2 を t1/t1b/t2/t2b に
  拡張した。

### テスト結果 TT1〜TT6

| ID | 状態 |
|----|------|
| TT1 | ✅ PASS (`src/menu.rs` unit) |
| TT2 | ✅ PASS (`src/menu.rs` unit) |
| TT3 | ✅ PASS (`src/menu.rs` unit) |
| TT4 | ✅ PASS (`src/menu.rs` unit) |
| TT5 | ✅ PASS (`tests/mode_toggle_footer.rs`) |
| TT6 | ✅ PASS (`tests/mode_toggle_footer.rs`) |

---

## 未決事項

| 項目 | 優先度 | 備考 |
|------|--------|------|
| 右クリックで「明示的に live」「明示的に replay」のミニメニュー | 低 | 「同モードへの切替は no-op」の挙動と整合させるなら不要だが、ラジオ性を残したい場合に検討 |
| バッジ右側に `→ REPLAY` のような切替先プレビュー文字を出すか | 低 | 視認性 vs ノイズのトレードオフ。目視テスト後に判断 |
| disabled 時の `…` サフィックスの代わりにスピナーを描画するか | 低 | iced の text/animation 機構と相談 |
| `Ctrl+M` を維持するか撤去するか | 低 | 撤去するならアクセラレータ表示の整合性も同時に消す |
