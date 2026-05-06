# Replay コントロールバー実装仕様

メニューバーを **2 段構成** に拡張し、replay 再生制御（再生 / 一時停止 / Step+ /
Step- / 停止）・現在情報表示・入力欄を常設する。モーダルダイアログ往復を排除する。

---

## レイアウト概要

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 段 1 (32px) │ ファイル（File）▼ │ 戦略: foo.py  Current: 2025-03-15    │
│             │                   │  ▶ 再生  ⏸ 停止  ⏭ Step+  ⏮ Step-  ⏹│
├─────────────────────────────────────────────────────────────────────────┤
│ 段 2 (32px) │ 銘柄: [1301.TSE ] │ 開始: [2025-01-06] 終了: [2025-03-31]│
│ (Replayのみ)│ 粒度: [Daily ▼]  │ 初期資金: [1000000]                  │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Live モード**: 段 1 のみ（32px）。段 2 は非表示。再生制御ボタンも非表示。
- **Replay モード**: 段 1 + 段 2（64px）。

---

## 仕様確定事項

| 項目 | 決定 |
|---|---|
| 高さ | Live: 32px。Replay: 64px（`bar_height(mode)` 関数化）。 |
| Step backward の単位 | 粒度に依存（Daily=1日・Minute=1分・Trade=1取引）。 |
| Step backward 状態復元 | 戦略状態（ポジション/PnL/注文）を正確に復元。Python engine snapshot ring buffer 経由。 |
| 入力欄の反映タイミング | 「▶ 再生」押下時のみ新セッションとして engine に発行。再生中に変更しても即座に反映しない。 |
| File メニュー項目 | Live / Replay 共通: `開く` / `上書き保存` / `名前を付けて保存` / `終了`。`リプレイを開始` / `リプレイを停止` は削除済み。 |
| 既存モーダル | `Action::ReplayStart` によるモーダル経路は当面併存（後フェーズで削除）。 |

---

## IPC 拡張（schema MINOR bump）

新 IPC op（いずれも `request_id: str` フィールド付き）:

| Op | 型 | 説明 |
|---|---|---|
| `PauseReplay` | `IpcMessage` | 再生を一時停止。受理状態: `RUNNING`。 |
| `ResumeReplay` | `IpcMessage` | 一時停止から再開。受理状態: `PAUSED`。 |
| `StepReplay` | `IpcMessage` | 1 粒度分前進して再 pause。受理状態: `PAUSED` のみ（`RUNNING` 中は `EngineBusy`）。 |
| `StepBackward` | `IpcMessage` | 1 粒度分後退（snapshot 復元）。受理状態: `PAUSED` かつ snapshot 非空。 |

**Pause セマンティクス**（`replay_session.py` の pacing ループ）:

```python
# pacing ループの疑似コード
while events:
    if get_paused() and consume_step() == 0:
        await asyncio.sleep(0.01)   # busy-wait 回避
        continue
    event = next_event()
    emit(event)
    record_ui_event(event)
    if is_granularity_boundary(event):
        push_snapshot()
    await pace(get_multiplier())
```

`StepReplay` で `step_request += 1`、`StepBackward` で末尾 snapshot を pop して状態復元。

---

## Snapshot ring buffer 設計

```python
@dataclass
class ReplaySnapshot:
    step_index: int           # 粒度ステップ番号（0-based）
    portfolio: dict           # positions / cash / realized_pnl のシリアライズ
    open_orders: list[dict]   # 未約定注文リスト
    strategy_state: object    # copy.deepcopy(strategy) オブジェクト
    ui_events: list[dict]     # この step で送出した UI イベント群（再送信用）
```

- `_replay_snapshots: deque[ReplaySnapshot]` with `maxlen=1000`（server.py）
- 各粒度境界（bar 完結時）に `push_snapshot` クロージャ経由で push
- `StepBackward` 受信時:
  1. `_replay_snapshots.pop()` で最新スナップショットを取り出す
  2. `portfolio` / `open_orders` / `strategy_state` を復元
  3. `ui_events` を Rust UI に再送信
  4. `has_history` フラグ（`len > 0`）を Rust に通知
- **deepcopy 失敗対策**: `copy.deepcopy(strategy)` が例外を上げたステップはスナップショットを保存しない（`has_history` が false のままでボタン disabled）。

---

## 再生制御ボタン enable 条件

