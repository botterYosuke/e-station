# Replay コントロールバー

メニューバーを **2 段構成** に拡張し、replay 再生制御（▶ / ⏸ / ⏭ / ⏮ / ⏹）・
現在情報表示・入力欄を常設する。モーダルダイアログ往復を排除した。

---

## レイアウト

```
┌─────────────────────────────────────────────────────────────────────┐
│ 段 1 (32px) │ ファイル（File）▼ │ 戦略: foo.py  Current: 2025-03-15│
│ (常時)      │                   │  ▶ 再生  ⏸ 停止  ⏭ Step+  ⏮ Step-  ⏹│
├─────────────────────────────────────────────────────────────────────┤
│ 段 2 (32px) │ 銘柄: [1301.TSE ] │ 開始: [2025-01-06] 終了: [2025-03-31]│
│ (Replay のみ)│ 粒度: [Daily ▼] │ 初期資金: [1000000]               │
└─────────────────────────────────────────────────────────────────────┘
```

- **Live モード**: 段 1 のみ（32px）。再生制御ボタンは非表示。
- **Replay モード**: 段 1 + 段 2（64px）。

---

## 段 1：情報表示 + 再生制御

| 位置 | 内容 |
|------|------|
| 左 | `ファイル（File）▼` ドロップダウンボタン |
| 中 | `戦略: <ファイル名>  Current: <日付>` |
| 右 | `▶ 再生` `⏸ 一時停止` `⏭ Step+` `⏮ Step-` `⏹ 停止` |

---

## 段 2（Replay のみ）：入力欄

| 入力欄 | フィールド |
|--------|----------|
| 銘柄 | `instrument_id: String` |
| 開始日 | `start_date: String`（YYYY-MM-DD） |
| 終了日 | `end_date: String` |
| 粒度 | `granularity: Option<Granularity>`（pick_list） |
| 初期資金 | `initial_cash: String` |

入力欄の変更は **`▶ 再生` 押下時のみ** engine に反映（新セッションとして発行）。
再生中に変更しても即座に反映しない。

---

## 状態モデル（`src/menu_bar_state.rs`）

```rust
pub struct ReplayBarState {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub granularity: Option<Granularity>,
    pub strategy_file: Option<PathBuf>,
    pub initial_cash: String,
    pub current_day: Option<String>,   // DateChangeMarker IPC 受信で更新
    pub replay_paused: bool,
    pub replay_has_history: bool,      // ReplayHistoryChanged IPC 受信で更新
}
```

> `current_day` は `BarMessage` 経由ではなく、`main.rs` が `DateChangeMarker` IPC を
> 受けて直接 `menu_bar.replay_bar.current_day` を更新する。

### BarMessage 追加 variant

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

---

## 再生制御ボタン enable 条件

`replay_control_state(replay_running, replay_paused, replay_has_history, mode_switch_in_progress)`
（`src/menu.rs` の純関数）。`mode_switch_in_progress = true` のとき全ボタン disabled。

| ボタン | enabled 条件 |
|--------|------------|
| ▶ 再生 | `!replay_running \|\| replay_paused` |
| ⏸ 一時停止 | `replay_running && !replay_paused` |
| ⏭ Step+ | `replay_paused`（PAUSED 状態のみ；RUNNING 中は `EngineBusy`） |
| ⏮ Step- | `replay_running && replay_has_history` |
| ⏹ 停止 | `replay_running` |

---

## IPC 拡張（SCHEMA_MINOR = 16）

| Op | 方向 | 受理状態 | 説明 |
|----|------|---------|------|
| `PauseReplay` | Rust → Python | RUNNING | 再生を一時停止 |
| `ResumeReplay` | Rust → Python | PAUSED | 一時停止から再開 |
| `StepReplay` | Rust → Python | PAUSED のみ | 1 粒度分前進して再 pause |
| `StepBackward` | Rust → Python | PAUSED かつ snapshot 非空 | 1 粒度分後退 |
| `DateChangeMarker` | Python → Rust | — | 現在日付更新 |
| `RestoreSnapshot` | Python → Rust | — | pane 全置換モード開始（Step- 前送信） |
| `ReplayHistoryChanged` | Python → Rust | — | `has_history` フラグ更新 |

全コマンドは `request_id: String` フィールド付き。Rust 側 handler は
`|_| Message::Noop` パターン（`SetReplaySpeed` と同形）。

`PressPause` は IPC 失敗時に `ReplayPauseStateChanged { paused: false }` で
`replay_paused` を即座にロールバックし、`log::error!` で記録する。

---

## Python Pause セマンティクス（`replay_session.py`）

