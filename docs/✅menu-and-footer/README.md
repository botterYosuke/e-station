# メニューバー / フッター / Save 機能

メインウィンドウの **iced widget メニューバー**（全 OS 統一）・**ステータスバー
（フッター）**・**File メニュー（Open / Save / Save As）**・**モード切替**・
**Tools (W&B) メニュー**の実装仕様。

> 旧構成では Win/macOS が muda（OS ネイティブ）、Linux が iced widget の
> 二系統だった。muda は完全廃止し、全プラットフォームで `widget_menu_bar` に
> 統一済み。詳細は [widget-menu-bar-impl.md](./widget-menu-bar-impl.md)。

実装は完了済み（`cargo test --workspace` 全 PASS）。

---

## ドキュメント

- [`widget-menu-bar-impl.md`](./widget-menu-bar-impl.md) — **メニューバー実装の主要仕様**（iced widget、全 OS 統一）
- [`footer-impl.md`](./footer-impl.md) — ステータスバー（フッター）の要件・設計・テスト
- [`save-menu-impl.md`](./save-menu-impl.md) — File メニュー / Save 実装（Open / Save / Save As / dirty 判定 / `CURRENT_PATH` / replay 戦略 `.py` `SCENARIO` 経路）
- [`mode-switch-impl.md`](./mode-switch-impl.md) — フッタートグルによる live ⇄ replay 切替・engine 再起動・5 軸 matrix（`モード（Mode）` トップレベルメニューは廃止）
- [`mode-toggle-redesign.md`](./mode-toggle-redesign.md) — UI 改修計画（メニュー → フッタートグル移行の設計判断・実装ステップ・テスト方針）
- [`wandb-submit-impl.md`](./wandb-submit-impl.md) — Tools サブメニューによる W&B Submit / Sign in / Sign out / RunBuffer / submit subprocess
- [`native-menu-bar-impl.md`](./native-menu-bar-impl.md) — **(歴史)** muda 時代の実装記録（2026-04-30）。muda 廃止後は archive 扱い

---

## 機能サマリ

### File メニュー

| モード | メニュー項目 | アクセラレータ |
|--------|------------|----------------|
| live | `開く…（Open）` / `上書き保存（Save）` / `名前を付けて保存…（Save As）` / `終了` | Ctrl+O / Ctrl+S / Ctrl+Shift+S / Ctrl+Q |
| replay | `Replay を開始…` / `Replay を停止` / `開く…（Open）` (.py) / `名前を付けて保存…（Save As）` (.py) / `終了` | — / — / Ctrl+O / Ctrl+Shift+S / Ctrl+Q |

詳細: [`save-menu-impl.md`](./save-menu-impl.md)

### フッタートグル（モード切替）

`モード（Mode）` トップレベルメニューは廃止。ステータスバーのバッジ自体が
クリック可能なトグルになった（詳細: [`mode-switch-impl.md`](./mode-switch-impl.md)）。

| 状態 | 表示 | 色 | 動作 |
|------|------|----|------|
| live（操作可能） | `● LIVE` | 緑 `(0.2, 0.75, 0.3)` | クリックで replay に切替 |
| replay（操作可能） | `● REPLAY` | アンバー `(0.9, 0.6, 0.1)` | クリックで live に切替 |
| 切替中 / 抑制中 | `● LIVE …` / `● REPLAY …` | 既存色を 50 % 減光 | クリック無効 |

- `Ctrl/Cmd+M` アクセラレータ（キーボード導線）は維持
- dirty 時はクリック後に save/discard confirm dialog へ遷移（disable はしない）
- 抑制理由優先順位: `mode_switch_in_progress` > `submit_in_flight` > `engine_busy`

### Tools サブメニュー（W&B 連携）

| ラベル | enable 条件 |
|--------|-------------|
| `W&B に登録…（Submit to W&B）` | サインイン中 + 未送信 run あり + submit 非実行中 |
| `送信履歴を開く（Open Submission Log）` | 履歴ファイルあり |
| `バッファを削除…（Clear Run Buffer）` | RunBuffer に run あり |
| `W&B にログイン…（Sign in to W&B）` | サインアウト中 |
| `W&B からログアウト（Sign out of W&B）` | サインイン中 |

詳細: [`wandb-submit-impl.md`](./wandb-submit-impl.md)

### ステータスバー

メインウィンドウ最下部に高さ 20 px の固定バーを表示。

| モード | 表示 | 色 |
|--------|------|-----|
| live | `● LIVE` | 緑 `(0.2, 0.75, 0.3)` |
| replay | `● REPLAY` | アンバー `(0.9, 0.6, 0.1)` |

- popout ウィンドウには **表示しない**
- モーダル展開中はオーバーレイの下に隠れる（[`footer-impl.md`](./footer-impl.md) §アーキテクチャ 参照）

---

## プラットフォーム分岐

muda 廃止後は **OS 別分岐は最小化**。実質的な OS 別動作は keyboard accelerator の
主修飾キー（Ctrl vs Cmd）とラベル表示のみ。

| 経路 | Windows | macOS | Linux |
|------|---------|-------|-------|
| メニュー bar | iced widget（共通） | iced widget（共通） | iced widget（共通） |
| アクセラレータ | `keyboard::listen()` + `physical_key`（Ctrl） | 同左（Ctrl または Cmd / logo） | 同左（Ctrl） |
| Quit | `Action::Quit` → `ExitRequested`（dirty 通る） | 同左（Cmd+Q もキーボード経由のため dirty 通る） | 同左 |
| ショートカット表示 | `Ctrl+O` 等 | `Cmd+O` 等 | `Ctrl+O` 等 |

不変条件: dispatch 経路は **`Message::NativeMenuAction(Action)` の単一系統**に
正規化される。menu 項目の集合計算（`actions_for_mode` / `tools_actions_for_state`）
および フッタートグルの enable 計算（`mode_toggle_state`）は `src/menu.rs` に集約され、
全 OS で同じ集合を返す。（`mode_menu_items` は廃止済み）

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action` enum / `MenuEntry` / 項目集合の cross-platform 計算 / `ModeToggleState` / `mode_toggle_state` |
| `src/menu_bar_state.rs` | widget bar 用の `TopMenu`（`File` / `Tools` 2 本立て） / `BarMessage` / `update`（全 OS） |
| `src/native_menu.rs` | `Action` enum / `widget_keyboard_subscription`（全 OS、accelerator 経路のみ） |
| `src/widget_menu_bar.rs` | iced widget bar / dropdown overlay（全 OS） |
| `src/main.rs` | `NativeMenu*` ハンドラ群・`build_state_json` / `is_dirty` / `CURRENT_PATH` / `_mode_switch_guard` / footer 合成 |
| `src/cli.rs` | `--saved-state <PATH>` 引数 |
| `src/modal/replay_form.rs` | replay フォーム（`prefill_from_scenario` / `set_strategy_file_only`） |
| `src/modal/wandb_signin.rs` / `wandb_submit.rs` | W&B モーダル |
| `src/wandb_auth.rs` / `wandb_submit_proc.rs` / `mask_secrets.rs` | 認証状態 / submit subprocess / ログマスカ |
| `python/engine/scenario.py` | 戦略 `.py` の `SCENARIO` 抽出・書き戻し |
| `python/engine/run_buffer.py` | replay 結果の RunBuffer 書き出し |
| `examples/wandb/submit_run.py` / `check_auth.py` / `pii_scrub.py` | W&B 送信 subprocess |
| `Cargo.toml` | （muda 依存は廃止済み） |
