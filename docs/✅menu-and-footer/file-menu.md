# File メニュー / Save

Open / Save / Save As / Quit の動作・`CURRENT_PATH` 管理・dirty 判定・
replay モードの SCENARIO 経路をまとめる。

---

## モード別動作

| モード | `開く` | `上書き保存` | `名前を付けて保存` |
|--------|--------|-------------|-----------------|
| Live | `.json` 選択 → `saved-state.json` 上書き → `restart()` | `CURRENT_PATH` あり: 直書き / なし: SaveAs フォールバック | 任意 `.json` へ書き出し。`CURRENT_PATH` 更新 |
| Replay | `.py` 選択 → `SCENARIO` 抽出 → `ReplayBarState` prefill | 戦略 `.py` の `SCENARIO` 書き戻し | 戦略 `.py` の `SCENARIO` 書き戻し（別パス可） |

`終了`: dirty チェックを通って終了。macOS Cmd+Q もキーボード subscription 経由で
`Action::Quit` → `ExitRequested` に流れる（`PredefinedMenuItem::quit` は使わないため dirty 確認が確実に走る）。

---

## CURRENT_PATH

`static CURRENT_PATH: Mutex<Option<PathBuf>>`（`src/main.rs`）。

セットタイミング:

- `開く` 成功時
- `名前を付けて保存` 成功時
- 起動時 `--saved-state <PATH>` 指定時

### 保存先の決定ロジック

| 操作 | `CURRENT_PATH = Some(p)` | `CURRENT_PATH = None` |
|------|--------------------------|------------------------|
| 明示 Save / Save As | `p` と `saved-state.json` の**両方に書く** | `saved-state.json` のみ |
| 自動保存 hook | `saved-state.json` のみ | `saved-state.json` のみ |

明示 Save が両方書くことで「Save 後にクラッシュしても任意パスだけが新しく
saved-state は古い」というスキューを排除する。

不変条件:

- 全 `lock()` 箇所で `Err(poisoned) => poisoned.into_inner()` パターンを使い panic 連鎖を防ぐ。
- `--saved-state` の非 UTF-8 パスは `log::error!` を出力し `SavedState::default()` を返す（`CURRENT_PATH` はセットしない）。
- `pending_save_path` のような共有スロットは持たない。Message にパスを直接埋め込む（並行 Save 時の保存先すり替わりを構造的に防ぐ）。

---

## dirty 判定

```text
dirty = match last_saved_bytes {
    None    => false,           // 初期状態は clean
    Some(b) => build_state_json() != b,
}
```

`build_state_json()` は `BTreeMap` ベースの**決定論的シリアライズ**。
`HashMap` / `FxHashMap` への退行は偽陽性を生むため禁止。
`AudioStream::streams` も `BTreeMap`（`SerTicker` / `Exchange` / `Ticker` に `Ord` 実装）。

`last_saved_bytes` の更新は明示 Save / 自動保存 hook の**両方で同じパスを通す**
（自動保存後の Quit で偽陽性 dialog が出ないように）。

---

## confirm dialog 発火経路

Open / Quit / SwitchMode の 3 経路で dirty かつ live モード時に `confirm_dialog_overlay` を表示。

| 選択 | Action |
|------|--------|
| 保存して続行 | `SaveAndOpenFile` / `SaveAndExit` / `SaveAndSwitchMode` |
| 破棄して続行 | `DiscardAndOpenFile` / `DiscardAndExit` / `DiscardAndSwitchMode` |
| キャンセル | `GoBack`（pending 状態を**一括クリア**） |

`Action::Save` / `Action::SaveAs` / `ExitRequested` / `NativeOpenFilePendingCheck` ハンドラ冒頭は
`confirm_dialog.is_none()` ガードを通す。confirm 表示中の Ctrl+S が rfd ダイアログを多重起動しないようにする。

---

## Save As の上書き確認

rfd `save_file()` の OS 側上書き確認に加え、アプリ層でも confirm ダイアログを出す:

- 既存ファイルが存在するときのみ confirm を表示
- UI は dirty 用 `confirm_dialog_overlay` を流用
- パスは Message 自身が運ぶ（`pending_save_path` のような共有スロットを使わない）

---

## 保存エラー分類

