# File メニュー / Save 実装仕様

OS ネイティブメニューバー（[native-menu-bar-impl.md](./native-menu-bar-impl.md)）の File
メニューに、一般的なデスクトップアプリ同等の **Open / Save / Save As** 体系を実装した
記録。エンジニアがコードを読む前に押さえておくべき不変条件と境界をまとめる。

---

## メニュー構成

| モード | 項目 | アクセラレータ | 役割 |
|--------|------|----------------|------|
| live | `開く…（Open）` | Ctrl+O | 任意 `.json` を `saved-state.json` に上書きして `restart()` |
| live | `上書き保存（Save）` | Ctrl+S | `CURRENT_PATH` ありなら直書き、無ければ Save As にフォールバック |
| live | `名前を付けて保存…（Save As）` | Ctrl+Shift+S | 任意パスへ書き出し。`CURRENT_PATH` を更新 |
| replay | `開く…（Open）` | Ctrl+O | 戦略 `.py` を選択 → `SCENARIO` を抽出 → `ReplayFormModal` を prefill |
| replay | `名前を付けて保存…（Save As）` | Ctrl+Shift+S | 戦略 `.py` の `SCENARIO` のみ書き戻し |
| 共通 | `終了` | Ctrl+Q（macOS は Cmd+Q） | dirty チェックを通って終了 |

> 三点リーダはすべて U+2026（`…`）。ASCII `...` は不可。

---

## アクセラレータ経路

muda 廃止後、accelerator は **全 OS で `iced::keyboard::listen()` 経由の単一経路**：

| プラットフォーム | 経路 | 主修飾キー |
|------------------|------|----------|
| Windows | `widget_keyboard_subscription` | Ctrl |
| macOS | 同上 | Ctrl または Cmd（logo） |
| Linux | 同上 | Ctrl |

実装: `src/native_menu.rs::widget_keyboard_subscription()`。`iced::keyboard::listen()`
で全イベントを購読し、`physical_key`（`Code::KeyO` / `KeyS` / `KeyQ` / `KeyM`）で
レイアウト非依存にマッチする。

不変条件:

- subscription は全 OS で 1 本だけ登録される（cfg gate なし、二重発火なし）。
- `widget_keyboard_subscription(app_mode)` は `Subscription::with(is_live)` で
  `is_live` を非キャプチャ渡しし、replay モード時は live 専用ショートカット
  （Open / Save / Save As）を抑制する。
- macOS のみ `modifiers.logo()`（Cmd キー）を受理。Win/Linux で受理すると
  Super/Win キーが WM ショートカット（Win+Q 等）と衝突するため。
- `Action::Quit` は `iced::window::close()` を直接呼ばず、
  `window::collect_window_specs(.., Message::ExitRequested)` を通して dirty チェックを
  必ず通過させる（macOS の Cmd+Q もこの経路を通るため、`PredefinedMenuItem::quit`
  時代と異なり dirty 確認が確実に走る）。

リグレッションガード: `tests/accelerator_bind.rs` / `tests/menu_actions_cross_platform.rs`

---

## `CURRENT_PATH`（現在開いているドキュメント）

「現在開いているファイル」の状態を `Flowsurface.current_path` として持ち、`restart()` を
貫通させるため `static CURRENT_PATH: std::sync::Mutex<Option<PathBuf>>` を `src/main.rs`
に併設する（`APP_MODE` と同形式）。

セットされるタイミング:

- `開く…（Open）` 成功時
- `名前を付けて保存…（Save As）` 成功時
- 起動時 `--saved-state <PATH>` 指定時

不変条件:

- 全 `lock()` 箇所で `Err(poisoned) => poisoned.into_inner()` パターンを使い、panic
  連鎖でメニューが死なないようにする
- `--saved-state` の非 UTF-8 パスが渡された場合は `log::error!` を出力し
  `SavedState::default()` を返す（`CURRENT_PATH` はセットしない）
- `pending_save_path` のような共有スロットは持たない。`NativeSaveAsWithSpecs { path,
  windows }` / `ConfirmSaveAsOverwrite { path }` のように Message にパスを直接埋め込む
  （並行 Save 時の保存先すり替わりを構造的に防ぐ）

リグレッションガード: `tests/current_path_persists_across_restart.rs`

### 保存先の決定ロジック

| 操作 | `CURRENT_PATH = Some(p)` | `CURRENT_PATH = None` |
|------|--------------------------|------------------------|
| 明示 `Save` / `Save As` | `p` と `saved-state.json` の **両方に書く** | `saved-state.json` のみ |
| 自動保存 hook | `saved-state.json` のみ（`p` は触らない） | `saved-state.json` のみ |

