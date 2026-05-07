# Replay コントロールバー実装計画

## Context

**現状**: replay モードで再生開始するたびにモーダルダイアログ
([src/modal/replay_form.rs](src/modal/replay_form.rs)) が開き、銘柄/開始日/終了日/
粒度/戦略ファイル/初期資金の **6 項目を毎回入力**する必要がある。試行錯誤の
頻度が高いと UX 負荷が大きい。

**変更目的**: メニューバー
([src/widget_menu_bar.rs](src/widget_menu_bar.rs)) を 2 段構成（32px → 64px）に
拡張し、replay 中の状態表示・入力欄・再生制御ボタンをすべてバー上に常設して
モーダル往復を排除する。

**仕様確定事項** (本セッションで合意):

| 項目 | 決定 |
|---|---|
| Step backward (1step戻る) | **本フェーズで実装する**。粒度単位（Daily=1日、Minute=1分、Trade=1取引）で戻る。戦略状態（ポジション/PnL/注文）を正確に復元する。Python engine に snapshot ring buffer を追加。新 IPC: `StepBackward`。 |
| Play/Pause/Step+ IPC | **新規 IPC を追加**: `PauseReplay` / `ResumeReplay` / `StepReplay`。schema MINOR を bump。 |
| 入力欄反映タイミング | **「再生」押下時のみ**。新 replay セッションとして engine に発行（既存 `LoadReplayData` + `StartEngine` 経路）。 |
| メニューバー高さ | **2 段構成**: 1段目 32px (File メニュー + 現在情報 + 再生制御ボタン)、2段目 32px (replay 入力欄)。Live モードでは2段目を出さず 32px のまま。 |
| File メニュー項目 | **Live モード**: 現状維持 (`開く` / `上書き保存` / `名前を付けて保存` / `終了`)。 **Replay モード**: Live モードと同じ 4 項目に統一 (`開く` / `上書き保存` / `名前を付けて保存` / `終了`)。`リプレイを開始` / `リプレイを停止` は replay モードのメニューから削除し、再生制御はバー上のボタンに完全移行。現行 Replay 集合は `[ReplayStart, ReplayStop, Quit]`（Open/SaveAs を含まない）であり、Step 6 で Live と同じ集合に変更する。 |

---

## 成果物

### 新規ドキュメント

**`docs/✅menu-and-footer/replay-control-bar-impl.md`** を新設し、本計画の
最終仕様（後述のレイアウト図・状態表・IPC 定義・テスト一覧）を移植する。
README.md の目次にも追加する。

### コード変更

#### 1. IPC schema 拡張（最初に着手）

| ファイル | 変更 |
|---|---|
| [python/engine/schemas.py](python/engine/schemas.py) | `PauseReplay` / `ResumeReplay` / `StepReplay` / `StepBackward` の `IpcMessage` サブクラス追加。`DateChangeMarker` / `RestoreSnapshot` / `ReplayHistoryChanged` の Event サブクラス追加。`SCHEMA_MINOR` を bump。`__all__` に追加。`AttemptedCommand` / `ReplayOnlyCommand` Literal へ `PauseReplay` / `ResumeReplay` / `StepReplay` / `StepBackward` を追加。 |
| [engine-client/src/dto.rs](engine-client/src/dto.rs) | `Command` enum に 4 variant 追加（`request_id: String` フィールド付き、既存 `StopReplay` パターン踏襲）。`AttemptedCommand` enum と `Debug` impl にも追加。`EngineEvent` enum に `RestoreSnapshot` / `ReplayHistoryChanged { has_history: bool }` variant を追加。 |
| [python/engine/server.py](python/engine/server.py) | `op == "PauseReplay" / "ResumeReplay" / "StepReplay" / "StepBackward"` 分岐を `SetReplaySpeed` 隣 (line 930 付近) に追加。`_check_replay_state` で適切な状態を要求。pause フラグを `_replay_paused: bool`、スナップショット履歴を `_replay_snapshots: deque[ReplaySnapshot]` として導入。 |
| [python/engine/replay_session.py](python/engine/replay_session.py) | streaming runner ループに `paused` フラグ・`step_request` カウンタ・**粒度境界でのスナップショット取得**を追加。`get_multiplier` 渡しと同じパターンで `get_paused` / `consume_step` / `push_snapshot` クロージャを注入。 |
| [engine-client/tests/dto_*.rs](engine-client/tests/) | 新 Command の serde roundtrip + debug redaction テストを追加。 |

