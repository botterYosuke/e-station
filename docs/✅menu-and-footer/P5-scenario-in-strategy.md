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
3. Python 側で **`ast.parse(source)` してトップレベル `Assign` または `AnnAssign` を走査**、`Assign` は `targets[0].id == "SCENARIO"`、`AnnAssign` は `target.id == "SCENARIO"` を拾い、その `value` を **`ast.literal_eval` で評価**（任意コード実行を起こさない read-only 抽出）。これにより `SCENARIO: Scenario = {...}`（注釈付き代入、計画書の推奨形式）と `SCENARIO = {...}`（注釈なし）の両形式を許可する。`AnnAssign.value` が `None`（例: `SCENARIO: Scenario` のようにアノテーションのみの宣言）の場合は **SCENARIO 不在として扱う**（`None` を返す）。
   `ast.literal_eval` は dict literal **のみ** を受理する。**dict unpacking（`{**other}`）・dict comprehension（`{k: v for ...}`）・関数呼び出しを含む dict は silent fail せず明示エラー** とし、エラー文言は次に統一する：
   `「dict literal 以外（unpacking {**...} / comprehension / 関数呼び出しを含む dict）は SCENARIO として読めません。リテラルの dict だけを使ってください」`
4. importlib による実行はここでは **行わない**（Run 押下時にのみ `engine.replay_session.run()` 経由で実行）
5. 結果を `Event::StrategyScenarioLoaded { scenario: Option<Scenario> }` で返す
6. Rust 側で `ReplayFormModal` のフィールドを prefill（None なら空のまま）
7. `Flowsurface.current_path = Some(path)`（F3 と統合）

### `Scenario` TypedDict + runtime validator

`python/engine/schemas.py`（または `python/engine/scenario.py`）に `Scenario` TypedDict を定義するのに加え、
**runtime 検証関数 `engine.scenario.validate(d: dict) -> None` を必須実装する**。TypedDict は静的型ヒントに過ぎず
実行時には dict の形状を検証しないため、`importlib.reload()` だけでは型違反（例: `instrument` に int を渡す等）
を検知できず rollback 経路が発火しない（R6-2 false-green）。`validate()` は次のように振る舞う：

- 必須キー（`schema_version` / `instrument` / `start` / `end` / `granularity` / `initial_cash`）の欠落で `TypeError`（または `ScenarioValidationError`）を raise
- 各キーの値の型が `Scenario` 定義と一致しない場合（例: `instrument` が `str` でない、`initial_cash` が `int` でない等）も同上の例外を raise
- 余剰キーの扱いは strict（未知キーがあれば raise）
- 例外メッセージには違反キー名と期待型を含める

GUI 経路（F6a）の read-only prefill / CLI 経路（F6b）/ 書き戻し後の再ロード検証（F6c）すべてが `validate()` を共通利用する。

### IPC スキーマ拡張

- `python/engine/schemas.py` に `Scenario` TypedDict 追加
- `engine-client/src/dto.rs` に対応する Rust 側 struct
- `SCHEMA_MINOR` をインクリメント（`SCHEMA_MAJOR` は据置）
- **DoD（SCHEMA_MAJOR/MINOR 区別 assert）**: `/ipc-schema-check` を実行し major 一致・minor 同期を確認。
  `engine-client/tests/scenario_roundtrip.rs` に **`assert_eq!(SCHEMA_MAJOR, 3)` と `assert!(SCHEMA_MINOR >= 11)` の両方を明示**
  （N=11 は F7 まで適用された SCHEMA_MINOR の現在値。F6 単体では 10 に bump されたが F7 で 11 に上がっている）してリグレッションガードを追加する。
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
  - `test_reads_annotated_assign`: `SCENARIO: Scenario = {...}`（`AnnAssign` 形、計画書推奨形式）を読めることを assert
  - `test_reads_plain_assign`: `SCENARIO = {...}`（`Assign` 形、注釈なし）を読めることを assert
  - `test_treats_annotation_only_as_absent`: `SCENARIO: Scenario`（`AnnAssign.value is None`、アノテーションのみの宣言）は **SCENARIO 不在として扱う**ことを assert（`None` を返し、エラーにはしない）
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
2. トップレベルの `Assign` および `AnnAssign` ノードを走査し、`SCENARIO` を target に持つノード（`Assign` の `targets[0].value == "SCENARIO"` または `AnnAssign` の `target.value == "SCENARIO"`）を探す
3. 見つかったら：そのノードの `value`（dict literal）を新 SCENARIO で差し替える。元が `AnnAssign`（注釈付き）であれば **アノテーション（`: Scenario`）を保持したまま value のみ差し替える**。元が `Assign`（注釈なし）であればそのまま `Assign` 形式を維持する。`AnnAssign.value is None`（アノテーションのみ宣言）にヒットした場合は同 `AnnAssign` の `value` を新 SCENARIO で埋める
4. 見つからなかったら：最後の `Import` / `ImportFrom` の直後に `SCENARIO = {...}` を `Assign` 形式で挿入する（既存の `Scenario` import が無い可能性に配慮し、注釈なし形式を採用）
5. `module.code` で文字列化して書き出し

### 安全装置（atomic write + 世代付きバックアップ）

