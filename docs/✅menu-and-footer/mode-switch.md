# モード切替（live ⇄ replay）

ステータスバー（フッター）の `● LIVE` / `● REPLAY` バッジをクリックして
live / replay モードを切り替える。`モード（Mode）` トップレベルメニューは廃止済み。

---

## 切替フロー

1. フッタークリック / `Ctrl+Cmd+M` → `Action::SwitchAppMode(target)` を `NativeMenuAction` として dispatch
2. live → replay 切替時: **dirty チェック**（dirty かつ live モードのとき confirm dialog を表示）
3. engine プロセスを**再起動**（live engine と replay engine は内部状態が大きく異なるため再利用しない）
4. `engine-session.json` を engine プロセスの Drop で削除 → bootstrap で新トークン・新 PID で再生成
5. `APP_MODE` static を更新 → サブスクリプション・ペイン構成を新モードで再構築

---

## dirty チェック経路

confirm dialog の選択肢と後続 Action:

| 選択 | Action |
|------|--------|
| 保存して切替 | `SaveAndSwitchMode` |
| 破棄して切替 | `DiscardAndSwitchMode` |
| キャンセル | `GoBack`（`pending_mode_switch` / `_mode_switch_guard` を**一括クリア**） |

dirty チェック対象は **live モードのみ**（replay モードからの切替では dialog を出さない）。

---

## 不変条件

- `tachibana_orders.jsonl` を**書き換えない**（重複発注防止 WAL 保護）。切替時の参照は read-only のみ。
- 再入禁止: `_mode_switch_guard: Option<ModeSwitchGuard>` で連打を防ぐ。RAII で完了 / panic 時に必ず解除。
- `--mode` CLI 引数は起動時のみ。切替後の `restart()` でも CLI 値は読まない（`APP_MODE` static が正本）。
- `MODE_SWITCHING` AtomicBool 中は再生制御ボタン・`Ctrl/Cmd+M` も disabled。
- 同モードへの切替（`SwitchAppMode(current)`）は no-op として早期リターン。
- `ACTION::Save` / `ACTION::SaveAs` / `ExitRequested` / `NativeOpenFilePendingCheck` ハンドラ冒頭は `confirm_dialog.is_none()` ガードを通す（切替 confirm 表示中の多重起動を防ぐ）。

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action::SwitchAppMode(AppMode)` / `ModeToggleState` / `mode_toggle_state` |
| `src/main.rs` | `SwitchMode` ハンドラ群（`SaveAndSwitchMode` / `DiscardAndSwitchMode`）/ engine 再起動 / `_mode_switch_guard` |
| `src/native_menu.rs` | `widget_keyboard_subscription()`（`Ctrl/Cmd+M` 残置） |
| `src/widget_menu_bar.rs` | `ファイル（File）▼` のみ（`Mode` / `Tools` ボタン廃止済み） |
| `src/menu_bar_state.rs` | `TopMenu { File }`（`Mode` / `Tools` バリアント廃止済み） |

---

## テスト

| テスト | 対象 |
|--------|------|
| `tests/mode_switch_restart.rs` | engine 再起動経路 |
| `tests/mode_switch_in_flight_order.rs` | in-flight order の read-only 参照 |
| `tests/mode_switch_reentry.rs` | 再入禁止 / RAII guard |
| `tests/mode_switch_panic_recovery.rs` | guard の panic 時解除 |
| `tests/mode_switch_timeout_abort.rs` | engine 起動タイムアウト時の abort |
| `tests/mode_switch_accelerator_disabled.rs` | `MODE_SWITCHING` 中の `Ctrl/Cmd+M` 抑止 |
| `src/menu.rs` unit TT1〜TT3 | `mode_toggle_state` の enable 計算 |
| integration TT5〜TT6 | フッタークリック dispatch / dirty 時 confirm dialog |

---

## 既知の制限

- **同モードへの切替**: no-op として早期リターン（フッター側も `!current` のみ発行するため基本的に起こらない）。
- **`Ctrl/Cmd+M` の `is_live` ガード**: `SwitchMode` は `MODE_SWITCHING.load(Acquire)` で再入を抑制するが、モード依存の `is_live` ガードは入れていない（`SwitchAppMode` はモード非依存に有効）。