**Pause セマンティクス**: CPU スピンを避けるため threading.Event ベースで実装する。
- `_replay_paused_event: threading.Event`（set = running、cleared = paused）を導入
- ループ先頭で `_replay_paused_event.wait()` を挿入
- PauseReplay 受信時: `_replay_paused_event.clear()`
- ResumeReplay 受信時: `_replay_paused_event.set()`
- StepReplay 受信時: `_step_request` カウンタを +1。ループ内でカウンタ > 0 のとき 1 tick emit して -1 する
- `get_multiplier` クロージャと同様に `get_paused_event` / `consume_step` を `start_backtest_replay_streaming` に注入
- `StepReplay` は `PAUSED` 状態のみ受け入れる（`RUNNING` 中は `EngineBusy` を返す）。UI 側は `replay_running && replay_paused` の場合のみ ⏭ ボタンを enable にする
- `StepBackward` は `PAUSED` 状態のみ受け入れる（`RUNNING` 中は `EngineBusy` を返す）。

**Snapshot ring buffer 設計**:

```python
@dataclass
class ReplaySnapshot:
    step_index: int           # 何ステップ目か
    portfolio: dict           # positions / cash / realized_pnl のシリアライズ
    open_orders: list         # 未約定注文リスト
    strategy_state: object    # copy.deepcopy(strategy) — 任意の Python オブジェクト
    ui_events: list[dict]     # この step で送出した UI イベント群（再送信用）
```

- `_replay_snapshots = collections.deque(maxlen=1000)` (server.py)
- 各粒度境界（bar 完結時）にスナップショットを push
- `StepBackward` 受信時（PAUSED のみ受け入れ）:
  1. Rust 側に `RestoreSnapshot` event を先行送信し、pane を全置換モードに切り替える（フラッシュ・重複受信防止）
  2. 末尾スナップショットを pop → portfolio/orders/strategy を復元
  3. `ui_events` を Rust に再送信（pane が RestoreSnapshot で受け入れ待ちのため重複扱いにならない）
  4. `ReplayHistoryChanged { has_history: !_replay_snapshots.is_empty() }` を送信して Rust 側ボタンを更新

**strategy_state の deepcopy 失敗対策**: `copy.deepcopy` が失敗した場合 (`__deepcopy__` 未実装等) はスナップショットを保存しない (その step は巻き戻し不可)。失敗時は `logger.warning("snapshot deepcopy failed at step %d: %s", step_index, e)` でログ出力してからスキップする。`has_history` フラグで Rust 側ボタン有効化を制御。

#### 2. メニューバー拡張