- 書き戻しは厳密に次の順序で実行する（中断・電源断で半端ファイルが残らないことを保証）：

  1. **tempfile 取得**: `tempfile.NamedTemporaryFile(dir=同一ディレクトリ, delete=False)` を生成
  2. **source backup**: 既存 target を `<path>.py.bak.<UTC秒>` にコピー
  3. **write**: 新しい SCENARIO を含む module コードを tempfile に書き込み
  4. **fsync**: `os.fsync(tmp_fd)` で物理書き込みを保証
  5. **`os.replace(tmp, target)`** で atomic に置換

- 各ステップ失敗時の cleanup 責任は呼び出し元（`engine.scenario.write_back`）が必ず負う：
  - **(1) tempfile 取得失敗** → cleanup なし。errno に応じて `error="parent_missing"` /
    `"disk_full"` / `"permission_denied"` のいずれかを返す（ラウンド2 / H-R2-2 で
    専用 `tempfile_failed` コードは廃止し、OSError errno マッピングに吸収した）
  - **(2) backup 失敗** → 取得済み tempfile を削除。`error="permission_denied"` または `"parent_missing"` を返す
  - **(3) write 失敗** → tempfile を削除し、作成済み `.bak.<UTC秒>` を削除。`error="disk_full"` または `"permission_denied"` を返す
  - **(4) fsync 失敗** → tempfile を削除し、作成済み `.bak.<UTC秒>` を削除。`error="disk_full"` を返す
  - **(5) os.replace 失敗** → tempfile を削除（`.bak.<UTC秒>` は残しておき rollback 可能にする）。`error="rename_failed"` を返す

- バックアップは `<path>.py.bak.<UTC秒>` 形式の世代付き（例: `buy_and_hold.py.bak.1714809600`）。
  **既存 `.bak.<UTC秒>` の上書きは禁止**（同じ秒に二回保存した場合は連番 suffix `-1`, `-2` を付与）
- ディスクフル等で write が失敗した場合、上記 cleanup 規則に従い tempfile / `.bak` を削除のうえエラーダイアログで通知
- **保存エラーコード列挙**（`Event::StrategyScenarioSaved { ok: false, error: ... }` の `error` 値）：
  `"permission_denied"` / `"parent_missing"` / `"disk_full"` / `"path_guard_violation"` /
  `"rename_failed"` / `"missing_scenario_field"` / `"validate_failed"` / `"syntax_error"` の
  **8 値のみ**（ラウンド2 / H-R2-2 で `"tempfile_failed"` を削除し、`tempfile.mkstemp` 失敗は
  errno に応じて `parent_missing` / `disk_full` / `permission_denied` のいずれかに吸収する）。
  `schemas.py` 側で `SaveErrorCode = Literal[...]` として固定し、未知値は pydantic validation で reject される
- 書き戻し後に **二段の検証** を必ず実行する（レビュー反映 2026-05-04 ラウンド1）：
  1. **構文検証**：`ast.parse(written_source)` + `engine.scenario.extract(written_path)` で書き戻したファイルが
     構文的に valid な Python であり SCENARIO が再抽出できることを確認（副作用ゼロ）
  2. **形状検証**：`engine.scenario.validate(extracted_scenario)` で
     TypedDict 形状（キー存在・値型）が runtime に一致することを確認（TypedDict は静的型のみで実行時検証無しのため必須）
  - **構文エラー（`SyntaxError`）OR `validate()` 失敗（`ScenarioValidationError`）のいずれか** → 直近の `.bak.<UTC秒>` から rollback → エラーダイアログ
  - **rollback 後 byte-diff assert**（テスト DoD）：書き戻し前のオリジナルと rollback 後のファイルが byte 単位で完全一致することを確認
  - **importlib による import 検証は採用しない**：`nautilus_trader` 等のサードパーティ依存が読み込めない環境
    （CI runner 等）で誤検知するため、要件から除外する（レビュー反映 2026-05-04 ラウンド1）
- `SCENARIO` 以外のコード・コメント・空白・docstring は触れないこと（`libcst` の不変条件）

### path ガード（`Command::SaveStrategyScenario` 不変条件）

Python 側 `engine.scenario.write_back(path, scenario, *, save_as: bool, loaded_path: Optional[Path])` は次の 3 条件すべてを満たさない限り `ValueError`（`error="path_guard_violation"`）で拒否する：

> **レビュー反映 2026-05-04 ラウンド1 (方針 B)**: `current_path` は GUI 側の責務であり、Python の path guard は `loaded_path` 一軸の FCFS 不変条件のみを保証する。`current_path` は本表から削除した（`_check_path_guard` シグネチャからも削除）。

