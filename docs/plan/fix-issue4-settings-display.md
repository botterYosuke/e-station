# 修正計画書: Issue 4 — メニューバーの各フィールドに現在設定値を表示

> **✅ 実装完了 (2026-05-08)** — R1 レビュー反映済み。

## 根本原因

ユーザーがリプレイフォームモーダル（`modal/replay_form.rs`）から設定を入力して
リプレイを開始した場合、フォームの値は `Command::LoadReplayData` と `Command::StartEngine` として
Python に送信されるが（`handlers/replay.rs:325-397`）、
**`ReplayBarState` の各フィールドにはコピーされない**。

メニューバーの `text_input` は `&bar.start_date`、`&bar.end_date`、`&bar.initial_cash`、
`bar.granularity.as_ref()` に直接バインドされているため（`widget_menu_bar.rs:245-259`）、
フォームから入力した値が `ReplayBarState` に書き戻されなければ、
フィールドは空（Default 状態）のままプレースホルダーが表示され続ける。

### SCENARIO ファイル経由の場合

`prefill_from_scenario()` (`menu_bar_state.rs:41-72`) は `ScenarioLoaded` イベント受信時に呼び出され
（`handlers/replay.rs:261-284`）、SCENARIO JSON から各フィールドを設定する。
この経路は正しく機能している。

### フォームモーダル経由の場合（バグ）

`ReplayMsg::FormMsg` (`handlers/replay.rs:325-397`) でフォーム送信を処理するが、
`FormMsg` のバリアント内に `menu_bar.replay_bar.start_date = form.start_date.clone()` 等の
書き戻しコードが存在しない。これが直接の原因。

## 影響範囲

- `src/handlers/replay.rs:325-397` — `ReplayMsg::FormMsg` ハンドラ
- `src/menu_bar_state.rs:39-77` — `ReplayBarState::prefill_from_scenario()` （参考実装）
- `src/modal/replay_form.rs` — フォームの state 構造（`start_date`, `end_date`, `granularity`, `initial_cash` の型確認）

## 修正方針

### Step 1: フォームの送信値を特定する

`handlers/replay.rs:348` の `Command::LoadReplayData` 呼び出し時に使われているフォーム変数を確認する。
フォームの state から各フィールドを読み取っている箇所が書き戻しのソース。

### Step 2: `FormMsg` ハンドラで `ReplayBarState` に書き戻す

`Command::LoadReplayData` を発行する直前に `ReplayBarState` を更新する：

```rust
// handlers/replay.rs — FormMsg ハンドラ内、IPC 発行の直前
self.menu_bar.replay_bar.start_date = form_state.start_date.clone();
self.menu_bar.replay_bar.end_date = form_state.end_date.clone();
self.menu_bar.replay_bar.granularity = form_state.granularity;
self.menu_bar.replay_bar.initial_cash = form_state.initial_cash.clone();
if let Some(path) = &form_state.strategy_file {
    self.menu_bar.replay_bar.strategy_file = Some(path.clone());
}
```

`instrument_id` については Issue 1 の対応（DataLoaded 時に ids から設定）と整合させる：
フォームに instrument 入力がある場合はここで設定、なければ DataLoaded で設定。

### Step 3: 「編集中の上書き防止」を考慮するか

計画書の注意点にある「ユーザーが入力欄を編集中の場合に上書きしないよう」の要件について：
FormMsg ハンドラは「フォームを送信した瞬間」にしか呼ばれないため、
この時点で上書きしても問題ない（ユーザーがバーで編集中にフォームを送信はできない）。

DataLoaded 時の書き戻しも同様：DataLoaded が届く時点ではリプレイが開始済みであり
バーの入力欄をユーザーが編集していることはない。  
よって「空のときのみ書き戻す」ガードは不要。

### Step 4: `text_input` のバインディング確認

`widget_menu_bar.rs:245-259` の各 `text_input` の `value` 引数が
`&bar.start_date` 等の参照になっていることを確認する。
現在の実装: `text_input("開始 YYYY-MM-DD", &bar.start_date)` — 既にバインド済み。
Step 2 で `ReplayBarState` フィールドが更新されれば自動的に反映される。

## 確認項目

- [ ] `modal/replay_form.rs` のフォーム state 構造と各フィールドの型が `ReplayBarState` と一致するか
- [ ] `granularity` は `Option<Granularity>` 同士で互換性があるか
- [ ] `FormMsg` ハンドラが複数のサブバリアント（Submit, Cancel 等）を持つ場合、Submit 時のみ書き戻すよう条件分岐を入れること
- [ ] リプレイファイル切替時（`DataLoaded` の再受信）にもフィールドが正しく更新されること

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `src/handlers/replay.rs:325-397` | `FormMsg` の Submit バリアント処理内で `ReplayBarState` フィールドに書き戻す |

## 実装難易度

**低**。`FormMsg` ハンドラ内でフォームの値を `ReplayBarState` にコピーするだけ。
新しい型定義・IPC 変更は不要。

---

## レビュー反映 (2026-05-08, ラウンド 1)

### R1 指摘 → 解消済み ✅

| 指摘 | 解消内容 |
|---|---|
| instrument_id が漏れ | `instrument_ids.join(", ")` で `replay_bar.instrument_id` に書き戻す実装を追加 |
| DataLoaded 依存が不正確 | 「DataLoaded で補完」記述を削除。Submit 時に全フィールドを書き戻す方針に統一 |
| 擬似コードの型ズレ | `strategy_file: PathBuf`（`.clone()`）・`initial_cash: u64`（`.to_string()`）として実装。form_state 再参照ではなく Submit payload の変数を直接使用 |
| リグレッションテスト不足 | `native_menu_handler_tests::form_submit_writes_all_fields_back_to_replay_bar` を追加（`main.rs:3700` 付近）。既存パターン（source inspection）に従い全 6 フィールドをピン |

### 設計判断

- 書き戻しは `self.replay_form_modal = None;` 直後・`if let Some(conn)` の前に置く。
  `engine_connection` が None でも（IPC 未接続でもフォームを開ける将来対応）バー表示は更新される。
- フィールドのエイリアス（`let bar = &mut ...`）はソース検査テストが `replay_bar.<field>` の文字列を検索できなくなるため不使用。直接 `self.menu_bar.replay_bar.<field>` で記述。
- `ShowDialog` 時のモーダル初期値同期（バーの現在値でプリフィル）は本 Issue のスコープ外。別 Issue として検討。

### 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/handlers/replay.rs:339-344` | Submit アームに `replay_bar` 書き戻し 6 行追加 |
| `src/main.rs:3700` 付近 | `form_submit_writes_all_fields_back_to_replay_bar` テスト追加 |

### 検証結果

`cargo check / clippy / test --workspace` 全緑（318 + 系 テスト通過）。
`cargo fmt --check` の差分は既存 `heatmap.rs` のみで本 Issue 変更ファイルは差分なし。
