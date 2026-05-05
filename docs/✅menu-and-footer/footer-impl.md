# ステータスバー（フッター）追加: 仕様

## ゴール

メインウィンドウ最下部に **ステータスバー** を固定表示し、
現在の起動モード（`LIVE` / `REPLAY`）を常時視認できるようにする。

将来的に「接続状態」「タイムゾーン」「バージョン」などの情報を同じバーに
追加できる拡張ポイントとして設計する。

---

## スコープ

### 含むもの

- メインウィンドウのフッターバー（高さ固定、幅 Fill）
- `● LIVE` / `● REPLAY` モードバッジ — クリックでモードをトグルする `button`
- バッジの disabled 状態（切替中 / `engine_busy` / `submit_in_flight` 時）と
  ツールチップ（操作可能時は切替先案内、抑制中は抑制理由）
- popout ウィンドウにはフッターを**表示しない**

### 含まないもの

- dirty によるバッジ disable（dirty はクリック後の confirm dialog に委ねる）
- 右クリックによるミニメニュー（将来フェーズ）
- 接続状態・タイムゾーン・バージョン表示（将来フェーズ）
- 設定・テーマによるフッター非表示トグル
- テーマシステム連携（現フェーズは固定色。将来 `style::*` 関数経由に移行）

---

## UI 仕様

```
┌─────────────────────────────────────────────────────────┐
│  sidebar │               dashboard                       │
│          │                                               │
│          │                                               │
├──────────┴───────────────────────────────────────────────┤
│  ● LIVE  ← クリックでトグル                               │ ← ステータスバー
└─────────────────────────────────────────────────────────┘
```

| 項目 | 詳細 |
|------|------|
| 高さ | 20 px（固定） |
| 背景色 | `Color::from_rgb(0.08, 0.08, 0.08)`（既存テーマより少し暗め） |
| バッジ位置 | 左端（padding left 8 px） |
| バッジ文字（操作可能） | `● LIVE` / `● REPLAY`（● はドット文字 U+25CF） |
| バッジ文字（抑制中） | `● LIVE …` / `● REPLAY …` |
| LIVE 色（操作可能） | `Color::from_rgb(0.2, 0.75, 0.3)`（緑） |
| REPLAY 色（操作可能） | `Color::from_rgb(0.9, 0.6, 0.1)`（アンバー） |
| 抑制中の色 | 各色を 50 % 減光 |
| フォントサイズ | 11 px |
| カーソル（操作可能） | pointer |
| カーソル（抑制中） | default |
| ツールチップ | 操作可能: 切替先案内 / 抑制中: 抑制理由 |

---

## アーキテクチャ

### 合成方針（統一決定 — Round 2 C2 を採用）

`status_bar` は `view_with_modal` に渡す `base` の **内側** に push する。
モーダル展開中は `view_with_modal` の opaque overlay が画面全体を覆うため、
フッターはオーバーレイの下に隠れる。これは**意図的なトレードオフ**：

- Settings / Theme / Network 等の下寄せメニューの配置基準（base 全体の bounds）を
  変更しない
- モーダル展開中はユーザーがモーダル操作に集中する文脈であり、フッターの
  視認性を一時的に失っても支障がない
- `column![modal_or_base, status_bar]` のように外側で合成する案は、下寄せ
  モーダルが status_bar 高さ分（20 px）ずれるため**採用しない**

> **Round 1 統一決定との関係**: Round 1 で「base に push、active_menu 分岐の前」と
> 決定し、Round 2 C2 で「モーダル中に隠れるのは意図的動作」と確定済み。
> 本書はこの決定に従う唯一の source of truth とする。

### フッター固定方針

ダッシュボード行（`row![sidebar, dashboard]`）に `.height(Length::Fill)` を
付けてビューポートの残余領域を埋め、フッターが常にウィンドウ下端に位置する
ようにする。バナーが展開されると `row` が縮小し、フッターは下端に維持される。

### Toast との重なり（意図的トレードオフ）

