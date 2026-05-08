# 修正計画書: Issue 1 — 銘柄入力欄の廃止 → チャートタイトルバー連動

> **✅ 実装完了 (2026-05-08)**
> R1 レビュー反映済み。詳細は `docs/plan/review-fixes-2026-05-08.md`。

## 根本原因

`widget_menu_bar.rs:242-244` の銘柄 `text_input` は独立した入力ウィジェットとして実装されており、
チャートペイン側の銘柄変更イベントと連動していない。
ユーザーは「メニューバーの入力欄」と「チャートタイトルバー」の 2 箇所で同じ情報を管理する羽目になっている。

二重管理の解消には次の 3 経路すべてを揃える必要がある:

| 経路 | 現状 | 対応 |
|---|---|---|
| 新規 replay 開始時の入力源 | menu bar の `text_input` | `ReplayFormModal` 内 `text_input` に一本化 |
| SCENARIO 起動による prefill | `prefill_from_scenario` で `bar.instrument_id` を直接更新（既に動作） | 維持 |
| チャートタイトルバーで銘柄を切り替えたとき | `Effect::SwitchTickersInGroup` 経由・`bar` 非同期 | **本計画で同期フック追加** |

> 旧版で「`PaneMsg` が無いので未実装」とした分析は誤り。実経路は
> [pane.rs:1878](src/screen/dashboard/pane.rs#L1878) の
> `RowSelection::Switch(ti) → Effect::SwitchTickersInGroup` で
> [dashboard.rs:1526](src/screen/dashboard.rs#L1526) `switch_tickers_in_group` に到達している。
> ここに `bar` 同期を差し込むのが本命。

## 影響範囲

- [src/widget_menu_bar.rs:242-244](src/widget_menu_bar.rs#L242-L244) — `text_input` ウィジェット本体
- [src/menu_bar_state.rs](src/menu_bar_state.rs) — `BarMessage::InstrumentChanged` バリアント削除
- [src/handlers/menu.rs:74-110](src/handlers/menu.rs#L74-L110) — `PressPlay` を modal を開く挙動に変更
- [src/handlers/replay.rs:90-143](src/handlers/replay.rs#L90-L143) — `ReplayDataLoaded` で初期 `bar.instrument_id` を設定
- [src/screen/dashboard.rs:1526](src/screen/dashboard.rs#L1526) `switch_tickers_in_group` — replay pane focus 時に `bar.instrument_id` を同期するフック
- [src/modal/replay_form.rs](src/modal/replay_form.rs) — 既存 `text_input` がそのまま入力経路（変更不要）

## 修正方針

### Step 1: `text_input` を menu bar から削除（`widget_menu_bar.rs`）

`widget_menu_bar.rs:242-244` の `text_input("銘柄 (例: 7203)", ...)` を `row![]` から削除する。
削除箇所には `ReplayBarState.instrument_id` を**読み取り専用ラベル**として表示する（`text(&bar.instrument_id)` 等）。
バーは「現在選択中の銘柄を表示するだけ」の存在に降格する。

```diff
-        text_input("銘柄 (例: 7203)", &bar.instrument_id)
-            .on_input(BarMessage::InstrumentChanged)
-            .width(Length::Fixed(120.0)),
+        text(if bar.instrument_id.is_empty() {
+            "(未選択)".to_string()
+        } else {
+            bar.instrument_id.clone()
+        })
+        .width(Length::Fixed(120.0)),
```

`ReplayBarState.instrument_id` フィールド自体は存続（Python に渡すパラメータとして機能している）。

### Step 2: `PressPlay` を「modal を開く」挙動に変更（`handlers/menu.rs`）

現状 `PressPlay` は `bar` から `ReplayFormModal` を構築 → `validate()` → 直接送信、というフローで
modal UI を経由しない（`replay_form_modal` を `Some` にしない）。
そのため menu bar の `text_input` が消えると、新規 replay 開始時に instrument_id を入れる場所が無くなる。

修正: 一時停止解除（`replay_paused && replay_running`）以外のパス（=新規 replay）では
`replay_form_modal` を `Some` にして modal を開き、submit ボタンで初めて送信する。

```rust
// handlers/menu.rs PressPlay arm (新規 replay 経路)
} else {
    // 新規 replay: modal を開く。bar の現状値を引き継ぐ（SCENARIO prefill 済みなら反映される）。
    use crate::modal::replay_form::ReplayFormModal;
    let bar = &self.menu_bar.replay_bar;
    self.replay_form_modal = Some(ReplayFormModal {
        instrument_id: bar.instrument_id.clone(),
        start_date: bar.start_date.clone(),
        end_date: bar.end_date.clone(),
        granularity: bar.granularity.clone(),
        strategy_file: bar.strategy_file.clone(),
        initial_cash: bar.initial_cash.clone(),
        validation_error: None,
        submitting: false,
    });
    Task::none()
}
```

modal 内の `text_input("銘柄 (例: 7203)", ...)`（`replay_form.rs:3` で既にインポート済み）が
新規 replay の instrument 入力 UI となる。`Submit` 時の検証・送信ロジックは既存の `replay_form_modal` ハンドラ
（[handlers/replay.rs:326](src/handlers/replay.rs#L326) 周辺）にそのまま乗る。

### Step 3: `ReplayDataLoaded` で `bar.instrument_id` を初期化（`handlers/replay.rs`）

[handlers/replay.rs:101-111](src/handlers/replay.rs#L101-L111) で `ids` を確定した直後、
`bar.instrument_id` を初期銘柄に同期する。

```rust
// handlers/replay.rs ReplayDataLoaded、ids 確定後
self.menu_bar.replay_bar.instrument_id = ids[0].clone();
```

**複数銘柄でも `ids[0]` のみ表示**（= `LoadReplayData` の `first_id` と一致）。
理由は Step 4 で説明する「focused pane の単一銘柄を表示する」というルールに揃えるため。
バー上で `ids.join(", ")` のように一覧表示する案は採用しない（focused pane 切り替え時のセマンティクスが破綻するため）。

### Step 4: `Effect::SwitchTickersInGroup` で `bar.instrument_id` を同期（`screen/dashboard.rs`）

[dashboard.rs:1526](src/screen/dashboard.rs#L1526) `switch_tickers_in_group` の冒頭、
あるいは呼び出し元で `Effect::SwitchTickersInGroup(ti)` を捕らえている経路で、
focused pane が **replay 文脈** であれば `bar.instrument_id` を更新する。

```rust
// switch_tickers_in_group 内、focus 解決後
// replay 起動中なら bar も同期
if self.replay_running {
    self.menu_bar.replay_bar.instrument_id = ticker_info.ticker.symbol_id().to_string();
}
```

> `replay_running` フラグまたは pane の種別（`Content::Candlestick` 等が replay 由来か）で
> live セッションへの混信を防ぐ。実装時に `replay_running` 判定で十分かを確認し、
> 不足なら `is_replay_pane(pane_id)` ヘルパを追加する。

これにより:
- pane タイトルバーで銘柄を切替 → `Effect::SwitchTickersInGroup` → `bar.instrument_id` 同期
- focused pane を切り替えても、各々の銘柄が `RowSelection::Switch` 経由で発火するわけではないので、
  **focus 切替単独では bar は再追従しない**。focus 切替で bar を追従させるかは将来課題（Step 6 参照）。

### Step 5: `BarMessage::InstrumentChanged` を削除

text_input 削除後、本バリアントの発火源は無くなる。

- `BarMessage::InstrumentChanged(String)` バリアントを削除
- [handlers/menu.rs:60-62](src/handlers/menu.rs#L60-L62) 周辺の no-op アームから当該行を削除
- 既存テスト（grep `InstrumentChanged`）を削除または別経路に書き換え
- `prefill_from_scenario` / Step 3 / Step 4 はいずれも state を直接書くのでメッセージ経路は不要

### Step 6: 既知の制約と将来課題

- **focus 切替に bar が追従しない**: 複数 replay pane を開いて focus を切り替えても、
  `bar.instrument_id` は最後に `SwitchTickersInGroup` を発火した値のまま。
  これは現フェーズのスコープ外とし、必要なら別 Issue で `pane_focused` フックを追加する。
- **link_group 越しの伝播**: `switch_tickers_in_group` は link_group 経由で他 pane も書き換えるため、
  group 外 pane focus 中でも bar が更新される可能性がある。replay フェーズでは link_group 1 系統のみ
  使うことが多いので影響は小さいが、テストで挙動を pin しておく（下記テスト 3）。

## テスト計画

### 単体・統合テスト

1. **fresh state で toolbar `Play` がまだ開始できる**
   - `replay_form_modal` が `None` の状態で `BarMessage::PressPlay` を送ると
     `replay_form_modal` が `Some` になり modal が開く
   - modal の `text_input` で `instrument_id` を入力 → `Submit` で `LoadReplayData` が送信される

2. **replay pane のタイトルバー変更後に `bar.instrument_id` が同期する**
   - `replay_running = true` で `Effect::SwitchTickersInGroup(ti)` を処理 →
     `menu_bar.replay_bar.instrument_id == ti.ticker.symbol_id()` を assert

3. **複数銘柄 replay でバー表示は `ids[0]`**
   - `ReplayDataLoaded { ids: vec!["7203", "9984"] }` を流す →
     `bar.instrument_id == "7203"` を assert
   - その後 pane タイトルバーで `9984` に切り替え → `bar.instrument_id == "9984"` を assert
   - `ids.join(", ")` 表示は採用しない旨を assert で固定（`!= "7203, 9984"`）

4. **`BarMessage::InstrumentChanged` バリアント不在の確認**
   - コンパイル時に検出されるが、念のため grep ベースで `InstrumentChanged` 残留 0 件を確認

5. **SCENARIO prefill 経路は壊れない**
   - 既存の `prefill_from_scenario` テストが緑のまま

### 観測点

- `cargo test -p e-station --lib menu_bar_state`
- `cargo test -p e-station --lib handlers::replay`
- `cargo test -p e-station --lib screen::dashboard`
- 手動: SCENARIO 起動 → bar に銘柄表示 → タイトルバーで切替 → bar 追従を目視

## 確認項目

- [ ] `text_input` 削除後にビルドが通ること（`cargo check --workspace`）
- [ ] `BarMessage::InstrumentChanged` 残留が 0 件（`Grep "InstrumentChanged"` で確認）
- [ ] `ReplayDataLoaded` 時に `bar.instrument_id` が `ids[0]` で初期化される
- [ ] `Effect::SwitchTickersInGroup` 処理で replay 中なら bar が同期する
- [ ] 新規 replay 開始フローが modal 経由で動作する（fresh state で `Play` を押せる）
- [ ] SCENARIO prefill 経路が壊れていない
- [ ] 上記テスト 1〜5 が緑

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| [src/widget_menu_bar.rs:242-244](src/widget_menu_bar.rs#L242-L244) | `text_input` を `text` ラベルに置換 |
| [src/menu_bar_state.rs](src/menu_bar_state.rs) | `BarMessage::InstrumentChanged` バリアント削除 |
| [src/handlers/menu.rs](src/handlers/menu.rs) | `PressPlay` 新規経路を modal を開く挙動に変更 |
| [src/handlers/replay.rs](src/handlers/replay.rs) | `ReplayDataLoaded` で `bar.instrument_id = ids[0]` |
| [src/screen/dashboard.rs:1526](src/screen/dashboard.rs#L1526) | `switch_tickers_in_group` で replay 中の bar 同期 |
| 各テストファイル | 上記テスト 1〜5 を追加 |

## 実装難易度

**中**。UI 変更だけでなくメッセージ経路の組み換え（`PressPlay` → modal）と
既存ホットパス（`switch_tickers_in_group`）への副作用追加を含む。
新規 IPC スキーマ変更は不要。