明示 Save が両方書くことで「Save 後にクラッシュしても任意パスだけが新しく
saved-state は古い」というスキューを排除する。自動保存は `CURRENT_PATH` を参照しない
ため、dirty 判定の基準は常に `build_state_json()` の単一出力で一意に確定する。

---

## dirty 判定と未保存変更ダイアログ

`Flowsurface.last_saved_bytes: Option<Vec<u8>>` を保持し、`build_state_json()` の出力と
等価判定して dirty を確定する。

```text
dirty = match last_saved_bytes {
    None    => false,                                  // 初期状態は clean
    Some(b) => build_state_json() != b,
}
```

不変条件:

- `build_state_json()` は `BTreeMap` ベースの **決定論的シリアライズ**。HashMap /
  FxHashMap への退行は dirty 偽陽性を生むため禁止
- `AudioStream::streams` も `BTreeMap`（`SerTicker` / `Exchange` / `Ticker` に `Ord` 実装）
- `last_saved_bytes` 更新は明示 Save / 自動保存 hook の **両方で同じパスを通す**
  （自動保存後の Quit で偽陽性 dialog が出ないように）

### confirm dialog の発火経路

Open / Quit / SwitchMode の 3 経路で dirty かつ live モード時に
`confirm_dialog_overlay` を表示する。3 択：

- 保存して続行 → `SaveAndOpenFile` / `SaveAndExit` / `SaveAndSwitchMode`
- 破棄して続行 → `DiscardAndOpenFile` / `DiscardAndExit` / `DiscardAndSwitchMode`
- キャンセル → `GoBack`

`GoBack` は `pending_open_file` / `pending_exit_windows` / `pending_mode_switch` /
`_mode_switch_guard` を **必ず一括クリア**する（orphan 化防止）。

`Action::Save` / `Action::SaveAs` / `ExitRequested` / `NativeOpenFilePendingCheck` の
各ハンドラ冒頭は `confirm_dialog.is_none()` ガードを通す。confirm 表示中の Ctrl+S /
Ctrl+Shift+S が rfd ダイアログを多重起動しないようにする。

リグレッションガード: `tests/dirty_detection.rs`

### 保存エラー分類

| エラー種別 | UI 挙動 | ログレベル | ログ文字列 |
|-----------|--------|-----------|-----------|
| `Cancelled` | 中止のみ。ダイアログ無し | INFO 相当（出さない） | — |
| `IoError(kind)` | エラーダイアログ + 中止 | **WARN** | 通常メッセージ |
| `PathGuardViolation { reason }` | エラーダイアログ + 中止 | **ERROR** | `BUG: path guard violation path=<p> reason=<r>` |

ログ出力先: debug ビルド → ターミナル stdout / release →
`~/AppData/Roaming/flowsurface/flowsurface-current.log`

`save_state_to_disk` は `log::error!` を直接呼ばず `log_save_error(&SaveError::IoError(..))`
を通す。リグレッションガード: `tests/save_error_classification.rs`
（`save_state_to_disk_does_not_use_log_error` で固定）。

---

## Save As の上書き確認

rfd `save_file()` の OS 側上書き確認に頼らず、アプリ層でも確認ダイアログを出す：

- 既存ファイルが存在するときのみ confirm を表示
- ダイアログ UI は dirty 用 `confirm_dialog_overlay` を流用
- パスは Message 自身が運ぶ（`pending_save_path` のような共有スロットを使わない）

リグレッションガード: `tests/save_as_overwrite_confirm.rs`

---

## replay モードの `.py` 経路

replay モードの Open / Save As は戦略 `.py` の `SCENARIO` 定数を対象にする。
`SCENARIO` は再現条件（instrument / start / end / granularity / initial_cash）を
戦略ファイル自身に埋め込むモジュール定数：

