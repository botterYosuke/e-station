# モード切替（フッタートグル）（live ⇄ replay）

ステータスバー（フッター）の `● LIVE` / `● REPLAY` バッジをクリックして
live / replay モードを切り替える機能。起動時の `--mode {live|replay}` 引数を
経由せずにアプリ内でモードを変更できる。

> **旧 `モード（Mode）` トップレベルメニューは廃止**。UI 改修の設計経緯は
> [`mode-toggle-redesign.md`](./mode-toggle-redesign.md) を参照。

---

## フッタートグル UI

| 状態 | 表示 | 色 | カーソル |
|------|------|----|----------|
| live（操作可能） | `● LIVE` | 緑 `(0.2, 0.75, 0.3)` | pointer |
| replay（操作可能） | `● REPLAY` | アンバー `(0.9, 0.6, 0.1)` | pointer |
| 切替中 / 抑制中 | `● LIVE …` / `● REPLAY …` | 既存色を 50 % 減光 | default |
| hover（操作可能時） | バッジ背景にうっすらハイライト | 同上 | pointer |

- クリック: `Action::SwitchAppMode(target)` を `NativeMenuAction` として dispatch
  （`target` = 現在と逆のモード）
- ツールチップ（操作可能時）: `クリックで Replay に切替` / `クリックで Live に切替`
- ツールチップ（抑制中）: 抑制理由を表示（下記 enable 計算参照）
- `Ctrl/Cmd+M` アクセラレータはキーボード導線として**併存**（廃止しない）
- dirty 状態は **disabled 理由に含めない**。クリック後に save/discard confirm dialog へ遷移

### enable 計算（`mode_toggle_state`）

```rust
pub struct ModeToggleState {
    pub current: AppMode,
    pub enabled: bool,
    pub disabled_reason: Option<&'static str>,
}

pub fn mode_toggle_state(
    current: AppMode,
    engine_busy: bool,
    mode_switch_in_progress: bool,
) -> ModeToggleState { ... }
```

抑制理由の優先順位（高 → 低）:

1. `mode_switch_in_progress` → `Engine を再起動中…`
2. `engine_busy` → `Engine がビジーです`
3. それ以外 → `enabled = true`

---

## 切替時の挙動

### 基本フロー

1. フッターバッジクリック（または `Ctrl/Cmd+M`）→ `Action::SwitchAppMode(target)` を `NativeMenuAction` として dispatch
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

不変条件：

- `tachibana_orders.jsonl` を **書き換えない**（重複発注防止 WAL を保護）。
  切替時の参照は read-only のみ
- 再入禁止: `_mode_switch_guard: Option<ModeSwitchGuard>` で連打を防ぐ。RAII で
  完了 / panic 時に必ず解除される
- `--mode` CLI 引数は起動時のみ。切替後の `restart()` でも CLI 値は読まない
  （`APP_MODE` static が正本）

リグレッションガード:

- `tests/mode_switch_restart.rs` — engine 再起動経路
- `tests/mode_switch_in_flight_order.rs` — in-flight order の read-only 参照
- `tests/mode_switch_reentry.rs` — 再入禁止
- `tests/mode_switch_panic_recovery.rs` — guard の panic 時解除
- `tests/mode_switch_timeout_abort.rs` — engine 起動タイムアウト時の abort
- `tests/mode_switch_accelerator_disabled.rs` — `Ctrl/Cmd+M` の `MODE_SWITCHING` 中抑止
  （フッター disabled と独立した軸として**維持**。フッター側は TT2〜TT4 で別途保証）
- `src/menu.rs` unit TT1〜TT4 — `mode_toggle_state(...)` の enable 計算
- integration TT5〜TT6 — フッタークリック dispatch / dirty 時 confirm dialog

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action::SwitchAppMode(AppMode)` / `ModeToggleState` / `mode_toggle_state` |
| `src/main.rs` | `status_bar(ModeToggleState)` / `SwitchMode` ハンドラ群（`SaveAndSwitchMode` / `DiscardAndSwitchMode`） / engine 再起動 / `_mode_switch_guard` |
| `src/native_menu.rs` | `Action` enum / `widget_keyboard_subscription`（Ctrl/Cmd+M 残置） |
| `src/widget_menu_bar.rs` | iced widget bar（`File` 1 本立て。`Mode` / `Tools` ボタン廃止） |
| `src/menu_bar_state.rs` | `TopMenu { File }`（`Mode` / `Tools` バリアント廃止） |

---

## 既知の制限

- **同モードへの切替**: `SwitchAppMode(current)` は no-op として早期リターンする
  （フッター側でも `!current` のみ発行するため基本的に起こらないが、guard は通る）
- **macOS Cmd+Q との関係**: モード切替の dirty 確認が出る経路と Cmd+Q 経路は別。
  Cmd+Q は OS 側で直接処理されるため [save-menu-impl.md §既知の制限](./save-menu-impl.md#既知の制限)
  を参照
- **右クリック未対応**: フッターバッジの右クリックは現フェーズ未実装。将来の
  ミニメニュー検討は [`mode-toggle-redesign.md §未決事項`](./mode-toggle-redesign.md#未決事項) を参照