| ファイル | 変更 |
|---|---|
| [src/widget_menu_bar.rs](src/widget_menu_bar.rs) | `view()` を `column!` で 2 段化。`mode == Replay` のとき下段を出す。`BAR_HEIGHT` は constant ではなく `bar_height(mode)` 関数化（live=32 / replay=64）。`with_dropdown_overlay` の `top_offset` 計算を bar_height 連動に。F8 R2 で廃止した動的 Y アンカーとは異なり、2段化は `bar_height(mode)` を `with_dropdown_overlay` の `top_offset` に静的に渡す方式であり、mouse_area on_move を再導入しない。 |
| [src/widget_menu_bar.rs](src/widget_menu_bar.rs) | 新規ヘルパー `replay_control_row(state) -> Element` を追加: 戦略ファイル名表示 / Current 日表示 / 再生(▶) / 一時停止(⏸) / Step+(⏭) ボタン。状態に応じて on_press 切り替え。 |
| [src/widget_menu_bar.rs](src/widget_menu_bar.rs) | 新規ヘルパー `replay_input_row(state) -> Element` を追加: 銘柄/開始日/終了日 `text_input` + 粒度 `pick_list` + 初期資金 `text_input`。各 `on_input` は `Message::ReplayBarInput*` を発行。 |
| [src/menu_bar_state.rs](src/menu_bar_state.rs) | `ReplayBarState` 構造体を追加（モーダルの `ReplayFormModal` から `validation_error` / `submitting` を除いた永続状態 + `last_known_strategy_file: Option<PathBuf>` + `current_day: Option<NaiveDate>` + `replay_has_history: bool`（Python の `_replay_snapshots` 非空を `ReplayHistoryChanged` IPC 経由で更新））。`State` に同フィールドを格納。`Granularity` 型は `crate::modal::replay_form::Granularity` として参照する。 |
| [src/menu_bar_state.rs](src/menu_bar_state.rs) | `BarMessage` に `InstrumentChanged(String)` / `StartDateChanged(String)` / `EndDateChanged(String)` / `GranularityChanged(Granularity)` / `InitialCashChanged(String)` / `PickStrategyFile` / `PressPlay` / `PressPause` / `PressStepForward` / `PressStepBackward` / `PressStop` を追加。 |
| [src/main.rs](src/main.rs) | 既存 `replay_form_modal: Option<ReplayFormModal>` 経路を **撤廃せず併存**（Action::ReplayStart によるモーダル経路は当面残す）。`PressPlay` の handler は modal の `validate()` を再利用して `Command::LoadReplayData + Command::StartEngine` を発行（`main.rs` の `LoadReplayData + StartEngine` 発行ロジックを関数抽出して共通化）。 |
| [src/main.rs](src/main.rs) | `PressPause` / `PressStepForward` から新 IPC コマンドを送る `dashboard::Event` または直接 `engine.send_command` 呼び出しを追加。`Command::PauseReplay` 等。 |
| [src/main.rs](src/main.rs) | `Current 日` 表示用に既存の `ReplayDataLoaded` イベントから取得した `current_replay_date: Option<NaiveDate>` を `Flowsurface` に保持し、replay tick 進行で更新。`DateChangeMarker` イベント（`{"event": "DateChangeMarker", "date": "YYYY-MM-DD"}`）の `date` フィールドを受信して更新する。`DateChangeMarker` は `dto.rs` 実装済み。`schemas.py` には未定義のため Step 2 で追加する。 |

#### 2.5 Replay モード File メニュー項目の整理

[src/menu.rs](src/menu.rs) の `actions_for_mode(&AppMode::Replay)` を Live と同じ集合に変更:

```text
Before: [ReplayStart, ReplayStop, Open, SaveAs, Quit]
After:  [Open, Save, SaveAs, Quit]   // Live と完全一致
```

- `Action::ReplayStart` / `Action::ReplayStop`: replay モードからも消す。バー上の ▶ / ⏹ ボタンが完全置換。
- `Action::Save` / `Action::SaveAs`: replay モードでも live と同じく返す。`save-menu-impl.md` の dirty 判定経路がそのまま流れるか確認 (Replay モードで上書き保存対象は `CURRENT_PATH` = 戦略 `.py` か layout `.json` か、`save-menu-impl.md` を読み直して必要なら docs 追記)。
- 結果として `actions_for_mode` は mode 依存ロジックを失い、`actions_for_mode(_) = [Open, Save, SaveAs, Quit]` の定数返しに退化する。引数を残すかは Step 6 で判断 (将来分岐余地のため残しても害なし、シンプル化のため落としても良い)。
- ファイルメニューのヘッダ "ファイル（File）▼" は変更なし。

[src/widget_menu_bar.rs](src/widget_menu_bar.rs) の `entries_for_menu` の `ReplayStop if !replay_running` 分岐は不要になるため削除。`replay_running` 引数は引き続き `with_dropdown_overlay` のシグネチャに残す (将来別目的で使う可能性) か、不要なら削除する判断を Step 6 で行う。