`replay_control_state(replay_running, replay_paused, replay_has_history, mode_switch_in_progress) -> ReplayControlState` を `src/menu.rs` に追加（純関数）。`mode_switch_in_progress=true` のとき全ボタン disabled。

| ボタン | 表示 | enabled 条件 |
|---|---|---|
| 再生 | ▶ | `!replay_running` または `replay_paused` |
| 一時停止 | ⏸ | `replay_running && !replay_paused` |
| Step+ | ⏭ | `replay_paused`（`PAUSED` 状態のみ；`RUNNING` 中は `EngineBusy`） |
| Step- | ⏮ | `replay_running && replay_has_history` |
| 停止 | ⏹ | `replay_running` |

---

## 状態モデル追加（`src/menu_bar_state.rs`）

```rust
pub struct ReplayBarState {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub granularity: Option<Granularity>,
    pub strategy_file: Option<PathBuf>,
    pub initial_cash: String,
    pub current_day: Option<String>,
    pub replay_paused: bool,
    pub replay_has_history: bool,
}
```

`BarMessage` 追加 variant:

```rust
InstrumentChanged(String),
StartDateChanged(String),
EndDateChanged(String),
GranularityChanged(Granularity),
InitialCashChanged(String),
PickStrategyFile,
PressPlay,
PressPause,
PressStepForward,
PressStepBackward,
PressStop,
ReplayPauseStateChanged { paused: bool, has_history: bool },
```

> **注**: `ReplayCurrentDayChanged(String)` は **存在しない**。`current_day` は
> `main.rs` 内で `DateChangeMarker` IPC を受信して
> `menu_bar.replay_bar.current_day` に直接更新する設計判断であり、
> `BarMessage` 経由ではない。

---

## 既存コードの再利用

| 既存実装 | 再利用箇所 |
|---|---|
| `ReplayFormModal::validate()` ([src/modal/replay_form.rs:111](../src/modal/replay_form.rs#L111)) | `PressPlay` handler で入力検証。`pub fn` に昇格。 |
| `prefill_from_scenario` ([src/modal/replay_form.rs:171](../src/modal/replay_form.rs#L171)) | 戦略ファイル選択後の SCENARIO 自動 prefill を `ReplayBarState` にも適用。 |
| `Granularity` enum ([src/modal/replay_form.rs:9](../src/modal/replay_form.rs#L9)) | `replay_input_row` の `pick_list` で流用。 |
| `StopReplay` 経路 ([src/main.rs:3379](../src/main.rs#L3379)) | `request_id` 採番・タイムアウト・`ForceStopReplay` フォールバックを `PauseReplay` 等でも踏襲。 |
| `mode_toggle_state` 純関数 + テスト構成 ([src/menu.rs](../src/menu.rs)) | `replay_control_state` の設計パターンを踏襲。 |

---

## テスト一覧

| テスト | 対象 |
|---|---|
| `engine-client/tests/engine_command.rs` 拡張 | `PauseReplay` / `ResumeReplay` / `StepReplay` / `StepBackward` の serde + Debug redaction |
| `engine-client/tests/schema_v*.rs` 新版 | 新 SCHEMA_MINOR の roundtrip |
| `python/engine/tests/test_replay_pause.py`（新規） | Pause → Step+ → Resume → Step- の正確な状態復元 |
| `tests/replay_control_state.rs`（新規） | 5 ボタン × {idle, running, paused, paused+history} 状態 |
| `tests/widget_menu_bar_replay_layout.rs`（新規） | live=32px / replay=64px、dropdown anchor 連動 |
| `tests/widget_menu_bar_state.rs` 拡張 | 新 `BarMessage` variant の `update` 遷移 |
| `tests/menu_actions_cross_platform.rs` 更新 | Replay モードの期待値が Live と一致すること |
| `src/modal/replay_form.rs` 既存テスト | `validate()` `pub` 化後も全 PASS |

---

## 既知の制限

- **deepcopy 失敗**: strategy が `copy.deepcopy` 非対応の場合、その step は Step- 不可。ログに警告を出す。
- **DPI スケーリング**: 64px 2 段組が高 DPI Windows で崩れないかは実機未確認（既存制限を踏襲）。
- **`MODE_SWITCHING` 中の保護**: `MODE_SWITCHING` AtomicBool 中は再生制御ボタンも disable（`mode-switch-impl.md` と整合）。
- **モーダル残存**: `replay_form_modal` 経路は当面 `Action::ReplayStart` 経由で残る。後フェーズで削除予定。