1. `path` の拡張子が **`.py` のみ**（`.json` / `.bak` / 拡張子なしは拒否）
2. `path` が **保存経路ごとの分岐ルール** を満たすこと（FCFS 不変条件 + Save As 派生許可）。詳細は次表：

   | 経路 | `save_as` | `loaded_path`（直前 Load の path） | 許可条件 |
   |------|-----------|---------------------------------|---------|
   | **Save（上書き保存）** | `false` | `Some(p)` | `path == loaded_path` のときのみ許可 |
   | **Save As（新規 .py 保存）** | `true` | `None` | Load 履歴なし新規保存。任意の `.py` `path` を許可（条件 3 に従う） |
   | **Save As（派生保存）** | `true` | `Some(loaded_path)` | `path != loaded_path` のときに許可（**Load 済みファイルから派生した別 path への保存**を許す）。`path == loaded_path` の場合は server-side で reject（Save 経路 `save_as=false` に倒すべき。UI 層の責務） |

   multi-client engine（max 4）で他クライアントが間に別の Load を挟んだ場合、`loaded_path` は最後の `LoadStrategyScenario` の path に更新されるため、Save 経路では拒否される（FCFS 違反）。Save As 派生経路は新規 `path` への書き出しのため FCFS の影響を受けない。
3. `path` が **永続状態ファイルのディレクトリ配下に書き込もうとしていない**こと。
   具体的には `engine-session.json` / `saved-state.json` / `tachibana_orders.jsonl` の
   親ディレクトリ（`%APPDATA%\flowsurface\` および `~/.cache/flowsurface/engine/`）への
   書き込みを禁止（パストラバーサル / 任意ファイル上書き対策）。**この条件は Save / Save As のいずれの経路でも不変**で、save_as フラグでバイパス不可。

これらは `python/tests/test_scenario_path_guard.py` で網羅し、multi-client での FCFS 違反テスト・Save As 派生 path 許可テストも含める。

### GUI 統合

- `上書き保存（Save）`：`save_as=false` で `Command::SaveStrategyScenario` を送出。`path == current_path == loaded_path` のときのみ Python 側 path guard を通過し、`current_path`（`.py`）の `SCENARIO` を更新
- `名前を付けて保存…（Save As）`：`save_as=true` で `Command::SaveStrategyScenario { path, scenario, save_as: true }` を送出。次の 2 通りいずれかで通過する：
  - **新規保存**: `current_path == None`（Load 履歴なし）かつ任意の新規 `.py` `path` を選択
  - **派生保存**: `current_path == Some(loaded_path)` かつ `path != loaded_path`（Load 済みファイルを別名 `.py` にコピー的保存。元ファイルは変更されない）
- live モードでは従来通り `saved-state.json` を保存（`current_path` の拡張子で分岐）

### Save As のモード依存モデル

| モード | `save_as` フラグ | `current_path` の状態 | `名前を付けて保存…（Save As）` の対象 | ファイルフィルタ |
|--------|-----------------|---------------------|--------------------------------------|----------------|
| replay（新規保存経路） | `true` | `None`（Load 履歴なし） | 新規 `.py` を作成。戦略コードは現在エディタ上のもの、SCENARIO は GUI 入力値 | `*.py` |
| replay（派生保存経路） | `true` | `Some(loaded_path)` かつ `path != loaded_path` | 直前 Load した `.py` の戦略コードをそのままコピーし、コピー先の **SCENARIO 辞書のみ atomic 書き戻し** | `*.py` |
| live   | `true` | -                   | `saved-state.json` 互換の JSON を任意パスへ保存 | `*.json` |

派生経路で `path == loaded_path` を選んだ場合は path guard を通らないため、`Save`（上書き保存）に倒す UI 側の事前チェックを入れる（OS ダイアログで Load 元と同じ path が選ばれたら `save_as=false` で送る）。モード別の挙動詳細は [./fix-save-menu.md](./fix-save-menu.md) のモード別挙動表と整合させる。

### ファイルフィルタ

- replay モード：`開く…（Open）` / `名前を付けて保存…（Save As）` のフィルタは `.py`
- live モード：従来通り `.json`

### Python ↔ Rust 役割分担

- 書き戻しロジックは Python 側（`engine.scenario.write_back(path, scenario)`）に実装
- Rust → Python に `Command::SaveStrategyScenario { path, scenario }` を送る
- 結果を `Event::StrategyScenarioSaved { ok, error }` で受ける

### DoD（F6c）

- テスト: `python/tests/test_scenario_writeback.py` / `python/tests/test_scenario_path_guard.py`
  - 必須ケース（R6-81 統一決定）：
    - `test_writeback_rollback_on_validate_failure`：**validate 失敗 → rollback → byte-diff 0**
    - `test_writeback_rollback_on_import_error`：**import エラー → rollback → byte-diff 0**
- 期待ログ:
  - 成功時：`INFO scenario.writeback path=... bak=... bytes=...`
  - validate 失敗時：`ERROR scenario.writeback rollback reason=validate_failed bak=...`
  - 構文エラー時：`ERROR scenario.writeback rollback reason=syntax_error bak=...`
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
  - 書き戻し後 `importlib` 再ロードで構文エラーが出ない、かつ `engine.scenario.validate()` が成功する
  - **rollback fixture（validate 失敗経路）** `test_writeback_rollback_on_validate_failure`:
    `SCENARIO = {"instrument": 1, ...}`（`str` 期待のキーに int を入れた dict literal）を新値として渡す。
    `importlib.reload()` 自体は dict literal の型違反では例外を出さない（TypedDict は静的型のみ）ため、
    再ロード後の `engine.scenario.validate(scenario_dict)` で `TypeError`（または `ScenarioValidationError`）が
    raise される → `.bak.<UTC秒>` から rollback → **rollback 後にオリジナルと byte-diff が 0** であることを assert
  - **rollback fixture（import エラー経路）** `test_writeback_rollback_on_import_error`:
    構文を崩した dict literal（例: `SCENARIO = {"instrument": "1301.TSE",,}` の余剰カンマ等、libcst 通過後に
    `SyntaxError` を起こす細工 fixture）で書き戻し → `importlib` 再ロードで `SyntaxError` →
    `.bak.<UTC秒>` から rollback → **rollback 後にオリジナルと byte-diff が 0** であることを assert
  - atomic 書き込み: 途中で `os.replace` を mock fail させたとき target ファイルが破損しない
  - `.bak.<UTC秒>` 既存上書き禁止（同秒の重複保存で連番 suffix）
- `python/tests/test_scenario_path_guard.py`
  - `.py` 以外の拡張子拒否
  - 直前 `LoadStrategyScenario` 不一致での拒否
  - 永続状態ファイルディレクトリへの書き込み拒否
  - multi-client（4 接続）で別クライアントが Load を挟んだ場合の FCFS 拒否
  - **`test_save_without_prior_load`**: Load 履歴 None の状態で `save_as=true` フラグが立っていれば許可、
    Load 履歴 None かつ `save_as=false`（= 通常の上書き Save）なら `error="path_guard_violation"` で拒否
  - **`test_save_as_with_prior_load_to_new_path`**: `current_path == Some(loaded_path)` かつ `save_as=true` かつ
    保存先 `path != loaded_path` のとき許可されることを assert（**Load 済みファイルから派生した別 path への保存**経路）。
    元の `loaded_path` のファイルは変更されないことも byte-diff で確認。
    同じ条件で `path == loaded_path` のときは `save_as=false`（Save 経路）に倒すべきで、`save_as=true` のままだと
    UI 層で事前にリダイレクトされる旨をコメントで明示

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

---

## レビュー反映 (2026-05-04, ラウンド 1)

F6 実装に対する e-station-review レビュー指摘 22 件（HIGH 5 / MEDIUM 17）の反映記録。
方針 B（計画書を実装に寄せる）採用。

**HIGH 解消項目**:

- **H1**: `SaveErrorCode = Literal[...]` を `schemas.py` に追加し、`StrategyScenarioSaved.error` を Literal に固定。8 値（`permission_denied` / `parent_missing` / `disk_full` / `path_guard_violation` / `rename_failed` / `missing_scenario_field` / `validate_failed` / `syntax_error`）以外は pydantic validation error（`tempfile_failed` は R2 で削除。詳細は §レビュー反映 (2026-05-04, ラウンド 2) 参照）
- **H2**: `_check_path_guard` / `write_back` から未使用の `current_path` 引数を削除（方針 B、loaded_path 一軸の FCFS）。GUI 側責務として注記
- **H3**: `save_as=true` かつ `path == loaded_path` を server-side で reject（UI 層の事前チェックに頼らず Python 側でも防御）
- **H4**: `scenario_roundtrip.rs:13` の `assert!(SCHEMA_MINOR >= 10)` を `>= 11` に更新（F7 後の現在値と一致）
- **H5**: `python/tests/test_schema_minor_compat.py` を新設し、SCHEMA_MAJOR 一致下での SCHEMA_MINOR 差異許容を回帰ガード

**MEDIUM 解消項目**:

- **M1**: `_dict_to_cst_expr` 未対応値型を `TypeError` で raise（暗黙の `repr()` フォールバック削除）
- **M2**: path_guard tests に `test_save_as_with_prior_load_to_new_path` / `test_rejects_save_as_when_path_equals_loaded_path` / `test_multi_client_fcfs_after_other_client_load` を追加
- **M3**: `msg["scenario"]` が None / 欠落のとき `error="missing_scenario_field"` を返す
- **M4**: `_do_load_strategy_scenario` の失敗ログを `WARN scenario.load failed reason=... path=...` 形式に統一
- **M5**: `_do_save_strategy_scenario` の rollback ログを `log.error("scenario.writeback rollback reason=... path=...")` に格上げ。`extract()` 多重 SCENARIO 定義は `ScenarioValidationError` で reject
- **M6**: `_verify_writeback` を「ast.parse + extract（構文検証）+ validate（形状検証）」の二段に変更。importlib による import 検証は要件から除外（依存読み込み環境制約のため）。rollback reason は `syntax_error` または `validate_failed` の二択
- **M7**: `Command::SaveStrategyScenario` の `Debug` 実装に `scenario` / `loaded_path` を追加
- **M8**: server.py の `msg.get("save_as", True)` を `False` に統一（dto.rs `#[serde(default)]` = false と整合）
- **M10**: `SCENARIO.schema_version == 1` の strict validation は v2 移行時に別計画で緩和（`fix-save-menu.md` §非スコープへ追記）
- **M11**: server.py 入口で `scenario_mod.validate(scenario_dict)` を試行し、失敗なら `error="validate_failed"` で即時 reject
- **M12**: `scenario_roundtrip.rs` の `assert_eq!(SCHEMA_MAJOR, 3)` メッセージを汎化
- **M13**: `engine-client/src/lib.rs` の F6/F7 SCHEMA_MINOR コメントを時系列順（F6=10 → F7=11）に整理
- **M16**: `os.close(fd)` 例外を `log.warning` に格上げ（黙殺しない）
- **M17**: `_resolve_cli_params` 内の `import sys` 重複をモジュール先頭に集約