`toast::Manager::new(content, ...)` は content 全体に overlay を載せる
（[src/main.rs:3175](../../../src/main.rs#L3175), [src/widget/toast.rs:310](../../../src/widget/toast.rs#L310)）。
本フェーズでは `content` に footer を含めた状態で toast を貼るため、
**toast がフッターに重なる可能性がある**。これも意図的トレードオフとし、
inset 調整は将来フェーズに繰り越す（§未決事項参照）。

理由：

- toast は既に sidebar 側の Start / End にアラインメントされており、画面下端に
  並ぶケースが少ない
- inset を入れる場合は `toast::Manager` のシグネチャ変更が必要で、本フェーズの
  スコープを超える
- 重なる時間は通知が autodismiss するまでの数秒であり、運用影響は小さい

### 変更ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/main.rs` | `status_bar(ModeToggleState)` に変更（`button` + `tooltip` ベース）/ `view()` から `mode_toggle_state(...)` を組み立てて渡す |
| `src/menu.rs` | `ModeToggleState` / `mode_toggle_state(...)` を追加、`mode_menu_items` を削除 |

### `fn view()` の変更点

main window ブロック（`id == self.main_window.id`）の `base` 構築を以下に変更：

```rust
let mut base = column![header_title];
if let Some(banner) = banner {
    base = base.push(container(banner).padding(padding::all(8)));
}
if let Some(err_msg) = &self.strategy_load_error {
    base = base.push(/* strategy_err_banner */);
}
base = base.push(
    match sidebar_pos {
        sidebar::Position::Left => row![sidebar_view, dashboard_view],
        sidebar::Position::Right => row![dashboard_view, sidebar_view],
    }
    .spacing(4)
    .padding(8)
    .height(Length::Fill),
);
// ModeToggleState を組み立てて status_bar へ渡す
let toggle_state = mode_toggle_state(
    current_mode,
    self.engine_busy,
    self.submit_in_flight,
    self._mode_switch_guard.is_some(),
);
base = base.push(status_bar(toggle_state));  // button + tooltip ベースのトグル

if let Some(menu) = self.sidebar.active_menu() {
    self.view_with_modal(base.into(), dashboard, menu)
} else {
    base.into()
}
```

> **実装注意 1**: `.height(Length::Fill)` は `match` 式が返す `row!` に対する
> メソッドチェーンの末尾に追加する。`.spacing(4).padding(8).height(Length::Fill)`
> の順で連結すること。
>
> **実装注意 2**: `status_bar` の push は popout 側（`else` ブロック）には**追加しない**。
> popout は `id == self.main_window.id` の `if` ブロック内でのみ footer を持つ。

### `status_bar` 要素の実装

`is_replay: bool` ベースから `ModeToggleState` ベースに変更。
詳細シグネチャは [`mode-toggle-redesign.md §status_bar の新シグネチャ`](./mode-toggle-redesign.md#status_bar-の新シグネチャ案) 参照。

```rust
const STATUS_BAR_HEIGHT: u32 = 20;
const STATUS_BAR_BG: Color = Color::from_rgb(0.08, 0.08, 0.08);

fn status_bar<'a>(state: ModeToggleState) -> Element<'a, Message> {
    // button + tooltip ベース。on_press を付けないと iced が disabled 扱いにする
    let label = match (state.current, state.enabled) {
        (AppMode::Live, true)    => "● LIVE",
        (AppMode::Replay, true)  => "● REPLAY",
        (AppMode::Live, false)   => "● LIVE …",
        (AppMode::Replay, false) => "● REPLAY …",
    };
    let dot_color = mode_dot_color(state.current, state.enabled);
    let target = match state.current {
        AppMode::Live => AppMode::Replay,
        AppMode::Replay => AppMode::Live,
    };
    let badge = button(text(label).size(11).color(dot_color))
        .padding(padding::left(8).right(8))
        .style(footer_button_style(state.enabled));
    let badge = if state.enabled {
        badge.on_press(Message::NativeMenuAction(Action::SwitchAppMode(target)))
    } else {
        badge
    };
    container(tooltip(badge, footer_tooltip_text(&state), tooltip::Position::Top))
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

> **`'static` → `'a` への変更理由**: `ModeToggleState` を引数に受け取るため
> lifetime を付ける必要がある（R2 設計判断の延長）。tooltip 文字列は `String`
> を保持できるよう `'a` で統一。

### `current_mode` / ガード状態の取得

`mode_toggle_state(...)` は `APP_MODE.get().expect(...)` で現モードを取得し、
`self.engine_busy` / `self.submit_in_flight` / `self._mode_switch_guard.is_some()` を
組み合わせて `ModeToggleState` を生成する。`expect` は初期化パスのみ、
ランタイムの `EngineStopped` ハンドラは `unwrap_or` を使う（R2 設計判断を継承）。

---

## テスト方針

### 自動テスト

#### フッター表示（既存 → 維持）

| ID | ケース | 種別 | ファイル |
|----|--------|------|---------|
| T5 | `STATUS_BAR_HEIGHT == 20` および `STATUS_BAR_BG` が定数として存在 | unit | `src/main.rs` `#[cfg(test)]` |

> T1〜T4（`status_bar_label` / `status_bar_dot_color`）は `ModeToggleState` ベース
> への変更に伴い、TT1〜TT4 に置き換える。

#### フッタートグル（追加）

| ID | ケース | 種別 | ファイル |
|----|--------|------|---------|
| TT1 | `mode_toggle_state(Live, busy=false, submit=false, switching=false).enabled == true` | unit | `src/menu.rs` |
| TT2 | `engine_busy=true` で `enabled=false`、`disabled_reason` が `Engine がビジーです` | unit | 同上 |
| TT3 | `submit_in_flight=true` は `engine_busy` より優先される | unit | 同上 |
| TT4 | `mode_switch_in_progress=true` は最優先 | unit | 同上 |
| TT5 | フッタークリック相当の Message で `Action::SwitchAppMode(!current)` が dispatch | integration | `tests/` |
| TT6 | dirty 時クリック → `SaveAndSwitchMode` confirm dialog が出る | integration | `tests/` |

> **テスト粒度の方針**: `iced` の `Element` は内部状態を持つツリーであり、
> 高さ・padding・背景色を runtime に取り出して assert する API は無い。
> よって視覚的不変条件は **目視に委ねる**。代わりに `STATUS_BAR_HEIGHT` /
> `STATUS_BAR_BG` を **named const** として外出しし、定数の存在と値を
> ユニットテスト（T5）で pin することでリグレッションを防ぐ。

### 廃止する自動テスト

- ❌ `src/main.rs` の `MAIN_RS.contains("status_bar(...")` 系ソース解析テスト
  は採用しない。等価リファクタで壊れやすく、振る舞い保証になっていない
  （観点 D: 既存 `view_calls_confirm_dialog_overlay_helper` 系の脆性と同種）
- popout 非表示の保証は **目視テスト V3** + **コードレビュー** に委ねる。
  将来 `fn render_main_window(...)` のような helper に切り出した時点で
  helper 単体テストで pin する（§未決事項）

### 目視テスト

| ID | ケース | コマンド |
|----|--------|---------|
| V1 | `--mode live` 起動 → 緑の `● LIVE` が表示 / hover で pointer カーソル / クリックで replay に切替 | `cargo run -- --mode live` |
| V2 | `--mode replay` 起動 → アンバーの `● REPLAY` / クリックで live に切替 | `cargo run -- --mode replay` |
| V3 | popout ウィンドウにはフッターが表示されない | dashboard pane を popout に切り出して確認 |
| V4 | バナー表示時にフッターが消えない（下端に維持） | Tachibana Error 状態を再現 |
| V5 | strategy_load_error バナー表示時もフッターが消えない | 不正な戦略ファイルで replay 起動 |
| V6 | ウィンドウリサイズ時にフッターが常に最下部に固定される | 手動リサイズ |
| V7 | Settings menu を開くとフッターが overlay の下に隠れる（C2 の意図的動作） | sidebar の Settings を開く |
| V8 | Theme menu / Network menu でも V7 と同じ挙動 | 各メニュー操作 |
| V9 | toast 表示中に footer と toast が重なるかを観察（重なっても許容） | 通知を発生させる |
| VT3 | 切替中はバッジが減光し再クリックしても無反応 | — |
| VT4 | W&B submit 中はバッジが減光しツールチップに理由が出る | — |
| VT5 | dirty 状態でクリック → save/discard/cancel ダイアログが出る | — |
| VT7 | メニューバーに `モード（Mode）` ボタンが表示されない | — |
| VT8 | `Ctrl+M` で同じ切替が起こる | — |

### CI ゲート

```bash
cargo fmt --check
cargo clippy -- -D warnings
cargo test --workspace
```

---

## 実装ステップ

1. ✅ `src/main.rs` に `STATUS_BAR_HEIGHT` / `STATUS_BAR_BG` 定数と
   `status_bar_label` / `status_bar_dot_color` / `status_bar` の 3 関数を追加する。
   配置は既存ヘルパー関数群（`apply_confirm_dialog_overlay` 近傍）にまとめる
2. ✅ `fn view()` の `id == self.main_window.id` ブロック内で：
   - `is_replay` を `APP_MODE.get().expect(...)` 経由で取得
   - `row!` のメソッドチェーンに `.height(Length::Fill)` を追加
   - `base.push(status_bar(is_replay))` を `active_menu` 分岐の**直前**に挿入
3. ✅ `#[cfg(test)] mod status_bar_tests` にユニットテスト T1〜T5 を追加（全 PASS）
4. ✅ `cargo fmt --check` / `cargo clippy -- -D warnings` / `cargo test --workspace` 全 PASS
5. ✅ 目視確認 V1〜V9 を実施。特に **V7（Settings overlay でフッターが
   隠れる）** は Round 2 C2 の意図的動作の検証なので必ず確認する（2026-04-30 全 PASS）

---

---

## 設計判断ログ

### 2026-04-30: `STATUS_BAR_HEIGHT` の型を `u16` → `u32` に変更

仕様書では `u16` と記載していたが、`iced_core::container::height()` が受け取る
`Into<Length>` の実装が `u32` / `f32` / `Pixels` のみで `u16` は未実装。
コンパイルエラー `E0277` が発生したため `u32` に変更した。
テスト T5 は `STATUS_BAR_HEIGHT == 20` の値のみを assert しており型には依存しないため
テスト修正不要。仕様書の UI 仕様（高さ 20 px）は変わらない。

---

## Tips

- `container::height()` に渡せる整数型は `u32` のみ（`u16` は `Into<Length>` 未実装）
- `engine-client/tests/schema_v2_4_nautilus.rs` の `ExecutionMarker` パターンは
  `..` を使わず全フィールドを束縛し `qty.is_none()` を assert すること（M-3 知見）
- float 比較は `f32::EPSILON`（≈1e-7）ではなく `1e-5` を使う（丸め誤差マージン確保）
- `dashboard_modal` は全面 opaque ではなく背景透過のため、Settings/ThemeEditor/Network
  メニュー展開中もフッターは隠れず下端に表示される（C2 の「隠れる」は `main_dialog_modal`
  のみ。仕様書の「opaque overlay が画面全体を覆う」記述は誤記）

---

## レビュー反映（2026-04-30, ラウンド 1）

### 解消した指摘

| ID | サマリ |
|----|--------|
| H-1 | `EngineStopped` を live モードでも `ReplayFinished` に変換していた → `APP_MODE` で分岐 |
| H-2 | `Flowsurface::new()` の `unwrap_or(false)` が D9 安全装置を無音化 → `expect` に変更 |
| M-1 | doc comment が `status_bar_label` に誤付与 → `apply_confirm_dialog_overlay` 直前へ移動 |
| M-2 | `status_bar()` の戻り値 `'static` が Round 1 H2 決定違反 → `'_` に変更 |
| M-3 | `ExecutionMarker` テストで `qty` が `..` で無検査 → 明示束縛 + `assert!(qty.is_none())` |
| M-4 | T3〜T4 の float 比較が `f32::EPSILON` で不安定 → `1e-5` に変更 |
| M-5 | T5 が `STATUS_BAR_BG.a` を検証しない → `assert!((.a - 1.0).abs() < eps)` 追加 |

### 設計判断

- `status_bar()` 戻り値を `'_` にしても `'static` 入力のみなので成立する。将来 `&self` 参照を含む拡張時も `'_` の方が柔軟
- `EngineStopped` ガードは `APP_MODE.get().unwrap_or(false)` を使用（`expect` だとここでの panic が live → replay モード切替で問題になりうるため）
- C2「opaque overlay」の記述は `dashboard_modal`（背景透過）と `main_dialog_modal`（全面暗転）の違いを区別できていなかった。次回仕様書改訂で修正する

### 残存 LOW（対応不要）

- L: テスト命名（`t1_`〜`t5_` プレフィックス）— 機能に影響なし
- L: `view_calls_confirm_dialog_overlay_helper` の境界検索が `"\n    fn "` で脆弱 — プリエグジスティング、現状のコード構造で問題なし
- L: `NativeMenuSetup` / `build_state_json` / `RequestOrderList` の `APP_MODE` `unwrap_or` — プリエグジスティング、別フェーズで一括修正候補

---

## レビュー反映（2026-04-30, ラウンド 2）

### 解消した指摘

| ID | サマリ |
|----|--------|
| R2-M-1 | `ReplayFinished` ハンドラで `_res` 握り潰し → `Ok/Err` 分岐 + エラートースト追加 |
| R2-M-2 | `EngineStopped` アームの `unwrap_or(false)` に理由コメントを追記 |

### 設計判断

- **`status_bar()` の `'static`**: `'_` は入力参照が無いため lifetime elision が機能せずコンパイルエラー（E0106）。`'static` を維持し、理由をコメントで明記した（`'static` は `'_` のサブタイプなので呼び出し元 `view()` での利用に問題なし）
- **`EngineStopped` の `unwrap_or(false)`**: ランタイムイベントハンドラでの `false`（live 扱い）フォールバックは意図的。`expect` は初期化パス（`Flowsurface::new`）に限定し、ランタイムパスでは安全なフォールバックを許容する設計方針を確定

### 残存 LOW（対応不要）

- L: テスト命名（`t1_`〜`t5_` プレフィックス）— 機能に影響なし

---

## 未決事項・将来の拡張候補

| 項目 | 優先度 | 備考 |
|------|--------|------|
| `fn render_main_window(...)` helper 抽出 | 中 | view() の main window ブロックを純関数に切り出せば、popout 非表示が型/呼び出しグラフで保証でき、ソース解析テストが不要になる |
| toast の inset 調整（footer 高さ分の bottom padding） | 中 | `toast::Manager` のシグネチャに inset 引数を追加するリファクタが必要 |
| バージョン表示（右端） | 低 | `env!("CARGO_PKG_VERSION")` で取得可 |
| 接続状態インジケーター | 中 | エンジン接続中 / 切断中を示す |
| タイムゾーン表示 | 低 | `self.timezone` をそのまま表示 |
| フッター高さのスケーリング | 低 | HiDPI 環境で 20 px が小さすぎる場合 |
| テーマシステム連携 | 低 | 現フェーズは固定色。将来 `style::*` 関数経由に移行 |
| モーダル展開中もフッターを見せる設計 | 低 | 現状は overlay に隠れる（C2）。要望が出たら overlay 側に窓を空けるか、外側合成＋下寄せモーダルの再配置で対応 |
| 右クリックによるミニメニュー（live / replay を明示選択） | 低 | トグル性を補完したい場合。詳細は [`mode-toggle-redesign.md §未決事項`](./mode-toggle-redesign.md#未決事項) |
| disabled 時のスピナー表示 | 低 | `…` サフィックスの代わり。iced の animation 機構と相談 |
