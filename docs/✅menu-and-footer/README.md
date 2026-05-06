# メニューバー / フッター / Replay コントロール — 仕様書

メインウィンドウの **メニューバー**・**ステータスバー（フッター）**・
**File メニュー**・**モード切替**・**Replay 再生制御** の現行仕様。
実装は `sasa/spicy-gosling` ブランチで完結済み。

---

## 全体レイアウト

![menu_layout](./assets/menu-layout.png)

```
┌─────────────────────────────────────────────────────────────────────┐
│ 段 1 (32px) │ ファイル（File）▼ │ 戦略: foo.py  Current: 2025-03-15│
│ (常時)      │                   │  ▶ 再生  ⏸ 停止  ⏭ Step+  ⏮ Step-  ⏹│
├─────────────────────────────────────────────────────────────────────┤
│ 段 2 (32px) │ 銘柄: [1301.TSE ] │ 開始: [2025-01-06] 終了: [2025-03-31]│
│ (Replay のみ)│ 粒度: [Daily ▼] │ 初期資金: [1000000]               │
├─────────────────────────────────────────────────────────────────────┤
│  sidebar    │                    dashboard                           │
├─────────────┴────────────────────────────────────────────────────────┤
│  ● LIVE / ● REPLAY   ← ステータスバー (20px)                         │
└──────────────────────────────────────────────────────────────────────┘
```

| モード | メニューバー高さ | 下段表示 | ステータスバー色 |
|--------|----------------|----------|----------------|
| Live   | 32 px          | なし     | 緑 `(0.2, 0.75, 0.3)` |
| Replay | 64 px          | あり     | アンバー `(0.9, 0.6, 0.1)` |

---

## ドキュメント

- [`menu-bar.md`](./menu-bar.md) — **Widget メニューバー**（iced 単一実装・アクセラレータ・File メニュー項目）
- [`replay-control.md`](./replay-control.md) — **Replay コントロールバー**（2 段化・再生制御・IPC・snapshot ring buffer）
- [`footer.md`](./footer.md) — **ステータスバー（フッター）**（モードバッジ・クリックトグル・enable 計算）
- [`mode-switch.md`](./mode-switch.md) — **モード切替**（フロー・engine 再起動・不変条件）
- [`file-menu.md`](./file-menu.md) — **File メニュー / Save**（CURRENT_PATH・dirty 判定・SCENARIO 経路）

---

## 主要ソースファイル

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action` / `actions_for_mode` / `mode_toggle_state` / `replay_control_state` |
| `src/menu_bar_state.rs` | `TopMenu` / `BarMessage` / `ReplayBarState` / `update()` |
| `src/widget_menu_bar.rs` | iced widget bar / dropdown / `replay_control_row` / `replay_input_row` |
| `src/native_menu.rs` | `widget_keyboard_subscription()`（accelerator、全 OS） |
| `src/main.rs` | `NativeMenuAction` ハンドラ群 / `status_bar` / `build_state_json` / `CURRENT_PATH` / `_mode_switch_guard` |
| `src/modal/replay_form.rs` | `validate()` (pub) / `prefill_from_scenario` / `Granularity` |
| `python/engine/scenario.py` | SCENARIO 抽出・検証・atomic write |
| `python/engine/server.py` | Pause/Resume/Step IPC ハンドラ / `_replay_snapshots` / `_replay_paused_event` |
| `python/engine/replay_session.py` | pacing ループ（paused・step カウンタ・snapshot push） |
| `engine-client/src/dto.rs` | `Command` / `EngineEvent` 追加 variant |
| `python/engine/schemas.py` | `PauseReplay` 等 `IpcMessage` サブクラス / `SCHEMA_MINOR = 16` |