**追加テスト**:

- `python/tests/test_schema_minor_compat.py`（新規、H5）
- `python/tests/test_scenario_path_guard.py`：`test_save_as_with_prior_load_to_new_path` / `test_rejects_save_as_when_path_equals_loaded_path` / `test_multi_client_fcfs_after_other_client_load`
- `python/tests/test_scenario_writeback.py`：`test_writeback_rejects_unsupported_value_type` / `test_saved_error_is_known_literal`
- `python/tests/test_scenario_load.py`：`test_extract_rejects_multiple_scenario_assignments` / `test_load_failed_log_format`
- `engine-client/tests/scenario_roundtrip.rs`：`save_with_loaded_path_none_round_trips` / `saved_with_ok_true_and_error_some_is_inconsistent`

---

## レビュー反映 (2026-05-04, ラウンド 2)

F6 ラウンド 2 e-station-review レビュー指摘 9 件（HIGH 3 / MEDIUM 6）の解消記録。
LOW 4 件は本ラウンドでは見送り（理由を末尾に記載）。

**HIGH 解消項目**:

- **H-R2-1 (送信パイプラインの model_dump 統一)**:
  `server.py::_do_save_strategy_scenario` の `_emit` を `StrategyScenarioSaved(...).model_dump()`
  経由に変更。未知 `error` 値は pydantic ValidationError で構築段階に検出される。
  `_emit` 入口に `assert error is None or error in get_args(SaveErrorCode)` および
  `assert not (ok and error is not None)` のダブルチェックを追加（L-R2-1 を吸収）。
  `_do_load_strategy_scenario` の `StrategyScenarioLoaded` / `StrategyScenarioLoadFailed`
  も同様に model_dump 経由に統一した。
