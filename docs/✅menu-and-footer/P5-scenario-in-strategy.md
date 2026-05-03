# SCENARIO 辞書による replay 再現条件の一元化

**作成日**: 2026-05-04
**作成者**: Claude Opus 4.7（botterYosuke）
**ステータス**: 未着手・実装計画
**起点課題**: [./fix-save-menu.md](./fix-save-menu.md) F6（凡例: F* = fix-save-menu.md 上の本系列タスク。P*/既存 Phase 8.x は別系列。なお F5 = Save As 上書き確認は P4 系列であり P5 とは別タスク）
**ロードマップ**: [./fix-save-menu.md](./fix-save-menu.md) §実装ロードマップ F6
**前提**: F3（`current_path` + 上書き保存）が完了していること

---

## 用語表

| 用語 | 定義 |
|------|------|
| Strategy class（戦略クラス） | ユーザーが書く `class XxxStrategy(Strategy):` の Python クラス本体。発注ロジックを持つ |
| SCENARIO 辞書 | 戦略 `.py` のトップレベルに宣言する **再現条件メタデータ**（`instrument` / `start` / `end` / `granularity` / `initial_cash` / `schema_version`）。実行コードではなく純粋なリテラル dict |
| scenario-bearing file | SCENARIO を含む `.py`（= `strategy_file` のうち SCENARIO を持つもの） |
| strategy_file | `--strategy` などで指定する `.py` ファイルパス（SCENARIO の有無は問わない） |
| 再現条件 | SCENARIO に格納される 5 項目のメタデータ。replay を 1 ファイルだけで再現するための最小集合 |

本書では従来「シナリオ」と表記されていた箇所を「SCENARIO」に統一する。

---

## やること <a id="p5"></a>

replay の再現条件（戦略 + 銘柄 + 期間 + 粒度 + 初期資金）を、戦略 `.py` 内のモジュール定数
`SCENARIO`（SCENARIO 辞書）として宣言する。replay は「この `.py` 1 個」だけで再現できるようにする。

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

class MyStrategy(Strategy):
    ...