```python
from typing import TypedDict

class Scenario(TypedDict):
    schema_version: int
    instrument: str
    start: str
    end: str
    granularity: str
    initial_cash: int

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

`python/engine/scenario.py::extract()` が `ast.parse + ast.literal_eval` で `SCENARIO`
**定数のみ**を安全抽出する。任意コード実行は Run 押下時の `importlib.util.spec_from_file_location`
に限定される。

抽出された値は `EngineEvent::StrategyScenarioLoaded` として GUI に届き、
`ReplayFormModal::prefill_from_scenario()` でフォームを埋める。`granularity` は
`Granularity` enum へマッピング、未知値は既存値を保持する。SCENARIO 不在の `.py` は
`strategy_file` だけセットしてフィールドを空のまま残す（仕様）。

### 書き戻し（Save / Save As）

`libcst` で `SCENARIO = {...}` の代入文ノードのみを置換する。戦略本体・コメント・
docstring・import は一切触らない。書き込みは：

- `tempfile + os.replace()` の **atomic write**
- 元ファイルを `.bak.<UTC秒>` 形式で世代付きバックアップ
- 書き戻し後に `ast.parse + extract + validate` で構文・形状を再検証

### path ガード

`SaveStrategyScenario` ハンドラで以下を強制する：

- `.py` 拡張子必須
- `Save`: `LoadStrategyScenario` で読み込んだ path と一致のみ許容
- `Save As`: 派生 path 許容。ただし server-side で `path == loaded_path` を reject
- 永続状態ディレクトリ（`%APPDATA%\flowsurface\` / `~/.cache/flowsurface/engine/`）への
  書き込みは禁止（`saved-state.json` / `engine-session.json` / `tachibana_orders.jsonl`
  を誤って `.py` 書き戻しで踏み潰さない）

### IPC

| Command / Event | 用途 |
|-----------------|------|
| `Command::LoadStrategyScenario { path }` | `.py` から `SCENARIO` を抽出 |
| `Command::SaveStrategyScenario { path, scenario, save_as }` | `SCENARIO` を書き戻し |
| `Event::StrategyScenarioLoaded { path, scenario }` | 抽出成功 → GUI が prefill |
| `Event::StrategyScenarioLoadFailed { path, reason }` | 抽出失敗 → GUI が toast 表示 |
| `Event::StrategyScenarioSaved { path }` | 書き戻し成功 |

`SCHEMA_MINOR=10` 以降。`engine-client/src/dto.rs` に対応 Command / Event バリアント。

リグレッションガード: `python/tests/test_scenario_*.py` /
`engine-client/tests/scenario_roundtrip.rs` / `src/modal/replay_form.rs` 内
`prefill_from_scenario_*` テスト

---

## 永続状態ファイルとの関係

プロジェクトの永続状態ファイルと本機能の対応：

| ファイル | 本機能での扱い |
|---------|----------------|
| `saved-state.json` | 自動保存先。明示 Save / Save As でも常に書き出す（`CURRENT_PATH` ありなら任意パスにも書く） |
| `engine-session.json` | モード切替で engine 再起動時に Drop で削除 → bootstrap で再生成 |
| `tachibana_orders.jsonl` | モード切替で **書き換えない**。SwitchMode は read-only 参照のみ許容 |

`SCENARIO` 書き戻し path ガードがこれらのファイルを物理的に保護する（`.py` 拡張子必須
+ 永続状態ディレクトリ書き込み禁止）。

---

## 主要ソース

| ファイル | 役割 |
|---------|------|
| `src/native_menu.rs` | `Action` enum / `widget_keyboard_subscription`（全 OS、accelerator 経路） |
| `src/widget_menu_bar.rs` | iced widget メニューバー（全 OS） |
| `src/main.rs` | `NativeMenu*` ハンドラ群・`build_state_json` / `is_dirty` / `last_saved_bytes` / `CURRENT_PATH` |
| `src/cli.rs` | `--saved-state <PATH>` 引数 |
| `src/modal/replay_form.rs` | `prefill_from_scenario` / `set_strategy_file_only` |
| `python/engine/scenario.py` | `SCENARIO` 抽出・検証・atomic write・path guard |
| `python/engine/server.py` | `LoadStrategyScenario` / `SaveStrategyScenario` IPC ハンドラ |
| `engine-client/src/dto.rs` | 対応 Command / Event バリアント |

---

## 検証コマンド

```bash
cargo fmt --check
cargo clippy --workspace -- -D warnings
cargo test --workspace
uv run pytest python/tests/test_scenario_*.py -v
```

メニュー / Save 経路を網羅する主要テスト:

```bash
cargo test --test accelerator_bind
cargo test --test current_path_persists_across_restart
cargo test --test dirty_detection
cargo test --test save_error_classification
cargo test --test save_as_overwrite_confirm
cargo test --test menu_actions_cross_platform
```

---

## 既知の制限

- **macOS Cmd+Q の dirty チェック**: muda 廃止により `PredefinedMenuItem::quit` は
  使わず、Cmd+Q もキーボード subscription 経由で `Action::Quit` → `ExitRequested` に
  流れる。よって dirty チェックは確実に通る（旧 muda 時代の OS 直接処理による迂回問題は解消）。
- **物理キー matching の盲点**: `physical_key` が `Physical::Unidentified` を返す
  特殊配列（一部ノート PC キー等）ではマッチしない。OS / 機種別の実機検証が望ましい。
- **`Cancelled` の無音中止**: rfd の Cancel パスはユーザー意図のキャンセルとして INFO
  相当扱いで記録しない。CI では rfd モックが必要