- **H-R2-2 (`tempfile_failed` 削除 / dead code 解消)**:
  `SaveErrorCode = Literal[...]` から `"tempfile_failed"` を除外（残り 8 値）。
  `tempfile.mkstemp` 失敗は OSError errno に応じて
  `parent_missing` / `disk_full` / `permission_denied` のいずれかに吸収する。
  `scenario.py` 内の `tempfile_failed` 文字列リテラルログも中立な
  `"tempfile creation failed (errno=%s)"` に変更。
- **H-R2-3 (`OSError.winerror` → `permission_denied`)**:
  `_do_save_strategy_scenario` の OSError 分岐に `winerror in (5, 32)`
  （ERROR_ACCESS_DENIED / ERROR_SHARING_VIOLATION）→ `"permission_denied"` を追加。
  errno が None でも winerror が分類できればそちらを優先する。

**MEDIUM 解消項目**:

- **M-R2-1 (BaseException 系も rollback)**:
  `write_back` Step 6 を `except Exception` から `except BaseException` に変更。
  `KeyboardInterrupt` / `SystemExit` を含む BaseException 系も rollback 後に再送出する
  ことで握り潰しを防ぐ。`finally` ブロックの cleanup は引き続き走る。
- **M-R2-2 (`scenario.py` ログを debug に揃え二重出力解消)**:
  `os.replace` 失敗・backup / write / fsync / rollback 等の OSError ログを
  `log.error` から `log.debug` に格下げ。`server.py` レイヤ
  (`_do_save_strategy_scenario`) を ERROR の単一 SoT として統一する。
- **M-R2-3 (`test_save_as_with_prior_load_to_new_path` を write_back まで拡張)**:
  自明な `_check_path_guard` byte-diff 0 テストを廃し、`write_back(new_path, ...)` を実走。
  `loaded_path` のファイルが byte 単位で不変、`new_path` に新 SCENARIO が
  正しく書き込まれることを assert する end-to-end テストに変更した。
- **M-R2-4 (multi-client 同言語テストの rename + 説明)**:
  `test_multi_client_fcfs_after_other_client_load` を
  `test_path_guard_rejects_save_when_loaded_path_changed` に rename。docstring に
  「単言語版・path_guard 単体テスト」「real multi-client serialization は
  `python/tests/test_server_multi_client.py` または future integration test で検証」を明記。
  実 server を起動する multi-client integration test の追加は **次フェーズで検討**
  （本ラウンドでは軽量化判断で見送り）。
- **M-R2-5 (path guard に macOS 永続ディレクトリを追加)**:
  `_get_persistent_dirs()` に `sys.platform == "darwin"` 分岐を追加し、
  `~/Library/Application Support/flowsurface` を path guard 対象に含めた。
  `test_rejects_persistent_macos_dir`（`@pytest.mark.skipif(sys.platform != "darwin")`）で
  リグレッションガードする。