更新が必要なテスト:
- [tests/menu_actions_cross_platform.rs](tests/menu_actions_cross_platform.rs) — `actions_for_mode(&AppMode::Replay)` の期待値
- [docs/✅menu-and-footer/README.md](docs/✅menu-and-footer/README.md) の `### File メニュー` テーブル — 行を書き換え
- [docs/✅menu-and-footer/save-menu-impl.md](docs/✅menu-and-footer/save-menu-impl.md) — replay モードで Save が有効になる挙動の追記

#### 3. 再生制御ボタンの enable 条件（新規 `mode_toggle_state` 風 helper）

[src/menu.rs](src/menu.rs) に `replay_control_state(replay_running, replay_paused) -> ReplayControlState` を追加し、テストしやすい純関数として隔離する。

| ボタン | enabled 条件 |
|---|---|
| ▶ 再生 | `mode == Replay`. `replay_running == false` (新セッション開始) または `replay_paused == true` (Resume) |
| ⏸ 一時停止 | `replay_running && !replay_paused` |
| ⏭ Step+ | `replay_running && replay_paused`（PAUSED 状態のみ受け入れ。RUNNING 中は EngineBusy を返す） |
| ⏮ Step- | `replay_running && replay_has_history` (`_replay_snapshots` が空でない間のみ有効) |
| ⏹ 停止 | `BarMessage::PressStop` → `Command::StopReplay` IPC を発行する独立経路（Step 6 で `Action::ReplayStop` variant を削除するため、File メニュー経路には依存しない）。`replay_running` のとき enable。 |

---

## 重要ファイル（変更対象）

```
engine-client/src/dto.rs                        — Command enum 拡張
python/engine/schemas.py                        — SCHEMA_MINOR bump + 3 op
python/engine/server.py                         — op 分岐 + paused 状態
python/engine/replay_session.py                 — pacing ループに paused/step
src/widget_menu_bar.rs                          — 2 段化 + 新行ヘルパー
src/menu_bar_state.rs                           — ReplayBarState 追加
src/menu.rs                                     — replay_control_state helper
src/main.rs                                     — 新 BarMessage handler / IPC 発行
src/modal/replay_form.rs                        — validate() を pub に昇格して共有
docs/✅menu-and-footer/replay-control-bar-impl.md  — 新設仕様書
docs/✅menu-and-footer/README.md                 — 目次追加
docs/✅menu-and-footer/widget-menu-bar-impl.md   — BAR_HEIGHT 動的化を反映
```

---

## 既存の再利用

