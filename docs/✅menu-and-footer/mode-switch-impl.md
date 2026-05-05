# モード切替メニュー（live ⇄ replay）

メニューバーの `モード（Mode）` サブメニューから live / replay モードを切り替える機能。
起動時の `--mode {live|replay}` 引数を経由せずにアプリ内でモードを変更できる。

---

## メニュー構成

| ラベル | 状態 | 動作 |
|--------|------|------|
| `ライブ（Live）` | 現在 live なら `✓` 付き | replay → live に切替 |
| `リプレイ（Replay）` | 現在 replay なら `✓` 付き | live → replay に切替 |

- ラジオ表示: `MenuEntry.checked: Option<bool>` で `Some(true)` / `Some(false)` /
  `None` の三状態を持つ（ファイル系項目と整列を揃えるため `bool` に潰さない）
- enable / tooltip / `checked` は `mode_menu_items(current_mode)` が返す
  `MenuEntry` リストに集約

---

## 切替時の挙動

### 基本フロー

1. メニュー選択 → `Action::SwitchAppMode(target)` を `NativeMenuAction` として dispatch
2. live → replay 切替時は **dirty チェック**を [save-menu-impl.md §confirm dialog の発火経路](./save-menu-impl.md#confirm-dialog-の発火経路)
   と共有（`SaveAndSwitchMode` / `DiscardAndSwitchMode` / `GoBack`）
3. **engine プロセスを再起動** — live engine と replay engine は内部状態が大きく
   異なるため再利用しない
4. `engine-session.json` が engine プロセスの Drop で削除され、再起動後の
   bootstrap で新トークン・新 PID で再生成される
5. `APP_MODE` static を更新し、サブスクリプション・ペイン構成を新モードで再構築

### 5 軸 matrix（不変条件）

`(現モード, 切替先, In-flight order, EngineBusy, submit_in_flight)` の 5 軸で挙動を
定義する：

| 軸 | 値 | 影響 |
|----|----|------|
| 現モード | live / replay | dirty チェック対象は live のみ |
| 切替先 | live / replay | 同モードへの切替は no-op |
| In-flight order | あり / なし | live → replay 切替時のみ参照（read-only） |
| EngineBusy | true / false | true 時は切替を保留しユーザーに通知 |
| submit_in_flight | true / false | W&B submit 中は切替を抑制（[wandb-submit-impl.md](./wandb-submit-impl.md) 参照） |

不変条件：

- `tachibana_orders.jsonl` を **書き換えない**（重複発注防止 WAL を保護）。
  切替時の参照は read-only のみ
- 再入禁止: `_mode_switch_guard: Option<ModeSwitchGuard>` で連打を防ぐ。RAII で
  完了 / panic 時に必ず解除される
- `--mode` CLI 引数は起動時のみ。切替後の `restart()` でも CLI 値は読まない
  （`APP_MODE` static が正本）

リグレッションガード:

- `tests/mode_menu_items.rs` — `mode_menu_items(current)` が返す `MenuEntry`
- `tests/mode_switch_restart.rs` — engine 再起動経路
- `tests/mode_switch_in_flight_order.rs` — in-flight order の read-only 参照
- `tests/mode_switch_blocks_during_submit.rs` — `submit_in_flight` 抑制
- `tests/mode_switch_reentry.rs` — 再入禁止
- `tests/mode_switch_panic_recovery.rs` — guard の panic 時解除
- `tests/mode_switch_timeout_abort.rs` — engine 起動タイムアウト時の abort
- `tests/mode_switch_accelerator_disabled.rs` — 切替中のアクセラレータ抑制
- `tests/wandb_modeswitch_lock_order.rs` — `CURRENT_PATH` / `MENU_IDS` /
  `_mode_switch_guard` の lock 取得順序

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action::SwitchAppMode(AppMode)` / `MenuEntry` / `mode_menu_items` |
| `src/main.rs` | `SwitchMode` ハンドラ群（`SaveAndSwitchMode` / `DiscardAndSwitchMode`） / engine 再起動 / `_mode_switch_guard` |
| `src/native_menu.rs` | `Action` enum / `widget_keyboard_subscription`（Ctrl/Cmd+M） |
| `src/widget_menu_bar.rs` | iced widget の Mode サブメニュー（全 OS） |

---

## 既知の制限

- **同モードへの切替**: `SwitchAppMode(current)` は no-op として早期リターンする
  （メニュー側でも disable を考慮してよいが、両系統で同じ guard を通る）
- **macOS Cmd+Q との関係**: モード切替の dirty 確認が出る経路と Cmd+Q 経路は別。
  Cmd+Q は OS 側で直接処理されるため [save-menu-impl.md §既知の制限](./save-menu-impl.md#既知の制限)
  を参照