```

補助 `.replay.json` は **作らない**（`.py` と `.json` の不整合を原理的に消す）。

---

## サブタスク分割

| ID | 内容 | 規模 | 並列性 |
|----|------|------|--------|
| **F6a** | 読み込み経路（GUI）：`開く…（Open）` で `.py` 選択 → Python 側で `ast.parse + ast.literal_eval` により SCENARIO を **read-only 抽出**（importlib は使わない） → IPC で返す → `ReplayFormModal` prefill | M | F6c と並列可 |
| **F6b** | 読み込み経路（CLI）：`engine.replay_session run` の `--strategy` のみ指定時に `SCENARIO` をフォールバック値として使用。CLI 引数優先のオーバーライド | S | F6c と並列可 |
| **F6c** | 書き戻し経路：`libcst` 依存追加 + `SCENARIO` ブロック差替ユーティリティ + `tempfile + os.replace()` atomic write + `.bak.<UTC秒>` 世代付きバックアップ + 再ロード検証 + rollback。`上書き保存（Save）` / `名前を付けて保存…（Save As）` に統合 | L | F6a / F6b と並列可（先行着手しても無駄にならない） |

---

## 確定済み設計判断 <a id="design"></a>

[./fix-save-menu.md](./fix-save-menu.md) F6 §論点 に対する確定方針：

| 論点 | 確定 |
|------|------|
| 定数名 | **`SCENARIO` 固定**（小文字 `scenario` などは認めない。書き戻しの曖昧性を回避） |
| `SCENARIO` が無い `.py` | **許容**。読み込みは寛容（prefill されないだけ）、書き戻し時に **新規 `SCENARIO` ブロックを `.py` 冒頭の import 直後に挿入** して永続化 |
| CLI 引数と `SCENARIO` の優先順位 | **CLI 引数優先**。CLI 経由の値は `SCENARIO` には書き戻さない（ad-hoc 実行を妨げない） |
| 書き戻し方式 | **`libcst` Concrete Syntax Tree 置換**（`ast` + 自前 unparse はコメント消失リスク、正規表現は脆い） |
| 抽出方式（read 経路） | **`ast.parse` + `ast.literal_eval`**（任意コード実行を起こさない read-only 抽出。importlib は Run 押下時のみ） |
| 依存追加 | `libcst` を `pyproject.toml` に追加（純 Python・MIT・軽量） |

---

## F6a：GUI 読み込み経路 <a id="f6a"></a>

### 動線

1. replay モード起動中に `開く…（Open）` で `.py` を選択
2. Rust → Python に `Command::LoadStrategyScenario { path }` を送る（新 IPC コマンド）
3. Python 側で **`ast.parse(source)` してトップレベル `Assign` を走査**、`targets[0].id == "SCENARIO"` の `value` を **`ast.literal_eval` で評価**（任意コード実行を起こさない read-only 抽出）。
   `ast.literal_eval` は dict literal **のみ** を受理する。**dict unpacking（`{**other}`）・dict comprehension（`{k: v for ...}`）・関数呼び出しを含む dict は silent fail せず明示エラー** とし、エラー文言は次に統一する：
   `「dict literal 以外（unpacking {**...} / comprehension / 関数呼び出しを含む dict）は SCENARIO として読めません。リテラルの dict だけを使ってください」`
4. importlib による実行はここでは **行わない**（Run 押下時にのみ `engine.replay_session.run()` 経由で実行）
5. 結果を `Event::StrategyScenarioLoaded { scenario: Option<Scenario> }` で返す
6. Rust 側で `ReplayFormModal` のフィールドを prefill（None なら空のまま）
7. `Flowsurface.current_path = Some(path)`（F3 と統合）

### IPC スキーマ拡張

- `python/engine/schemas.py` に `Scenario` TypedDict 追加
- `engine-client/src/dto.rs` に対応する Rust 側 struct
- `SCHEMA_MINOR` をインクリメント（`SCHEMA_MAJOR` は据置）
- **DoD（SCHEMA_MAJOR/MINOR 区別 assert）**: `/ipc-schema-check` を実行し major 一致・minor 同期を確認。
  `engine-client/tests/scenario_roundtrip.rs` に **`assert_eq!(SCHEMA_MAJOR, 1)` と `assert!(SCHEMA_MINOR >= N)` の両方を明示**
  （N = 本変更で確定する値）してリグレッションガードを追加する。
  さらに **「minor 不一致でも接続は成立する」** ことを `python/tests/test_schema_minor_compat.py` で保護する
  （SCHEMA_MAJOR のみ不一致時にハンドシェイク失敗、SCHEMA_MINOR 差異は WARN ログで通過）。

### エラーハンドリング

- `ast.parse` 構文エラー：`Event::StrategyScenarioLoadFailed { reason }` を返し、ダイアログで原因表示。`current_path` はセットしない
- `ast.literal_eval` で評価不能（リテラル以外を含む dict 値、dict unpacking、comprehension、関数呼び出し）：
  同上 + 該当ノードの行番号 + 統一エラー文言「dict literal 以外（unpacking `{**...}` / comprehension / 関数呼び出しを含む dict）は SCENARIO として読めません。リテラルの dict だけを使ってください」
- `SCENARIO` の型不一致（必須キー欠落・型違反）：同上 + 不一致箇所を含めたメッセージ
- read 経路では Python コード本体を実行しないため、`__pycache__` 抑制は不要（`sys.dont_write_bytecode` には触れない）

### DoD（F6a）

- テスト: `python/tests/test_scenario_load.py`
  - `test_rejects_dict_unpacking`: `SCENARIO = {**other_dict, "instrument": "..."}` を読んで上記統一エラー文言で reject
  - `test_rejects_dict_comprehension`: `SCENARIO = {k: v for k, v in [...]}` を読んで上記統一エラー文言で reject
  - **`test_read_only_no_side_effect`（read-only regression guard）**:
    モジュールトップレベルに `open("/tmp/SIDE_EFFECT", "w").write("x")` のような副作用コードを含む `.py` を Load しても
    `/tmp/SIDE_EFFECT` が**作られない**ことを assert（`ast.parse + ast.literal_eval` は import を発火しないため副作用は起きない）。
    併せて `sys.modules` 増加なし・`__pycache__` 生成なしも assert
- 期待ログ: 成功時 `INFO scenario.load path=... keys=6`、失敗時 `WARN scenario.load failed reason=...`
- 観測コマンド: `uv run pytest python/tests/test_scenario_load.py -v`

---

## F6b：CLI 読み込み経路 <a id="f6b"></a>

### 動線

```bash
# SCENARIO を使う（引数省略）
uv run python -m engine.replay_session run --strategy docs/example/buy_and_hold.py

# CLI 引数で上書き
uv run python -m engine.replay_session run \
    --strategy docs/example/buy_and_hold.py \
    --instrument 7203.TSE \
    --start 2025-04-01 --end 2025-04-30