`_replay_paused_event: threading.Event`（set = running、cleared = paused）で CPU スピンを回避:

```python
while events:
    _replay_paused_event.wait()   # cleared = paused でここでブロック
    if consume_step() == 0 and get_paused():
        continue
    event = next_event()
    emit(event)
    if is_granularity_boundary(event):
        push_snapshot()
    await pace(get_multiplier())
```

| IPC | 操作 |
|-----|------|
| `PauseReplay` | `_replay_paused_event.clear()` |
| `ResumeReplay` | `_replay_paused_event.set()` |
| `StepReplay` | `_step_request += 1` |

`PressPlay` handler は `ReplayFormModal::validate()`（`pub fn` に昇格済み）で入力検証後、
`LoadReplayData + StartEngine` IPC を発行する。

---

## Snapshot ring buffer（`server.py`）

```python
@dataclass
class ReplaySnapshot:
    step_index: int           # 粒度ステップ番号（0-based）
    portfolio: dict           # positions / cash / realized_pnl
    open_orders: list[dict]
    strategy_state: object    # copy.deepcopy(strategy)
    ui_events: list[dict]     # この step で送出した UI イベント群
```

- `_replay_snapshots: deque[ReplaySnapshot]` with `maxlen=1000`（server.py）
- 各粒度境界（bar 完結時）に `push_snapshot` クロージャ経由で push

### StepBackward の流れ

1. `RestoreSnapshot` を Rust に先行送信（pane を全置換モードに切り替え）
2. `_replay_snapshots.pop()` で最新スナップショットを取り出す
3. `portfolio` / `open_orders` / `strategy_state` を復元
4. `ui_events` を Rust UI に再送信
5. `ReplayHistoryChanged { has_history: !_replay_snapshots.is_empty() }` を送信してボタン状態を更新

### deepcopy 失敗対策

`copy.deepcopy(strategy)` が例外を上げたステップはスナップショットを保存しない。
`logger.warning("snapshot deepcopy failed at step %d: %s", step_index, e)` を出力してスキップ。
その step は Step- 不可（`has_history = false` のままボタン disabled）。

---

## 既存コードの再利用

| 既存実装 | 再利用箇所 |
|----------|----------|
| `ReplayFormModal::validate()` (`src/modal/replay_form.rs`) | `PressPlay` handler の入力検証（`pub fn` に昇格） |
| `prefill_from_scenario` (同上) | 戦略ファイル選択後の SCENARIO 自動 prefill を `ReplayBarState` にも適用 |
| `Granularity` enum (同上) | `replay_input_row` の pick_list で流用 |
| `StopReplay` 経路 (`src/main.rs`) | `request_id` 採番・タイムアウト・`ForceStopReplay` フォールバックの実装パターンを踏襲 |
| `mode_toggle_state` 純関数 + テスト構成 (`src/menu.rs`) | `replay_control_state` の設計パターンを踏襲 |

---

## テスト

| テスト | 対象 |
|--------|------|
| `tests/replay_control_state.rs` | 5 ボタン × {idle, running, paused, paused+history, mode_switching} 状態 |
| `tests/widget_menu_bar_replay_layout.rs` | Live=32px / Replay=64px / dropdown anchor 連動 |
| `tests/widget_menu_bar_state.rs` | 新 `BarMessage` variant の `update` 遷移 / `ReplayBarState` 遷移 |
| `engine-client/tests/engine_command.rs` | `PauseReplay` / `ResumeReplay` / `StepReplay` / `StepBackward` の serde + Debug redaction |
| `engine-client/tests/schema_v2_4_nautilus.rs` | SCHEMA_MINOR = 16 の roundtrip |
| `python/tests/test_replay_pause.py` | Pause → Step+ → Resume → Step- の正確な状態復元 |
| `python/tests/test_replay_snapshot.py` | snapshot push/pop roundtrip（deepcopy 失敗対策含む） |

---

## 既知の制限

- **DPI スケーリング**: 64px 2 段組が高 DPI Windows で崩れないかは実機未確認。
- **deepcopy 失敗**: strategy が `copy.deepcopy` 非対応の step は Step- 不可。`logger.warning` で記録。
- **snapshot maxlen 非対称**: `maxlen=1000` は Daily では年単位、Trade では数十分分に相当。将来設定可能化候補。
- **モーダル残存**: `replay_form_modal` 経路は当面 `Action::ReplayStart` 経由で残存（後フェーズで削除予定）。UI からは到達不能。
- **Pause 中の dirty 判定**: Pause 中（`replay_paused = true`）は engine_busy 扱いとし、フッタートグルは `disabled_reason` を返す（`mode-switch.md` の不変条件と整合）。