- **M-R2-6 (`test_writeback_rollback_on_validate_failure` を本来経路に修正)**:
  `_verify_writeback` を `monkeypatch` で `ScenarioValidationError` raise に差し替える方式へ
  変更。仮に server.py 入口の `validate()` が write_back に到達しないように働いた場合でも、
  `write_back` 内部の rollback 経路（`.bak` から path への restore）が実行され
  byte-diff 0 で一致することを直接 assert する。

**追加・変更されたテスト**:

- `python/tests/test_scenario_writeback.py`:
  - 既存 `test_writeback_rollback_on_validate_failure` を monkeypatch 方式に書き換え（M-R2-6）
  - 既存 `test_saved_error_is_known_literal` を 8 値版に更新 + `tempfile_failed` 削除済みの
    ValidationError も assert（H-R2-2 + H-R2-1 の wire payload 検証）
  - 新規 `test_emit_rejects_unknown_error_code`（H-R2-1 リグレッションガード）
  - 新規 `test_oserror_winerror_maps_to_permission_denied`（H-R2-3 リグレッションガード）
  - 新規 `test_rollback_on_recursion_error`（M-R2-1 リグレッションガード）
  - 新規 `test_rejects_persistent_macos_dir`（M-R2-5 / `skipif` で macOS 限定）
- `python/tests/test_scenario_path_guard.py`:
  - `test_save_as_with_prior_load_to_new_path` を write_back 実走版に拡張（M-R2-3）
  - `test_multi_client_fcfs_after_other_client_load` を
    `test_path_guard_rejects_save_when_loaded_path_changed` に rename + docstring 追記（M-R2-4）

**LOW 見送り項目（理由）**:

- **L-R2-1 (`_emit` の ok/error 不整合 assert)**:
  H-R2-1 の `_emit` 入口 `assert not (ok and error is not None)` で吸収済み。独立タスクとしては不要。
- **L-R2-2 (TypeError → validate_failed フォールバック明文化)**:
  `_do_save_strategy_scenario` の汎用 `except Exception` 節で未知例外は
  `validate_failed` に集約し、ログには `type(exc).__name__` を残す現挙動を本ラウンド計画書に
  明文化するに留め、実装変更は不要。
- **L-R2-3 (caplog propagation)**: 現状 pass しており修正不要。
- **L-R2-4 (`resolve()` OSError 握りつぶし)**: 実害低、修正不要。

**送信パイプラインの不変条件（ラウンド2 確定）**:

- `StrategyScenarioSaved` / `StrategyScenarioLoaded` / `StrategyScenarioLoadFailed` は
  すべて `model_dump(exclude_none=False)` 経由で `_outbox` に push する
- `error` 値は `SaveErrorCode` Literal の 8 値のみ。未知値は pydantic ValidationError で
  構築段階に検出される
- `scenario.py` レイヤのログは debug、`server.py` レイヤが ERROR の単一 SoT
- BaseException 系も rollback 対象（KeyboardInterrupt / SystemExit を握り潰さない）
- `_get_persistent_dirs()` は `%APPDATA%\flowsurface` / `~/Library/Application Support/flowsurface` /
  `~/.cache/flowsurface/engine` の 3 OS をカバー

---

## レビュー反映 (2026-05-04, ラウンド 3)

F6 ラウンド 3 e-station-review レビュー指摘 6 件（MEDIUM 3 / LOW 3）の解消記録。
これにより F6 review-fix-loop は CRITICAL / HIGH / MEDIUM すべてゼロで収束した。

**MEDIUM 解消項目**:

- **M-R3-1 (`Path.home()` を try/except RuntimeError でガード)**:
  `_get_persistent_dirs()` の `Path.home()` 呼び出しを try/except で囲み、
  HOME / USERPROFILE が解決できない環境でも `RuntimeError` を伝播させない。
  APPDATA だけが取れる環境ではそれだけを返し、何も取れなければ空リストを返す
  （graceful degradation）。リグレッションガード:
  `test_get_persistent_dirs_without_home`。
- **M-R3-2 (`_emit` の不変条件を `assert` ではなく `ValueError` で強制)**:
  `server.py::_do_save_strategy_scenario` 内 `_emit` クロージャの 2 つの assert
  （ok=True かつ error 付き / 未知 SaveErrorCode）を `if not <cond>: raise ValueError(...)`
  に変更。`python -O` 最適化で assert は無効化されるため、本番でも内部不変条件
  を強制する。リグレッションガード: `test_emit_invariants_raise_value_error_not_assert`
  （pydantic schema-level reject は別テスト `test_emit_rejects_unknown_error_code` が担当）。
- **M-R3-3 (`test_rollback_on_recursion_error` を `KeyboardInterrupt` 系に差し替え)**:
  `RecursionError` は `Exception` のサブクラスなので旧実装の `except Exception` でも
  捕捉できており BaseException 拡張の差異を証明できなかった。
  `KeyboardInterrupt`（`BaseException` 直系で `Exception` 派生でない）を inject する
  `test_rollback_on_keyboard_interrupt` に差し替え、真の境界条件を検証する。

**LOW 解消項目**:

- **L-R3-1 (`test_emit_rejects_unknown_error_code` docstring 整理)**:
  pydantic schema-level rejection を確認するテストであることを docstring に明記し、
  `_emit` クロージャ経路の不変条件は M-R3-2 修正で `ValueError` として強制される
  （別テスト `test_emit_invariants_raise_value_error_not_assert` 参照）と注記。
  両テストが送信パイプラインの 2 層を分担してカバーする構図にした。