```

### 実装ポイント

- `replay_session.py` の `run` サブコマンドで `--instrument` / `--start` / `--end` /
  `--granularity` / `--initial-cash` を **optional** に変更
- 引数欠落時に戦略 `.py` から `SCENARIO` を読む（F6a と同じ `engine.scenario.extract()` を使用、importlib なし）
- 引数あり + `SCENARIO` あり：CLI 引数優先（警告ログ「SCENARIO の X を CLI 引数で上書きしました」）
- 引数欠落 + `SCENARIO` 欠落：エラー終了（明示的なメッセージ）

### F6a との共通化

`SCENARIO` ロード処理は `engine.scenario` モジュール（新規）にユーティリティとして切り出し、
GUI / CLI 両経路から呼ぶ。

### DoD（F6b）

- テスト: `python/tests/test_scenario_cli.py`
- 期待ログ: `INFO scenario.cli source=file|cli|merged`
- 観測コマンド: `uv run pytest python/tests/test_scenario_cli.py -v`

---

## F6c：書き戻し経路 <a id="f6c"></a>

### `libcst` による置換アルゴリズム

1. `cst.parse_module(source)` で CST を取得
2. トップレベルの `Assign` ノードを走査し、`targets[0].value == "SCENARIO"` を探す
3. 見つかったら：そのノードの `value`（dict literal）を新 SCENARIO で差し替え
4. 見つからなかったら：最後の `Import` / `ImportFrom` の直後に `SCENARIO = {...}` を挿入
5. `module.code` で文字列化して書き出し

### 安全装置（atomic write + 世代付きバックアップ）

- 書き戻しは厳密に次の順序で実行する（中断・電源断で半端ファイルが残らないことを保証）：

  1. **tempfile 取得**: `tempfile.NamedTemporaryFile(dir=同一ディレクトリ, delete=False)` を生成
  2. **source backup**: 既存 target を `<path>.py.bak.<UTC秒>` にコピー
  3. **write**: 新しい SCENARIO を含む module コードを tempfile に書き込み
  4. **fsync**: `os.fsync(tmp_fd)` で物理書き込みを保証
  5. **`os.replace(tmp, target)`** で atomic に置換

- 各ステップ失敗時の cleanup 責任は呼び出し元（`engine.scenario.write_back`）が必ず負う：
  - **(1) tempfile 取得失敗** → cleanup なし。`error="tempfile_failed"` を返す
  - **(2) backup 失敗** → 取得済み tempfile を削除。`error="permission_denied"` または `"parent_missing"` を返す
  - **(3) write 失敗** → tempfile を削除し、作成済み `.bak.<UTC秒>` を削除。`error="disk_full"` または `"permission_denied"` を返す
  - **(4) fsync 失敗** → tempfile を削除し、作成済み `.bak.<UTC秒>` を削除。`error="disk_full"` を返す
  - **(5) os.replace 失敗** → tempfile を削除（`.bak.<UTC秒>` は残しておき rollback 可能にする）。`error="rename_failed"` を返す

- バックアップは `<path>.py.bak.<UTC秒>` 形式の世代付き（例: `buy_and_hold.py.bak.1714809600`）。
  **既存 `.bak.<UTC秒>` の上書きは禁止**（同じ秒に二回保存した場合は連番 suffix `-1`, `-2` を付与）
- ディスクフル等で write が失敗した場合、上記 cleanup 規則に従い tempfile / `.bak` を削除のうえエラーダイアログで通知
- **保存エラーコード列挙**（`Event::StrategyScenarioSaved { ok: false, error: ... }` の `error` 値）：
  `"permission_denied"` / `"parent_missing"` / `"disk_full"` / `"path_guard_violation"` / `"rename_failed"` / `"tempfile_failed"` のみ
- 書き戻し後に `importlib` で再ロード検証
  - import エラー → 直近の `.bak.<UTC秒>` から rollback → エラーダイアログ
  - **rollback 後 byte-diff assert**（テスト DoD）：書き戻し前のオリジナルと rollback 後のファイルが byte 単位で完全一致することを確認
- `SCENARIO` 以外のコード・コメント・空白・docstring は触れないこと（`libcst` の不変条件）

### path ガード（`Command::SaveStrategyScenario` 不変条件）

Python 側 `engine.scenario.write_back(path, scenario)` は次の 3 条件すべてを満たさない限り `ValueError` で拒否する：

1. `path` の拡張子が **`.py` のみ**（`.json` / `.bak` / 拡張子なしは拒否）
2. `path` が **直前の `Command::LoadStrategyScenario` で渡された path と一致** する（FCFS 不変条件）。
   multi-client engine（max 4）で他クライアントが間に別の Load を挟んだ場合は拒否。
   **Load 履歴が None の場合の分岐**: `current_path == None` かつ Save As 経路フラグ
   （`Command::SaveStrategyScenario { save_as: true, .. }`）が立っているときに**限り**許可する。
   それ以外（current_path に値があるのに Load 履歴が無い等）は拒否
3. `path` が **永続状態ファイルのディレクトリ配下に書き込もうとしていない**こと。
   具体的には `engine-session.json` / `saved-state.json` / `tachibana_orders.jsonl` の
   親ディレクトリ（`%APPDATA%\flowsurface\` および `~/.cache/flowsurface/engine/`）への
   書き込みを禁止（パストラバーサル / 任意ファイル上書き対策）

これらは `python/tests/test_scenario_path_guard.py` で網羅し、multi-client での FCFS 違反テストも含める。

### GUI 統合

- `上書き保存（Save）`：`current_path`（`.py`）の `SCENARIO` を更新
- `名前を付けて保存…（Save As）`：現在の戦略 `.py` をコピーし、コピー先の `SCENARIO` を新値で書き出す（戦略コードはそのまま）
- live モードでは従来通り `saved-state.json` を保存（`current_path` の拡張子で分岐）

### Save As のモード依存モデル

| モード | `名前を付けて保存…（Save As）` の対象 | ファイルフィルタ |
|--------|--------------------------------------|----------------|
| replay | 現在の戦略 `.py` をコピーし、コピー先の **SCENARIO 辞書を atomic 書き戻し**（戦略コード本体は変更なし） | `*.py` |
| live   | `saved-state.json` 互換の JSON を任意パスへ保存 | `*.json` |

モード別の挙動詳細は [./fix-save-menu.md](./fix-save-menu.md) のモード別挙動表と整合させる。

### ファイルフィルタ

- replay モード：`開く…（Open）` / `名前を付けて保存…（Save As）` のフィルタは `.py`
- live モード：従来通り `.json`

### Python ↔ Rust 役割分担

- 書き戻しロジックは Python 側（`engine.scenario.write_back(path, scenario)`）に実装
- Rust → Python に `Command::SaveStrategyScenario { path, scenario }` を送る
- 結果を `Event::StrategyScenarioSaved { ok, error }` で受ける

### DoD（F6c）

- テスト: `python/tests/test_scenario_writeback.py` / `python/tests/test_scenario_path_guard.py`
- 期待ログ: `INFO scenario.writeback path=... bak=... bytes=...` / 失敗時 `ERROR scenario.writeback rollback bak=...`
- 観測コマンド: `uv run pytest python/tests/test_scenario_writeback.py python/tests/test_scenario_path_guard.py -v`

---

## テスト

### Python

- `python/tests/test_scenario_load.py`
  - 正常な `SCENARIO` を `ast.literal_eval` で読める
  - `SCENARIO` 不在 `.py` で `None` が返る
  - 構文エラーは適切にハンドリング
  - 必須キー欠落は型エラーとして報告
  - **read 経路で `import` 副作用が発生しない**（`sys.modules` 増加なし、`__pycache__` 生成なし）
- `python/tests/test_scenario_writeback.py`
  - 既存 `SCENARIO` の差替で他のコード・コメント・空白が完全保持される（diff 比較）
  - `SCENARIO` 不在 `.py` への新規挿入が import 直後に入る
  - 書き戻し後 `importlib` 再ロードで構文エラーが出ない
  - **rollback fixture**: `SCENARIO = {"instrument": 1, ...}`（int を入れて TypedDict 検証 fail）を新値として
    渡し、`importlib` 再ロード時に型検証を fail させる経路を発火 → `.bak.<UTC秒>` から rollback
    → **rollback 後にオリジナルと byte-diff が 0** であることを assert
  - atomic 書き込み: 途中で `os.replace` を mock fail させたとき target ファイルが破損しない
  - `.bak.<UTC秒>` 既存上書き禁止（同秒の重複保存で連番 suffix）
- `python/tests/test_scenario_path_guard.py`
  - `.py` 以外の拡張子拒否
  - 直前 `LoadStrategyScenario` 不一致での拒否
  - 永続状態ファイルディレクトリへの書き込み拒否
  - multi-client（4 接続）で別クライアントが Load を挟んだ場合の FCFS 拒否
  - **`test_save_without_prior_load`**: Load 履歴 None の状態で `save_as=true` フラグが立っていれば許可、
    Load 履歴 None かつ `save_as=false`（= 通常の上書き Save）なら `error="path_guard_violation"` で拒否

### Rust

- `engine-client/tests/scenario_roundtrip.rs`
  - `LoadStrategyScenario` → `StrategyScenarioLoaded` のラウンドトリップ
  - `SaveStrategyScenario` → `StrategyScenarioSaved` のラウンドトリップ
  - `assert!(SCHEMA_MINOR >= N)` リグレッションガード（N は本変更で確定）
- `actions_for_mode` テストに `.py` フィルタの replay モード動作を追加

### サンプル戦略の更新

- `docs/example/buy_and_hold.py` に `SCENARIO` 辞書を追加（仕様の生きた例として）
- **影響範囲調査タスク**（F6c のサブ DoD）: 以下のコマンドで影響箇所を grep し、本書または PR description に結果を記載する：

  ```bash
  grep -rn "buy_and_hold" python/tests/ docs/ scripts/ --include="*.py" --include="*.md"
  grep -rn "docs/example" python/tests/ engine-client/tests/ --include="*.py" --include="*.rs"
  ```

  既知の影響先候補: `python/tests/test_replay_session*.py`（fixture が `buy_and_hold.py` を参照していれば SCENARIO 追加でも互換が保たれることを確認）。

---

## マイグレーション

- 既存の `.py` 戦略には影響なし（`SCENARIO` 不在は許容）
- ユーザーが `名前を付けて保存…（Save As）` を一度実行すると `SCENARIO` が自動挿入される
- `docs/example/buy_and_hold.py` は本フェーズで `SCENARIO` 付きに更新

---

## 非スコープ

- -（対象外） `SCENARIO` 以外のメタデータ（タグ・説明文・タイムゾーン等）の追加
- -（対象外） 複数 `SCENARIO` の宣言（A/B 比較は W&B sweep で行う）
- -（対象外） `.replay.json` 補助ファイル（明示的にやらない）
- -（対象外） live モード `.py` 戦略への `SCENARIO` 適用（live は `saved-state.json` 経路のまま）
- -（対象外） `SCENARIO` のスキーマバージョン migration（v1 のみ。将来必要になれば別計画）
- -（対象外 / 運用注記） **`.bak.<UTC秒>` の自動 GC**: 本フェーズでは実装しない。
  `.bak.<UTC秒>` は**手動削除**を運用ルールとする。
  将来、世代爆発が問題になった場合は「起動時に N=20 世代を超えた古い `.bak.<UTC秒>` を削除する」
  optional な GC を別タスクで検討する余地を残す

---

## 未決事項

- **未信頼 `.py` 警告ダイアログ**: `開く…（Open）` 直後の read 経路は `ast.literal_eval` で安全だが、
  Run 押下時の `importlib` 実行は任意コード実行であり、CLAUDE.md「ユーザー戦略は自己責任方針」と整合する。
  Run 前に「このファイルは任意コードを実行します。信頼できる作者のものですか？」ダイアログを出すかは未決
- **`subprocess` による read 経路の隔離**: 現状 `ast.parse + ast.literal_eval` は process-wide 副作用を持たない
  ため不要だが、将来 SCENARIO に動的要素を許容する場合は SCENARIO 抽出を `subprocess` で隔離する案を検討
- **自前メニューバー（widget menu bar）** での `.py` フィルタ実装: Linux 側（[./P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md)）の
  ファイルダイアログがネイティブと同等のフィルタ UX を提供できるかは未確認

---

## 関連ファイル

| ファイル | 役割 |
|---------|------|
| [../../docs/example/buy_and_hold.py](../../docs/example/buy_and_hold.py) | サンプル戦略（`SCENARIO` 付与対象） |
| [../../python/engine/replay_session.py](../../python/engine/replay_session.py) | F6b CLI 経路 |
| [../../python/engine/schemas.py](../../python/engine/schemas.py) | `Scenario` TypedDict + IPC スキーマ拡張 |
| [../../engine-client/src/dto.rs](../../engine-client/src/dto.rs) | Rust 側 IPC 構造体 |
| [../../engine-client/src/lib.rs](../../engine-client/src/lib.rs) | Rust 側 `SCHEMA_MAJOR` / `SCHEMA_MINOR` 定義（`python/engine/schemas.py` と同期対象） |
| [../../src/screen/dashboard/modal.rs](../../src/screen/dashboard/modal.rs) | `ReplayFormModal` prefill 統合先 |
| [./fix-save-menu.md](./fix-save-menu.md) | F* 系列の親計画書（モード別挙動表・凡例） |
| [./P7-mode-switch-menu.md](./P7-mode-switch-menu.md) | モード切替時の Save As 挙動 |
| [./P8-widget-menu-bar-linux.md](./P8-widget-menu-bar-linux.md) | Linux 自前メニューバー（widget menu bar）でのファイルフィルタ |