- **`ReplayFormModal::validate()`** ([src/modal/replay_form.rs:111](src/modal/replay_form.rs#L111)): 入力検証ロジックを完全再利用。`pub fn` に昇格して `widget_menu_bar` 経由でも呼べるようにする。重複実装を避ける。
- **`prefill_from_scenario`** ([src/modal/replay_form.rs:171](src/modal/replay_form.rs#L171)): 戦略ファイル選択時の SCENARIO 読み込みは同じヘルパーで `ReplayBarState` にも投入する。
- **`Granularity` enum** ([src/modal/replay_form.rs:9](src/modal/replay_form.rs#L9)): pick_list 用の型をそのまま流用。
- **`SetReplaySpeed`** 経路（`main.rs` の `SetReplaySpeed` dispatch 箇所）: 新 IPC の dispatch 構造を踏襲。
- **`StopReplay`** 経路（`main.rs` の `StopReplayOnly` ハンドラ）: `request_id` 採番・タイムアウト・`ForceStopReplay` フォールバックの実装パターンをコピー。
- **`mode_toggle_state`** ([src/menu.rs](src/menu.rs)): 純関数 helper + テストの構成を `replay_control_state` でも踏襲。

---

## 段階的実装手順

1. **Step 1: 仕様書**
   `docs/✅menu-and-footer/replay-control-bar-impl.md` を新設し、本計画の確定仕様を移植。README.md の目次に1行追加。

2. **Step 2: IPC 拡張（TDD: schema → dto → server → engine）**
   - `engine-client/tests/schema_v2_5_roundtrip.rs`（新設）に `PauseReplay` 等の serde roundtrip テストを追加（RED）
   - `engine-client/src/dto.rs` の `Command` / `AttemptedCommand` に variant 追加（GREEN）
   - `python/engine/schemas.py` に IpcMessage サブクラス追加 + SCHEMA_MINOR bump。`AttemptedCommand` / `ReplayOnlyCommand` Literal へ `PauseReplay` / `ResumeReplay` / `StepReplay` を追加。`ReplayHistoryChanged { has_history: bool }` を `schemas.py` と `dto.rs` に追加する。`_replay_snapshots` の push/pop のたびに Python → Rust に通知し、Rust 側の `ReplayBarState.replay_has_history` を更新する。
   - `python/engine/server.py` の op 分岐追加（`_check_replay_state` 連動）
   - `python/engine/replay_session.py` pacing ループに paused/step フラグ反映
   - 既存 schema バージョン互換テスト ([engine-client/tests/schema_v*.rs](engine-client/tests/)) を更新

3. **Step 3: `replay_control_state` 純関数 helper**
   `src/menu.rs` に追加。`tests/replay_control_state.rs` で 4 ボタン × 状態の表をテスト。

   状態遷移テスト表:
   ```
   状態          | ▶ 再生 | ⏸ 一時停止 | ⏭ Step+ | ⏮ Step- | ⏹ 停止
   idle          | ○      | ✗          | ✗        | ✗        | ✗
   running       | ✗      | ○          | ✗        | ✗        | ○
   paused        | ○      | ✗          | ○        | ○*       | ○
   ```
   （`○*` = `replay_has_history == true` のときのみ有効）

4. **Step 4: メニューバー 2 段化（live モード回帰なきこと）**
   - `BAR_HEIGHT` を `bar_height(mode)` に置換
   - Live モードでは下段なし（高さ 32px 維持） — 既存テスト [tests/widget_menu_bar_state.rs](tests/widget_menu_bar_state.rs) が PASS
   - `with_dropdown_overlay` の anchor を mode 連動に（`with_dropdown_overlay` の呼び出し元（`main.rs`）にも `mode` 引数を渡す変更が必要）
   - 新規テスト: replay モードで bar_height == 64.0 / live モードで 32.0

5. **Step 5: 入力欄・現在情報・再生制御ボタン配線**
   - `ReplayBarState` を `menu_bar_state.rs` に追加
   - `BarMessage` に新 variant 追加 + `update()` 拡張
   - `widget_menu_bar.rs` に `replay_control_row` / `replay_input_row` 実装
   - `main.rs` に新 BarMessage 受け handler — `PressPlay` は `validate()` 経由で `LoadReplayData + StartEngine`、`PressPause` → `Command::PauseReplay`、`PressStepForward` → `Command::StepReplay`、`PressStepBackward` → `Command::StepBackward`、`PressStop` → `Command::StopReplay` を各 IPC 送信

6. **Step 6: Replay モード File メニューの整理 + 既存モーダル経路の縮退**
   - `actions_for_mode(&AppMode::Replay)` を `[Open, Save, SaveAs, Quit]` に変更
   - `entries_for_menu` の `ReplayStop` 分岐を削除
   - `tests/menu_actions_cross_platform.rs` 期待値更新（`assert!(!actions.contains(&Action::ReplayStart))` 相当の否定 assert に書き換え）
   - `Action::ReplayStart` / `Action::ReplayStop` の enum variant 自体を削除する（`src/menu.rs` の `Action` enum から除去、`to_native_action` / `entries_for_menu` から対応アームも削除）。`to_native_action()` 内の `Action::ReplayStart` / `Action::ReplayStop` アームを削除する（accelerator 経路は現状存在しないが dispatch アームは除去対象）
   - `ReplayFormModal` 構造体および `Message::ShowReplayDialog` 経路は **当面残す** が、UI からは到達不能になる (validate() のみが共有ロジックとして使われる)。別フェーズでまとめて削除。

7. **Step 7: 仕様書最終化 + cargo fmt + cargo clippy + cargo test**

---

## 検証方法 (E2E)

`docs/✅menu-and-footer/widget-menu-bar-impl.md` の記述に従い、Rust unit / integration test で以下を担保。手動確認は debug build を起動して行う。

### 自動テスト

| テスト | 対象 |
|---|---|
| `engine-client/tests/schema_v2_5_roundtrip.rs`（新設・前バージョンから番号 bump） | `PauseReplay` / `ResumeReplay` / `StepReplay` の serde + Debug redaction |
| `engine-client/tests/schema_v*.rs` 新版 | 新 SCHEMA_MINOR の roundtrip |
| `python/engine/tests/test_replay_pause.py` (新規) | server.py で Pause→Step→Resume が正しく streaming pacing に反映されるか（既存 `test_replay_speed.py` 風）。受け入れ条件: (1) PauseReplay 送信後 200ms 以内に tick カウントが増加しないこと、(2) StepReplay 送信後に tick カウントが恰度 +1 増加すること、(3) ResumeReplay 後に一定時間内に tick カウントが +1 以上増加すること、(4) StepBackward 送信後に tick カウントが -1 方向に変化し portfolio が前値に復元されること |
| `python/engine/tests/test_replay_snapshot.py`（新規） | 3 step 進んで StepBackward × 2 後に portfolio が正確に復元されること（snapshot push → pop → restore の roundtrip） |
| `tests/replay_control_state.rs` (新規) | 4 ボタン × {idle, running, paused} 状態の enable 表 |
| `tests/widget_menu_bar_replay_layout.rs` (新規) | live: 1 段 / replay: 2 段、`with_dropdown_overlay` の anchor が連動すること。replay モード時 `top_offset` が 64 相当の値であること（`bar_height(Replay)` を参照していること）も assert 対象に含める |
| `tests/widget_menu_bar_state.rs` 拡張 | 新 `BarMessage` variant の `update` 遷移 |
| `tests/widget_menu_bar_state.rs` 拡張（ReplayBarState 状態遷移） | `InstrumentChanged` を `replay_running=true` 状態で送ったとき IPC が発行されないこと |
| `src/modal/replay_form.rs` 既存テスト | `validate()` `pub` 化後も全 PASS |

### 手動 E2E (e-station-review skill の e2e-testing パターン準拠)

1. `cargo run` で起動 → 初期 live モードでメニューバー高さ 32px 維持を目視
2. フッタートグルで replay モードに切替 → メニューバー下段に入力欄が出現
3. 戦略ファイル選択 → SCENARIO 自動 prefill が 5 入力欄に反映
4. ▶ 押下 → ダイアログを開かず replay 開始（既存挙動と同じ engine 起動シーケンス）
5. ⏸ 押下 → tick 配信が止まる（footer の現在日が更新停止）
6. ⏭ 連打 → 1 tick ずつ進む
7. ▶ 再押下 → resume 開始
8. ⏹ 押下 → 既存 `StopReplay` 経路で停止
9. 入力欄を変更 → 再生中はそのまま (反映されない)、▶ 再押下時に新セッションで反映
10. live モードに戻ると下段が消えて高さ 32px に戻る

---

## 既知のリスク / 非対応

- **Pause 中の dirty 判定 / モード切替**: Pause 中（`_replay_paused == true`）は engine_busy 扱いとし、フッタートグルが `disabled_reason` を返す形にすることで `mode-switch-impl.md` の不変条件と整合する。Step 5 実装時に `is_engine_busy()` ロジックへの paused フラグ考慮を追加する。
- **Snapshot 履歴の粒度依存**: `_replay_snapshots` の `maxlen=1000` は Daily 粒度では年単位で戻れる一方、Trade 粒度では数十分分に相当する。この非対称性は既知の制限であり、将来 `maxlen` を設定可能にすることで対応可能。
- **DPI スケーリング**: 64px 段組が高 DPI Windows で崩れないかは実機未確認 (既存 `widget-menu-bar-impl.md` の制限を踏襲)。
- **schema MINOR bump の波及**: 既存 schema_v*.rs テストすべてに新 op を伝播する必要があるため Step 2 で一括更新。
- **モード切替中の保護**: `MODE_SWITCHING` AtomicBool 中は再生制御ボタンも disable する (mode-switch-impl.md と整合)。

---

## レビュー反映 (2026-05-06, ラウンド 1)

`review-fixes-2026-05-06.md` の指摘を反映した確定変更点:

| Finding ID | 対応内容 |
|---|---|
| C-M3 | `StepReplay` 受付状態を `PAUSED` のみに修正（`RUNNING` 中は `EngineBusy`）。本文 IPC テーブル・enable 条件テーブル・docstring を更新。 |
| [M-1] | Step+ enabled 条件を `replay_running` → `replay_paused` に修正（本文テーブル行を更新）。 |
| [L-2] | `replay_control_state` シグネチャに `mode_switch_in_progress` 第 4 引数を追加し本文に反映。全ボタン disabled 行を表に追記。 |
| [H-5] | Rust 側の Pause/Resume/Step+ ハンドラは `|_| Message::Noop` パターン（`SetReplaySpeed` と同一）を採用。Python 側 Ack 追加は不要と確認。`StepBackward` も同パターンで実装済み。 |

SCHEMA_MINOR: 15（PauseReplay/ResumeReplay/StepReplay）→ 16（StepBackward/RestoreSnapshot/ReplayHistoryChanged）に bump 済み。

---

## レビュー反映 R2 (2026-05-06)

### 解消済み

| Finding ID | 対応内容 |
|---|---|
| R2-C1 | `engine_runner.py` の `start_backtest_replay_streaming` シグネチャに `push_snapshot` 引数を追加し、各粒度境界（per-item）で呼び出す実装を追加。`server.py` の `_run()` 内 `start_backtest_replay_streaming(...)` 呼び出しに `push_snapshot=self._push_replay_snapshot` を注入。`test_replay_snapshot.py` に `TestPushSnapshotInjection` クラス（2 テスト）を追加して注入経路を検証。 |
| R2-H2 | `src/main.rs` の `BarMessage::PressPause` ハンドラで IPC 失敗時に `self.replay_paused = true` を `ReplayPauseStateChanged { paused: false }` で即座にロールバック。エラーは `log::error!` で記録。 |
| R2-M1 | `BarMessage::ReplayPauseStateChanged { paused, .. }` ハンドラで `self.replay_paused = *paused` を追加し、`self.replay_paused`（`Flowsurface` フィールド）と `menu_bar.replay_bar.replay_paused` の乖離を解消。 |
| R2-M2 | `tests/widget_menu_bar_state.rs` に `replay_pause_state_changed_updates_both_paused_and_history` および `instrument_changed_updates_replay_bar_instrument_id` のソースインスペクションテスト 2 件を追加。 |
| R2-M3 | `docs/✅menu-and-footer/replay-control-bar-impl.md` の `BarMessage` variant 一覧から `ReplayCurrentDayChanged(String)` を削除し、`current_day` は `DateChangeMarker` IPC 経由で直接更新する設計判断の脚注を追記。 |

### 残存項目

| Finding ID | 状況 |
|---|---|
| R2-H1 | `EngineEvent::RestoreSnapshot` を `None`（サイレント黙殺）から `Message::RestoreSnapshotPending { step_index, ts_event_ms }` に昇格。ハンドラで `current_day = None` リセット + `log::debug!` 記録 + TODO コメント追加。chart pane のデータフラッシュ（完全実装）は次フェーズに持ち越し。 |
