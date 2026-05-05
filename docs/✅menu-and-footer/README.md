# メニューバー / フッター / Save 機能

メインウィンドウの **OS ネイティブメニューバー**・**ステータスバー（フッター）**・
**File メニュー（Open / Save / Save As）**・**モード切替**・**Tools (W&B) メニュー**
の実装仕様。

実装は完了済み（`cargo test --workspace` 全 PASS）。

---

## ドキュメント

- [`native-menu-bar-impl.md`](./native-menu-bar-impl.md) — OS ネイティブメニューバー（muda、Win/macOS）の要件・設計・テスト
- [`footer-impl.md`](./footer-impl.md) — ステータスバー（フッター）の要件・設計・テスト
- [`save-menu-impl.md`](./save-menu-impl.md) — File メニュー / Save 実装（Open / Save / Save As / dirty 判定 / `CURRENT_PATH` / replay 戦略 `.py` `SCENARIO` 経路）
- [`mode-switch-impl.md`](./mode-switch-impl.md) — `モード（Mode）` サブメニューによる live ⇄ replay 切替・engine 再起動・5 軸 matrix
- [`widget-menu-bar-impl.md`](./widget-menu-bar-impl.md) — Linux 自前メニューバー（iced widget、muda 非対応プラットフォーム向け）
- [`wandb-submit-impl.md`](./wandb-submit-impl.md) — Tools サブメニューによる W&B Submit / Sign in / Sign out / RunBuffer / submit subprocess

---

## 機能サマリ

### File メニュー

| モード | メニュー項目 | アクセラレータ |
|--------|------------|----------------|
| live | `開く…（Open）` / `上書き保存（Save）` / `名前を付けて保存…（Save As）` / `終了` | Ctrl+O / Ctrl+S / Ctrl+Shift+S / Ctrl+Q |
| replay | `Replay を開始…` / `Replay を停止` / `開く…（Open）` (.py) / `名前を付けて保存…（Save As）` (.py) / `終了` | — / — / Ctrl+O / Ctrl+Shift+S / Ctrl+Q |

詳細: [`save-menu-impl.md`](./save-menu-impl.md)

### モード（Mode）サブメニュー

| ラベル | 状態 |
|--------|------|
| `ライブ（Live）` | 現在 live なら `✓` |
| `リプレイ（Replay）` | 現在 replay なら `✓` |

切替時は engine プロセスを再起動。dirty チェック / in-flight order / W&B submit
in-flight の 3 種類のガードで安全に遷移する。詳細: [`mode-switch-impl.md`](./mode-switch-impl.md)

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

| 経路 | Windows | macOS | Linux |
|------|---------|-------|-------|
| メニュー bar | muda（OS native） | muda（OS native） | iced widget（自前） |
| アクセラレータ | muda の MenuItem accelerator | muda + `PredefinedMenuItem::quit` | iced `keyboard::on_key_press` subscription |
| Quit | `Action::Quit` → `ExitRequested` | Cmd+Q（OS 直接処理、dirty 迂回） / Ctrl+Q（dirty 通る） | `Action::Quit` → `ExitRequested` |

不変条件: dispatch 経路は **`Message::NativeMenuAction(Action)` の単一系統**に
正規化される。menu 項目の集合計算（`actions_for_mode` / `mode_menu_items` /
`tools_actions_for_state`）は `src/menu.rs` に集約され、全 OS で同じ集合を返す。

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/menu.rs` | `Action` enum / `MenuEntry` / 項目集合の cross-platform 計算 |
| `src/menu_bar_state.rs` | widget bar 用の `TopMenu` / `BarMessage` / `update`（cfg gate なし） |
| `src/native_menu.rs` | muda 統合・Subscription・MENU_IDS poison 復旧（Win/macOS） |
| `src/widget_menu_bar.rs` | iced widget bar / dropdown overlay（Linux 限定） |
| `src/main.rs` | `NativeMenu*` ハンドラ群・`build_state_json` / `is_dirty` / `CURRENT_PATH` / `_mode_switch_guard` / footer 合成 |
| `src/cli.rs` | `--saved-state <PATH>` 引数 |
| `src/modal/replay_form.rs` | replay フォーム（`prefill_from_scenario` / `set_strategy_file_only`） |
| `src/modal/wandb_signin.rs` / `wandb_submit.rs` | W&B モーダル |
| `src/wandb_auth.rs` / `wandb_submit_proc.rs` / `mask_secrets.rs` | 認証状態 / submit subprocess / ログマスカ |
| `python/engine/scenario.py` | 戦略 `.py` の `SCENARIO` 抽出・書き戻し |
| `python/engine/run_buffer.py` | replay 結果の RunBuffer 書き出し |
| `examples/wandb/submit_run.py` / `check_auth.py` / `pii_scrub.py` | W&B 送信 subprocess |
| `Cargo.toml` | `muda = "0.15"` 依存（Win/macOS 限定） |