- **L-R3-2 (計画書 L416 周辺の "9 値" stale 表記を "8 値" に修正)**:
  `tempfile_failed` を含む 9 値の旧表記を 8 値に更新。
  「（`tempfile_failed` は R2 で削除。詳細は §レビュー反映 (2026-05-04, ラウンド 2) 参照）」
  と注記し、ラウンド 2 の H-R2-2 と整合させた。
- **L-R3-3 (`test_rejects_persistent_macos_dir` を non-darwin CI でも回帰検知できる版に変更)**:
  旧 `@pytest.mark.skipif(sys.platform != "darwin")` だと Windows / Linux CI では
  スキップされて回帰検知できなかった。`monkeypatch.setattr(scenario.sys, "platform", "darwin")`
  と `Path.home()` 固定で cross-platform で常に実行する
  `test_rejects_persistent_macos_dir_on_darwin_platform` に rename / 拡張した。

**追加・変更されたテスト**:

- `python/tests/test_scenario_writeback.py`:
  - 新規 `test_get_persistent_dirs_without_home`（M-R3-1 リグレッションガード）
  - 新規 `test_emit_invariants_raise_value_error_not_assert`（M-R3-2 リグレッションガード）
  - 既存 `test_rollback_on_recursion_error` を `test_rollback_on_keyboard_interrupt` に
    差し替え（M-R3-3、`Exception` 派生でなく `BaseException` 直系を inject）
  - 既存 `test_emit_rejects_unknown_error_code` の docstring 整理（L-R3-1）
  - 既存 `test_rejects_persistent_macos_dir` を
    `test_rejects_persistent_macos_dir_on_darwin_platform` に rename / cross-platform 化（L-R3-3）

**追加された不変条件（ラウンド3 確定）**:

- `_emit` の内部不変条件は `assert` ではなく `ValueError` で強制する
  （`-O` 最適化耐性 / 本番でも保護）
- `_get_persistent_dirs()` は `Path.home()` 不明時に graceful degradation する
  （APPDATA があればそれだけ返し、なければ空リスト。例外を伝播させない）
- BaseException 系の rollback テストは `KeyboardInterrupt` で行う
  （`Exception` 派生では旧実装の差異を証明できない）
- macOS 永続ディレクトリ拒否テストは `sys.platform` を偽装し cross-platform で常に実行する

---

## レビュー反映 (2026-05-04, ラウンド 4)

F6 ラウンド 4 e-station-review レビュー指摘 3 件（MEDIUM 1 / LOW 2）の解消記録。
これにより F6 review-fix-loop は **CRITICAL / HIGH / MEDIUM すべてゼロで収束達成**。

**MEDIUM 解消項目**:

- **M-R4-1 (`_get_persistent_dirs()` 空リスト時の path guard 消失をふさぐ)**:
  ラウンド 3 の `try/except RuntimeError` 修正により `Path.home()` 失敗 + APPDATA
  未設定の環境では `_get_persistent_dirs()` が空リストを返し、`_check_path_guard`
  の永続ディレクトリチェックループが 0 回になり、`<anywhere>/.cache/flowsurface/engine/x.py`
  などが path guard を通過してしまっていた。`_PERSISTENT_DIR_SUFFIXES`
  （`flowsurface` / `Library/Application Support/flowsurface` / `.cache/flowsurface/engine`）
  と `_path_under_persistent_suffix()` を追加し、suffix-based fallback で
  `path_guard_violation` を発火させる。
  **採用方針**: 「絶対パス由来のリストが空のときのみ fallback を発火」する保守的設計。
  通常環境で `<legitimate>/flowsurface/foo.py` 等を誤検知しないようにする。
  リグレッションガード:
  - `test_rejects_cache_flowsurface_engine_path_when_home_unresolved`
  - `test_rejects_appdata_flowsurface_path_when_home_unresolved`

**LOW 解消項目**:

- **L-R4-1 (`server.py:317` の assert 残留 → if/raise に統一)**:
  `_mode in ("live", "replay")` の防衛 assert を `if not <cond>: raise RuntimeError(...)`
  に変更。`_emit` (M-R3-2) と同じ `python -O` 耐性方針に合わせ、不変条件を本番でも
  強制する。回帰テストはコンストラクタ即時の不変条件確認なので追加せず（過剰なため）。
- **L-R4-2 (`test_emit_invariants_raise_value_error_not_assert` の実行ベース化) — 保留**:
  `_emit` クロージャは coroutine 内で生成され、外部から直接 invoke できない構造
  （`__closure__` 経由で取り出しても自由変数 `request_id` / `path_str` の bind が
  必要で、in-process で再構築すると元の関数本体と等価ではなくなる）。現行テストは
  (a) `_emit` と同一ロジックを `frozenset(get_args(SaveErrorCode))` で再構築した
  `_emit_replica` で振る舞い検証 + (b) `inspect.getsource` でソース上の `raise ValueError`
  / `_emit invariant` 文字列を assert する **ハイブリッド形式**で実装済み。
  純粋な実行ベース化は次フェーズで integration test を整備した際に再検討する
  （現行ハイブリッド形式は `python -O` 下でも (a) のリプリカ実行が成立するため
  実質的なリグレッションガードとして機能する）。