| エラー種別 | UI 挙動 | ログレベル |
|-----------|--------|-----------|
| `Cancelled` | 中止のみ。ダイアログなし | 出力なし |
| `IoError(kind)` | エラーダイアログ + 中止 | WARN |
| `PathGuardViolation { reason }` | エラーダイアログ + 中止 | ERROR（`BUG:` プレフィックス付き） |

`save_state_to_disk` は `log::error!` を直接呼ばず `log_save_error(...)` を通す。

---

## replay モードの SCENARIO 経路

`SCENARIO` は戦略 `.py` に埋め込まれた再現条件定数:

```python
SCENARIO: Scenario = {
    "schema_version": 1,
    "instrument": "1301.TSE",
    "start": "2025-01-06",
    "end": "2025-03-31",
    "granularity": "1m",
    "initial_cash": 1_000_000,
}
```

### 抽出（Open）

`python/engine/scenario.py::extract()` が `ast.parse + ast.literal_eval` で `SCENARIO` 定数のみ安全抽出。
任意コード実行は Run 押下時の `importlib.util.spec_from_file_location` に限定される。

抽出結果は `EngineEvent::StrategyScenarioLoaded` として GUI に届き、
`ReplayBarState` に prefill される。SCENARIO 不在の `.py` は `strategy_file` だけセットしてフィールドを空のまま残す。

### 書き戻し（Save / Save As）

`libcst` で `SCENARIO = {...}` の代入文ノードのみ置換。戦略本体・コメント・docstring・import は一切触らない。

- `tempfile + os.replace()` の atomic write
- 元ファイルを `.bak.<UTC秒>` 形式で世代付きバックアップ
- 書き戻し後に `ast.parse + extract + validate` で構文・形状を再検証

### path ガード

- `.py` 拡張子必須
- `Save`: `LoadStrategyScenario` で読み込んだ path と一致のみ許容
- `Save As`: 派生 path 許容。ただし server 側で `path == loaded_path` を reject
- 永続状態ディレクトリ（`%APPDATA%\flowsurface\` / `~/.cache/flowsurface/engine/`）への書き込み禁止

### IPC

| Command / Event | 用途 |
|-----------------|------|
| `Command::LoadStrategyScenario { path }` | `.py` から `SCENARIO` を抽出 |
| `Command::SaveStrategyScenario { path, scenario, save_as }` | `SCENARIO` を書き戻し |
| `Event::StrategyScenarioLoaded { path, scenario }` | 抽出成功 → GUI が prefill |
| `Event::StrategyScenarioLoadFailed { path, reason }` | 抽出失敗 → toast 表示 |
| `Event::StrategyScenarioSaved { path }` | 書き戻し成功 |

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/main.rs` | `NativeMenu*` ハンドラ群 / `build_state_json` / `is_dirty` / `last_saved_bytes` / `CURRENT_PATH` |
| `src/native_menu.rs` | `widget_keyboard_subscription()`（全 OS、accelerator 経路） |
| `src/modal/replay_form.rs` | `prefill_from_scenario` / `set_strategy_file_only` |
| `python/engine/scenario.py` | SCENARIO 抽出・検証・atomic write・path guard |
| `python/engine/server.py` | `LoadStrategyScenario` / `SaveStrategyScenario` IPC ハンドラ |
| `engine-client/src/dto.rs` | 対応 Command / Event バリアント |

---

## テスト

| テスト | 対象 |
|--------|------|
| `tests/accelerator_bind.rs` | `physical_key` 使用 / `logo()` macOS gate |
| `tests/current_path_persists_across_restart.rs` | `CURRENT_PATH` 保持 |
| `tests/dirty_detection.rs` | `build_state_json` 決定論的シリアライズ |
| `tests/save_error_classification.rs` | `Cancelled` / `IoError` / `PathGuardViolation` 分類 |
| `tests/save_as_overwrite_confirm.rs` | 上書き確認ダイアログ |
| `tests/menu_actions_cross_platform.rs` | `actions_for_mode` が全モードで同一集合を返すこと |
| `python/tests/test_scenario_*.py` | SCENARIO 抽出・書き戻し・path guard |
| `engine-client/tests/scenario_roundtrip.rs` | SCENARIO IPC serde roundtrip |

---

## 既知の制限

- **物理キー matching の盲点**: `Physical::Unidentified` を返す特殊配列ではマッチしない。
- **`Cancelled` の無音中止**: rfd の Cancel はユーザー意図のキャンセルとして記録しない。CI では rfd モックが必要。