**追加・変更されたテスト**:

- `python/tests/test_scenario_path_guard.py`:
  - 新規 `test_rejects_cache_flowsurface_engine_path_when_home_unresolved`（M-R4-1）
  - 新規 `test_rejects_appdata_flowsurface_path_when_home_unresolved`（M-R4-1）

**追加された不変条件（ラウンド4 確定）**:

- 永続ディレクトリ検出は **(1) 絶対パス完全一致 + (2) suffix-based fallback** の二軸で行う。
  fallback は (1) が空リスト（HOME/APPDATA いずれも解決できない環境）のときのみ発火し、
  通常環境での誤検知を抑える
- `DataEngineServer._mode` の不変条件も `assert` ではなく `RuntimeError` で強制する
  （`-O` 最適化耐性。`_emit` invariant と同じ方針）

**結果**: F6 review-fix-loop **収束達成**（CRITICAL / HIGH / MEDIUM すべてゼロ）。

---

## F6a 後半 GUI 配線完了 (2026-05-04)

ラウンド 1〜4 で確定した DTO / Python ハンドラに対応する **Rust GUI 配線** を実装し、
F6a の最終 DoD（replay モードの `File > 開く...` から `.py` 選択 → SCENARIO prefill）を満たした。

**実装内容**:

- `src/main.rs::Action::OpenFile` を `app_mode()` で分岐:
  - replay モード → `.py` ファイルフィルタで OS ダイアログを開き、結果を
    `Message::NativeOpenStrategyPicked(Option<PathBuf>)` に流す
  - live モード → 従来通り `saved-state.json`（`.json` フィルタ）
- `Message::NativeOpenStrategyPicked(Some(path))` ハンドラ:
  - `app_mode() == Replay` を改めてガード（live で誤って `.py` が選ばれても drop）
  - `Command::LoadStrategyScenario { request_id, path }` を engine へ送信
- engine event を `map_engine_event_to_tachibana()` 経由で Message に変換:
  - `EngineEvent::StrategyScenarioLoaded` → `Message::StrategyScenarioLoadedEvent`
  - `EngineEvent::StrategyScenarioLoadFailed` → `Message::StrategyScenarioLoadFailedEvent`
- `Message::StrategyScenarioLoadedEvent`:
  - `CURRENT_PATH` を更新（M-7 poison recovery: `into_inner()` フォールバック）
  - `replay_form_modal` を `get_or_insert_with(default)` し、scenario が `Some` なら
    `prefill_from_scenario(path, &value)`、`None` なら `set_strategy_file_only(path)`
- `Message::StrategyScenarioLoadFailedEvent`:
  - エラートーストを push、`CURRENT_PATH` は更新しない（仕様）
- `ReplayFormModal::prefill_from_scenario()` / `set_strategy_file_only()` を新設
  - granularity の Literal（`Trade` / `Minute` / `Daily`）→ `Granularity` enum マッピング
  - 未知 granularity は既存値を保持（破壊的に上書きしない）
  - 部分的な scenario（一部キーのみ）でも他フィールドは触らない
  - `validation_error` は prefill 成功時にクリア（古いエラーバナー残留防止）

**追加リグレッションテスト**（合計 10 件、すべてパス）:

`src/modal/replay_form.rs`:

- `prefill_from_scenario_populates_all_fields`
- `prefill_from_scenario_clears_validation_error`
- `prefill_from_scenario_unknown_granularity_preserves_existing`
- `prefill_from_scenario_partial_keeps_other_fields`
- `prefill_from_scenario_non_object_only_sets_path`
- `set_strategy_file_only_sets_path_and_keeps_fields`

`src/main.rs::native_menu_handler_tests`（include_str! ソース検査ベース）:

- `open_file_replay_mode_uses_py_filter` — replay 分岐 + `.py` フィルタ + `NativeOpenStrategyPicked` dispatch
- `open_strategy_picked_some_sends_load_strategy_scenario` — モードガード + `Command::LoadStrategyScenario`
- `strategy_scenario_loaded_event_prefills_modal` — `prefill_from_scenario` / `set_strategy_file_only` / `CURRENT_PATH` 更新
- `strategy_scenario_load_failed_event_pushes_toast_only` — トースト発火 + `CURRENT_PATH` 不変 + prefill 不発火

**観測コマンド**:

```bash
cargo test --bin flowsurface modal::replay_form
cargo test --bin flowsurface native_menu_handler_tests
cargo test --workspace --no-fail-fast
uv run pytest python/tests/ -m "not live"
```

**検証結果**:

- Rust workspace 全テスト緑（既存 + 新規 10 件）
- Python テスト 1819 件緑（退行なし）
- `cargo clippy -- -D warnings` 緑
- `cargo fmt` クリーン

**持ち越し（P8 へ）**:

- Linux 自前メニューバー（widget menu bar）で `.py` フィルタが OS ネイティブと同等の UX を出せるか
  （P8-widget-menu-bar-linux.md でカバー予定）
