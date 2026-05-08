# 実装計画書: SCENARIO `instruments_ref` による外部ユニバース参照（schema v3）

**Author**: blacksheep team (sasaicco@gmail.com)
**Date**: 2026-05-08
**Status**: Plan (R1 review-fix applied)
**Target**: [python/engine/scenario.py](../../python/engine/scenario.py), [python/engine/replay_session.py](../../python/engine/replay_session.py), [python/engine/server.py](../../python/engine/server.py), [python/engine/schemas.py](../../python/engine/schemas.py)

## 根本動機

`engine.scenario.extract()` は `ast.literal_eval` のみで SCENARIO 定数を抽出し、
import を一切発火させない（副作用ゼロ）安全設計を取っている。
これは保ちたい。一方で blacksheep の `order_flow_02` のように
**前日 jquants daily を走査して算出した数十〜千銘柄のユニバース**を扱う戦略では、
銘柄リストを `.py` に手で列挙するしかなく、

- コピペ事故
- 戦略 .py の diff 汚染（毎日 50〜1000 行が変わる）
- ユニバース更新時のメンテ負担

を生んでいる。

理想形:

```python
SCENARIO: Scenario = {
    "schema_version": 3,
    "instruments_ref": "data/universe/order_flow_02_2025-01-06.json#/instruments",
    "start": "2025-01-06",
    "end": "2025-01-10",
    "granularity": "Minute",
    "initial_cash": 1_000_000,
}
```

外部 JSON への参照だけを SCENARIO に書ける状態にする。

## 設計判断（採用案 = A）

| 案 | 概要 | 結論 |
|---|---|---|
| **A. schema v3 で `instruments_ref` 追加** | `extract()` は literal dict のまま。直後に `resolve_refs()` を 1 段挟む | **採用** |
| B. blacksheep 側で inline スクリプト | e-station 変更ゼロ。但し戦略 .py の diff 汚染が解消されない | 却下 |
| C. `extract()` を whitelist 評価に拡張 | 攻撃面が増え安全性論証が複雑化 | 却下 |

採用理由: 「リテラル dict 抽出」の安全性を破壊せず、解決責務を独立関数に切り出せるため、
write_back / GUI / regression test への影響が最小。将来 `start_ref`, `params_ref` 等の追加も同形で展開できる。

## IPC 契約（R1-Finding-1 で先に固定）

`_do_load_strategy_scenario` が返す `StrategyScenarioLoaded` の wire 契約を **raw + 解決済み instruments の両持ち** に確定する。「raw を返すのか / resolved を返すのか / 両方か」で実装者が割れないよう、`resolve_refs()` 仕様も非破壊に揃える。

### 1. `resolve_refs()` は `instruments_ref` を **pop しない**（非破壊）

旧仕様（pop して `instruments` に置換）を破棄。新仕様:

```python
def resolve_refs(d: dict, *, base_dir: Path) -> dict:
    """v3 のとき instruments_ref を解決して `instruments` を **追加** する。

    元 d は破壊しない（dict(d) を返す）。
    instruments_ref キーは出力にも保持される（write_back / GUI 表示用）。
    instruments と instruments_ref の両立は依然エラー（`instruments_ref` が
    解決されたあと両方持つのはあり得ないため、入力時点で reject）。
    """
```

### 2. `_validate_v3` は **resolve 後の dict** を受ける（`instruments_ref` 同居 OK）

`_validate_v3` の `extra` キーチェックで `instruments_ref` を **許容キー** に含める:

```python
_OPTIONAL_KEYS_V3: frozenset[str] = frozenset({"instruments_ref"})
# extra = d.keys() - REQUIRED_KEYS_V3 - _OPTIONAL_KEYS_V3
```

`instruments` は必須（解決済み前提）。`instruments_ref` は任意（保持されていればそのまま）。

### 3. `StrategyScenarioLoaded` wire schema

[python/engine/schemas.py:1087](../../python/engine/schemas.py#L1087) を以下に拡張:

```python
class StrategyScenarioLoaded(IpcMessage):
    request_id: str
    path: str
    # raw dict（instruments_ref を含む / write_back で保持される）
    scenario: dict | None
    # 解決済み instruments（v3 + ref 経由のときだけ非 None）
    # v1/v2 や inline v3 では None（GUI 側は scenario["instrument(s)"] を見る）
    resolved_instruments: list[str] | None = None
    event: Literal["StrategyScenarioLoaded"] = "StrategyScenarioLoaded"
```

`resolved_instruments` を **追加フィールド**にすることで、

- GUI prefill は `resolved_instruments` を最優先に見る → **v3 + ref で正しく動く**
- 既存 v1/v2 は `resolved_instruments=None` のまま流れる → **回帰なし**
- `scenario` フィールドは raw のまま → **GUI が write_back で `instruments_ref` 文字列を保持できる**

### 4. Rust 側 prefill の更新（後述 Step 8 で扱う）

[src/modal/replay_form.rs:171](../../src/modal/replay_form.rs#L171) と
[src/menu_bar_state.rs:41](../../src/menu_bar_state.rs#L41) の `prefill_from_scenario`
は、`resolved_instruments` を **第 1 優先**で参照する分岐を追加する。
ない場合は既存の `instruments` / `instrument` フォールバックに落ちる。

```rust
// 優先順位: resolved_instruments (v3+ref) > instruments (v2) > instrument (v1)
if let Some(ids) = resolved_instruments {
    if !ids.is_empty() { self.instrument_id = ids.join(", "); }
} else if let Some(arr) = obj.get("instruments")... { ... }
else if let Some(s) = obj.get("instrument")... { ... }
```

呼び出し元は `prefill_from_scenario(path, &scenario, resolved_instruments.as_ref())`
に署名拡張する。

### 5. エラー taxonomy（R1-Finding-3 / -5 と整合）

`ScenarioValidationError` に `code` 属性を追加し、文字列パース依存を排除:

```python
class ScenarioValidationError(Exception):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code  # "unresolved_ref" / "schema" / None
```

- `resolve_refs` 内の参照解決失敗 → `code="unresolved_ref"`
- `_validate_v*` の形状違反 → `code="schema"` または `None`（既存挙動互換）

server 側は `exc.code == "unresolved_ref"` で分岐する。

**rollback reason との関係（R1-Finding-5）**:

| レイヤ | エラー分類軸 |
|---|---|
| `scenario.write_back._verify_writeback` の **rollback reason** | `syntax_error` / `validate_failed` の **2 値固定（変更なし）** |
| `server._do_save_strategy_scenario` の **SaveErrorCode** | `unresolved_ref` を含む 9 値（事前 validate で振り分け） |

つまり `unresolved_ref` は **server 層が write_back 呼び出し前に return する**コードであり、
`scenario.py` の `write_back()` 内には到達しない。`_verify_writeback` 内の
resolve 失敗は `validate_failed` に吸収される（rollback reason の枠は広げない）。

## 制約（既存設計の不変条件、壊さない）

1. `extract()` は `ast.literal_eval` のみを使用し、import を発火させない
2. SCENARIO は GUI から編集・書き戻し可能（libcst write_back と互換）
3. v1 / v2 SCENARIO は無修正で動作する（regression test 全件 pass）
4. `write_back()` は **resolve 前の元 dict** を保存する（`instruments_ref` 文字列を保つ）
5. path guard / FCFS / atomic write の不変条件は v3 でも維持

## 影響範囲

| ファイル | 変更内容 |
|---|---|
| [python/engine/scenario.py](../../python/engine/scenario.py) | `Scenario_v3` TypedDict 追加 / `_validate_v3` / `_resolve_json_pointer` / `resolve_refs` / `_dict_to_cst_expr` の v3 対応 / `validate()` の dispatch 拡張 |
| [python/engine/replay_session.py:2255](../../python/engine/replay_session.py#L2255) | `_extract_scenario` 直後に `resolve_refs(d, base_dir=Path(strategy_path).parent)` を呼ぶ |
| [python/engine/server.py:2811-2825](../../python/engine/server.py#L2811-L2825) | `_do_load_strategy_scenario` で `extract` → `resolve_refs` を実行（成功時は **resolve 後 dict を `scenario` として返す**。但し `instruments_ref` も保持して GUI に渡す） |
| [python/engine/schemas.py:1087-1097](../../python/engine/schemas.py#L1087-L1097) | `StrategyScenarioLoaded` に `resolved_instruments: list[str] \| None` フィールド追加 |
| [python/engine/schemas.py:88-97](../../python/engine/schemas.py#L88-L97) | `SaveErrorCode` Literal を 8 → **10 値**に拡張（`unresolved_ref` / `relative_ref_crosses_dir`） |
| [src/modal/replay_form.rs:171-220](../../src/modal/replay_form.rs#L171-L220) | `prefill_from_scenario` の署名に `resolved_instruments: Option<&[String]>` 追加 |
| [src/menu_bar_state.rs:39-72](../../src/menu_bar_state.rs#L39-L72) | `ReplayBarState::prefill_from_scenario` を同様に拡張 |
| 呼び出し元（`StrategyScenarioLoaded` 受信ハンドラ） | wire の `resolved_instruments` を Rust 側 prefill に流す |
| [python/tests/test_scenario_load.py](../../python/tests/test_scenario_load.py) | v3 系テスト 11 ケース追加 |
| [python/tests/test_scenario_writeback.py](../../python/tests/test_scenario_writeback.py) | v3 write_back / Save As 別ディレクトリ系 4 ケース追加 |
| Rust 側 unit テスト | `prefill_from_scenario` の v3+ref / inline / フォールバック回帰テスト |

GUI のレイアウト変更（編集 UI）は **本フェーズでは無し**。但し IPC 契約変更に伴う
**Rust 側 prefill 呼び出しの配線追加**は本フェーズに含む（R1-Finding-4）。
`instruments_ref` の編集 UI（テキスト編集や JSON 数のプレビュー）は別タスクとして後追い。

## 修正方針

### Step 1: `scenario.py` に v3 型・バリデーション・解決処理を追加

#### 1-1. `Scenario_v3` TypedDict と `REQUIRED_KEYS_V3`

```python
class Scenario_v3(TypedDict, total=False):
    schema_version: int
    instruments: list          # instruments_ref と排他
    instruments_ref: str       # instruments と排他
    start: str
    end: str
    granularity: str
    initial_cash: int

# 解決後の必須キー（resolve_refs 後のバリデーション用）
_EXPECTED_TYPES_V3: dict[str, type] = {
    "schema_version": int,
    "instruments": list,
    "start": str,
    "end": str,
    "granularity": str,
    "initial_cash": int,
}
REQUIRED_KEYS_V3: frozenset[str] = frozenset(_EXPECTED_TYPES_V3.keys())
```

#### 1-2. `_resolve_json_pointer(doc, pointer: str)` — RFC 6901 最小実装

- `""` または `"#"` → doc 全体
- `"#/a/b"` → doc["a"]["b"]
- list の場合 token を int に変換
- `~1 → /` / `~0 → ~` のアンエスケープ
- 解決失敗時は `ScenarioValidationError`

#### 1-3. `resolve_refs(d: dict, *, base_dir: Path) -> dict`

```python
def resolve_refs(d: dict, *, base_dir: Path) -> dict:
    """SCENARIO 内の `*_ref` フィールドを外部ファイルから解決する。

    v1 / v2 はそのまま返す（no-op）。v3 のとき instruments_ref を解決して
    `instruments` を **追加** した新 dict を返す（instruments_ref キーは保持）。
    元の d は破壊しない。

    Raises:
        ScenarioValidationError: 参照ファイル不在 / JSON Pointer 解決失敗 /
            `instruments` と `instruments_ref` の両立 / 型不一致。
            参照解決系の失敗は `code="unresolved_ref"` を持つ。
    """
```

実装ポイント:
- `d.get("schema_version") != 3` なら `dict(d)` をそのまま返す（v1/v2 互換）
- `instruments` と `instruments_ref` の両立は明示エラー（`code=None`）
- ファイル読み込み失敗（`OSError` / `json.JSONDecodeError`）は `ScenarioValidationError(..., code="unresolved_ref")` に包む
- 解決結果が `list[str]` 以外（`list` でない / 要素に非 str を含む）なら `code="unresolved_ref"` で拒否
- `base_dir` 引数は Path。**呼び出し元が文脈ごとに正しい値を渡す**（後述 Step 2 / 3 / 7 で文脈別に固定）
- 返却 dict は **元 dict のコピー + `instruments` キーを追加**（`instruments_ref` は保持）

#### 1-4. `_validate_v3(d: dict)` と `validate()` の dispatch 拡張

```python
_OPTIONAL_KEYS_V3: frozenset[str] = frozenset({"instruments_ref"})

def _validate_v3(d: dict) -> None:
    """v3 (schema_version=3) 専用。resolve_refs 後の dict を受ける前提。

    resolve 後は `instruments` が必須・`instruments_ref` は任意（保持されていれば許可）。
    """
    missing = REQUIRED_KEYS_V3 - d.keys()
    ...
    extra = d.keys() - REQUIRED_KEYS_V3 - _OPTIONAL_KEYS_V3
    ...
    # 型チェック（v2 と同じ instruments: list[str] 非空ルール）
    # instruments_ref が含まれていれば str 型チェックのみ（中身は resolve_refs が保証済み）
```

`validate()` の dispatch:

```python
sv = d.get("schema_version")
if sv == 1:
    _validate_v1(d)
elif sv == 2:
    _validate_v2(d)
elif sv == 3:
    _validate_v3(d)  # resolve 後の形状を見る
else:
    raise ScenarioValidationError(
        f"SCENARIO schema_version must be 1, 2 or 3, got {sv!r}"
    )
```

**重要**: v3 を `validate()` に渡すのは **resolve 後の dict**。
resolve 前の dict（`instruments_ref` を持つ）は GUI 側で write_back 用に保持し、
validate には通さない。

#### 1-5. `_dict_to_cst_expr` の v3 対応

`instruments_ref` は str なので既存の str 分岐で素通りする → **コード変更不要**。
ただしテストで明示確認する。

### Step 2: `replay_session.py` で resolve_refs を呼び出す

[python/engine/replay_session.py:2255](../../python/engine/replay_session.py#L2255) 付近:

```python
from engine.scenario import extract as _extract_scenario
from engine.scenario import resolve_refs as _resolve_refs  # 追加

...
scenario = _extract_scenario(Path(strategy_path))
if scenario is not None:
    # base_dir = strategy ファイル親（CLI 文脈では loaded_path 概念がないため strategy_path に固定）
    scenario = _resolve_refs(scenario, base_dir=Path(strategy_path).parent)
```

例外ハンドラに `ScenarioValidationError` を追加し、
既存の `(SyntaxError, ValueError)` と同列で「invalid syntax」ログ → `sys.exit(1)`。
（resolve 失敗は CLI で続行不可能）

CLI では Save As 経路がないため base_dir 取り違えのリスクは無い。GUI 経路は Step 4 で別扱い。

### Step 3: `server.py` (`_do_load_strategy_scenario`) で resolve_refs を呼ぶ

[python/engine/server.py:2811-2834](../../python/engine/server.py#L2811-L2834):

```python
scenario = scenario_mod.extract(Path(path_str))
resolved_instruments: list[str] | None = None
if scenario is not None:
    # base_dir = 読み込み元 .py の親（GUI Load 経路では当然 loaded_path == path_str）
    resolved = scenario_mod.resolve_refs(scenario, base_dir=Path(path_str).parent)
    if scenario.get("schema_version") == 3 and "instruments_ref" in scenario:
        # v3 + ref 経路のみ resolved_instruments を別フィールドとして送る
        resolved_instruments = list(resolved.get("instruments", []))

self._outbox.append(
    StrategyScenarioLoaded(
        request_id=request_id,
        path=path_str,
        scenario=scenario,                       # raw のまま送る（write_back 用）
        resolved_instruments=resolved_instruments,  # v3+ref 時のみ非 None
    ).model_dump(exclude_none=False)
)
```

`resolve_refs` 失敗（`ScenarioValidationError`）は既存の `Exception` ハンドラで
`StrategyScenarioLoadFailed(reason=...)` に流す（追加ロジック不要）。

ログ形式は既存の `WARN scenario.load failed reason=... path=...` を踏襲。

### Step 4: `_do_save_strategy_scenario` の write_back 経路

write_back は **resolve 前の dict（raw）** を扱う。GUI から送られてくる
`msg["scenario"]` は v3 の `instruments_ref` を含むことがある。

#### 4-1. base_dir の選定（R1-Finding-2 対応）

| 経路 | loaded_path | path_str | 採用する base_dir | 補足 |
|---|---|---|---|---|
| Save | `Some(p)` | `p` （= loaded_path） | `loaded_path.parent` | path_str.parent と一致 |
| Save As 同一ディレクトリ | `Some(p)` | `p.parent / new.py` | `loaded_path.parent` | 相対 ref の意味が変わらない |
| Save As 別ディレクトリ + 相対 ref | `Some(p)` | 別ディレクトリ | **明示エラー** | 後述 4-2 参照 |
| Save As 別ディレクトリ + 絶対 ref | `Some(p)` | 別ディレクトリ | `path_str.parent`（影響なし） | 絶対 ref は base_dir 非依存 |
| Save As 新規（Load 履歴なし） | `None` | 新規 .py | `path_str.parent` | ベースが取れないので保存先基準に倒す |

**採用方針**: Load 履歴がある場合は **常に `loaded_path.parent` を base_dir として ref を検証する**。
これにより「保存先ディレクトリを変えただけで相対 ref の意味が静かに変わる」事故を防ぐ。

#### 4-2. 別ディレクトリ Save As + 相対 ref の取り扱い

Save As で `loaded_path.parent != path_str.parent` かつ `instruments_ref` が
**相対パス**（`os.path.isabs` が False）なら、保存後にそのファイルを Load し直すと
ref が壊れる（base_dir が暗黙に変わる）。

選択肢:

1. **明示エラー（採用）**: `SaveErrorCode = relative_ref_crosses_dir` を追加し、
   GUI に「ref を絶対パスにするか、universe JSON を新ディレクトリにコピーしてください」と
   伝える。データ書き換えで救済しない（ユーザー意図を勝手に推測しない）。
2. ref を絶対パスに自動書き換え: 採用しない（ユーザーの意図と異なる可能性 + diff 汚染）。
3. 保存先 base_dir に対して resolve し直して通す: 採用しない（相対 ref の意味が黙って変わる）。

絶対 ref（`os.path.isabs(ref) == True` または `pathlib.PurePath(ref).is_absolute()`）の
場合は base_dir 非依存なので、Save As 別ディレクトリでもそのまま許可。

#### 4-3. 実装スケッチ

```python
# server.py _do_save_strategy_scenario
scenario_dict = msg.get("scenario")
if scenario_dict is None:
    _emit(False, "missing_scenario_field")
    return

# base_dir 決定（4-1）
if loaded_path is not None:
    base_dir = Path(loaded_path).parent
else:
    base_dir = Path(path_str).parent

# Save As 別ディレクトリ + 相対 ref の事前ガード（4-2）
if save_as and loaded_path is not None:
    ref = scenario_dict.get("instruments_ref") if isinstance(scenario_dict, dict) else None
    if isinstance(ref, str):
        ref_path_str = ref.split("#", 1)[0]
        is_abs = bool(ref_path_str) and Path(ref_path_str).is_absolute()
        if not is_abs and Path(loaded_path).parent.resolve() != Path(path_str).parent.resolve():
            _emit(False, "relative_ref_crosses_dir")
            return

# 事前 validate（resolve → validate）
try:
    resolved = scenario_mod.resolve_refs(scenario_dict, base_dir=base_dir)
    scenario_mod.validate(resolved)
except ScenarioValidationError as exc:
    # R1-Finding-3: 文字列パース依存を排し、code 属性で分岐
    if getattr(exc, "code", None) == "unresolved_ref":
        _emit(False, "unresolved_ref")
    else:
        _emit(False, "validate_failed")
    return

# write_back には raw dict を渡す（参照保持）
scenario_mod.write_back(
    Path(path_str), scenario_dict, save_as=save_as, loaded_path=loaded_path,
)
```

#### 4-4. `_verify_writeback` の resolve 化

`_verify_writeback` は `path.parent` を base_dir として使う（書き戻し直後のファイル
自身を Load した場合の挙動と一致させる）。これは Save 経路では `loaded_path.parent ==
path.parent` なので一致し、Save As 経路では「これから loaded_path となる新ファイル
基準」なので妥当。

```python
def _verify_writeback(path: Path, _scenario: dict) -> None:
    extracted = extract(path)
    if extracted is None:
        raise ScenarioValidationError("SCENARIO not found in written file")
    resolved = resolve_refs(extracted, base_dir=path.parent)
    validate(resolved)
```

`_verify_writeback` 内の resolve 失敗は **`syntax_error` ではないため必ず
`validate_failed` に分類される**（rollback reason は 2 値固定 / R1-Finding-5）。
理論上は Step 4-3 の事前 validate を通過しているため到達しないが、race（ref JSON が
書き戻し中に消えた等）は `validate_failed` で rollback する。

### Step 5: `SaveErrorCode` 拡張

[python/engine/schemas.py:88-97](../../python/engine/schemas.py#L88-L97):

```python
SaveErrorCode = Literal[
    "permission_denied",
    "parent_missing",
    "disk_full",
    "path_guard_violation",
    "rename_failed",
    "missing_scenario_field",
    "validate_failed",
    "syntax_error",
    "unresolved_ref",            # 追加: instruments_ref の参照解決失敗
    "relative_ref_crosses_dir",  # 追加: Save As で相対 ref の base_dir が変わる
]
```

`scenario.py` 層の rollback reason は **`syntax_error` / `validate_failed` の 2 値で不変**
（R1-Finding-5）。`unresolved_ref` / `relative_ref_crosses_dir` は **server 層が
write_back 呼び出し前に return する**コードなので scenario.py 内には現れない。
この層分離をモジュール docstring と `_emit` の docstring に明記する。

### Step 6: テスト追加

[python/tests/test_scenario_load.py](../../python/tests/test_scenario_load.py):

| Test | 内容 |
|---|---|
| `test_v3_scenario_with_instruments_ref` | tmp 配下に JSON を作って ref 経由で読み込まれること |
| `test_v3_scenario_inline_instruments` | instruments_ref 無しの v3（`instruments` 直書き）が v2 と同じく動く |
| `test_v3_scenario_both_keys_rejected` | instruments と instruments_ref 同居で `ScenarioValidationError` |
| `test_v3_scenario_missing_file` | ref 先 .json が存在しないと `ScenarioValidationError(code="unresolved_ref")` |
| `test_v3_scenario_pointer_invalid` | `#/nonexistent` で `code="unresolved_ref"` |
| `test_v3_scenario_pointer_root` | `#` または空 pointer で root をそのまま list として返す |
| `test_v3_scenario_pointer_escape` | `~0` / `~1` のエスケープ動作 |
| `test_v3_scenario_ref_must_be_list_of_str` | ref 先が `list[int]` / dict / str で `code="unresolved_ref"` |
| `test_v3_scenario_resolve_does_not_mutate_input` | `resolve_refs(d)` が元 d を破壊しない |
| `test_v3_scenario_resolve_keeps_instruments_ref` | resolve 後 dict に `instruments_ref` キーが残る（非破壊仕様 / R1-Finding-1） |
| `test_validate_v3_with_instruments_and_ref` | `instruments` + `instruments_ref` 両持ちの resolve 後 dict が validate を pass |
| `test_validate_v3_unresolved_dict_rejected` | resolve 前 dict（instruments 欠落）を validate に通すと reject |
| `test_scenario_validation_error_code_attr` | `ScenarioValidationError.code` 属性が正しくセットされる（R1-Finding-3） |

[python/tests/test_scenario_writeback.py](../../python/tests/test_scenario_writeback.py):

| Test | 内容 |
|---|---|
| `test_v3_writeback_preserves_instruments_ref` | raw dict を write_back → 読み戻しても `instruments_ref` 文字列が保たれる |
| `test_v3_writeback_verify_resolves` | `_verify_writeback` が ref を解決して validate に通す（tmp に JSON 存在） |
| `test_v3_writeback_unresolved_ref_rolls_back_as_validate_failed` | `_verify_writeback` 内で ref 解決失敗 → rollback reason は `validate_failed` で固定（R1-Finding-5） |
| `test_v3_save_as_relative_ref_crosses_dir_rejected` | server 層で Save As 別ディレクトリ + 相対 ref を `relative_ref_crosses_dir` で拒否（R1-Finding-2） |
| `test_v3_save_as_absolute_ref_crosses_dir_allowed` | 絶対 ref なら別ディレクトリ Save As でも通る |
| `test_v3_save_unresolved_ref_emits_save_error_code` | server の事前 validate 段階で `unresolved_ref` を返す（write_back 未到達） |

[python/tests/test_multi_instrument_acceptance.py](../../python/tests/test_multi_instrument_acceptance.py):

| Test | 内容 |
|---|---|
| `test_replay_session_loads_v3_with_instruments_ref` | replay_session の `_resolve_replay_params` が v3 + ref を読める E2E |
| `test_strategy_scenario_loaded_carries_resolved_instruments` | server `_do_load_strategy_scenario` が v3+ref で `resolved_instruments` を埋める |
| `test_strategy_scenario_loaded_v1_v2_resolved_is_none` | v1/v2 では `resolved_instruments=None` で wire を流れる（回帰防止） |

Rust 側 unit テスト（[src/modal/replay_form.rs](../../src/modal/replay_form.rs) / [src/menu_bar_state.rs](../../src/menu_bar_state.rs) の `#[cfg(test)] mod tests`）:

| Test | 内容 |
|---|---|
| `prefill_from_scenario_v1_uses_instrument_field` | 既存挙動回帰（`resolved_instruments=None`） |
| `prefill_from_scenario_v2_uses_instruments_array` | 既存挙動回帰（`resolved_instruments=None`） |
| `prefill_from_scenario_v3_inline_uses_instruments` | v3 inline で `instruments` 配列が使われる |
| `prefill_from_scenario_v3_resolved_takes_priority` | `resolved_instruments=Some([...])` が `instruments` より優先される |
| `prefill_from_scenario_v3_resolved_empty_falls_back` | `resolved_instruments=Some(empty)` の場合は触らない（既存値保持） |
| `replay_bar_state_prefill_v3_resolved` | `ReplayBarState::prefill_from_scenario` も同様に v3+resolved を扱える |

### Step 7: ドキュメント更新

[python/engine/scenario.py](../../python/engine/scenario.py) のモジュール docstring に
`resolve_refs()` を Public API として追記。`SCHEMA_VERSION` の意味と v1/v2/v3 の差分を表で記載。

## 実装順（依存順）

| # | Step | 依存 | 想定工数 | 状態 |
|---|---|---|---|---|
| 1 | `ScenarioValidationError.code` 属性追加 + 既存呼び出し互換確認 | なし | 0.25h | ✅ 完了 |
| 2 | `_resolve_json_pointer` + 単体テスト | なし | 0.5h | ✅ 完了 |
| 3 | `resolve_refs`（非破壊・instruments_ref 保持）+ 単体テスト | 1, 2 | 0.75h | ✅ 完了 |
| 4 | `Scenario_v3` TypedDict + `_validate_v3`（`instruments_ref` 任意キー）+ `validate()` dispatch | 3 | 0.5h | ✅ 完了 |
| 5 | `replay_session._resolve_replay_params` で resolve 呼び出し（base_dir = strategy 親）+ acceptance test | 4 | 0.5h | ✅ 完了 |
| 6 | `_verify_writeback` の resolve 化（base_dir = path.parent）+ v1/v2 regression 確認 | 4 | 0.5h | ✅ 完了 |
| 7 | `StrategyScenarioLoaded` に `resolved_instruments` フィールド追加 + `_do_load_strategy_scenario` で resolve & 別フィールド埋め | 4 | 0.5h | ✅ 完了（Python schemas.py + Rust dto.rs + proto + grpc_transport）|
| 8 | `SaveErrorCode` 拡張（`unresolved_ref` / `relative_ref_crosses_dir`） + schemas regression | 4 | 0.25h | ✅ 完了（schemas.py 10 値、SCHEMA_MINOR 23 bump、Rust / Python 全テスト通過）|
| 9 | `_do_save_strategy_scenario` で base_dir 選定 + 相対 ref ガード + `code` 分岐 | 6, 8 | 0.75h | ✅ 完了 |
| 10 | Rust `prefill_from_scenario` 署名拡張 + 呼び出し元配線 + Rust unit テスト | 7 | 1.0h | ✅ 完了（messages.rs / handlers/replay.rs / modal/replay_form.rs / menu_bar_state.rs / main.rs 全配線 + 6テスト追加）|
| 11 | write_back 系テスト追加（Save As 別ディレクトリ系含む） | 6, 9 | 0.75h | ✅ 完了 |
| 12 | docstring / module header 更新（層分離の明文化） | 全部 | 0.25h | ✅ 完了（scenario.py Public API docstring に resolve_refs・schema v1/v2/v3 一覧・layer contract を追記）|

### 実装知見（2026-05-08 Rust prefill 担当 / Step 10）

- `ReplayMsg::ScenarioLoaded` に `resolved_instruments: Option<Vec<String>>` を追加する際、`src/main.rs` のエンジンイベントマッピング（`..` で省略していた箇所）を exhaustive match に戻して明示的に渡す必要がある。`..` を残すと L1 指摘（サイレント破棄）が残存する。
- `prefill_from_scenario` の 3 引数化で既存テストがすべてコンパイルエラーになる。テスト側に `None` を追加するだけで既存挙動は完全保持。
- `Some(&[])` の空スライスは "触らない" セマンティクスにする（`!ids.is_empty()` ガード）。これで resolved_instruments が空のとき既存値が保持される。
- `else { if let ... }` が 2 つ連続する場合、Clippy は警告しない（単一 `if let` なら `else if let` に縮小できるが、連続 2 個は不可）。現在の形で OK。
- `scenario=None` 時の `resolved_instruments` は完全無視（仕様通り）。`resolved_instruments` は `scenario` が存在する文脈でのみ意味を持つ — コメント不要。

### レビュー反映 (2026-05-08, ラウンド 1 — Step 10 R1)

**解消した指摘**: CRITICAL 0 / HIGH 0 / MEDIUM 0 — 初回レビューで収束。

**残存 LOW**（対応不要）:
- RS-1: `else { if let }` ネスト — Clippy 警告なし、変更不要
- RS-2: `replay_form.rs` と `menu_bar_state.rs` の instrument 選択ロジック共通化 — 独立型のため YAGNI
- RS-3: `ReplayBarState::prefill_from_scenario` docstring に第3引数の言及なし — 機能上問題なし
- SF-1: `scenario=None` 時に `resolved_instruments` が無視される点のコメント欠如 — 仕様通りで明白
- TD-2: `resolved_instruments = Some([])` テストコメントに「Python 側では ScenarioValidationError になる正常系」の説明があると良い

**検証コマンド全緑確認**:
```
cargo check --workspace  ✅
cargo clippy --workspace -- -D warnings  ✅
cargo fmt --check  ✅
cargo test --workspace（FAILED なし）  ✅
```

### 実装知見（2026-05-08 scenario.py コア担当）

- `ScenarioValidationError` の `__init__` に `code` キーワード引数を追加することで、既存の `ScenarioValidationError("msg")` 呼び出しはすべて後方互換（`code=None` がデフォルト）。
- `_resolve_json_pointer` は RFC 6901 の `~0`/`~1` アンエスケープ順序に注意。`~1` を先に処理してから `~0` を処理する（逆順にすると `~01` が誤って `~` → `~1` → `/` になる）。
- `resolve_refs` で `instruments_ref` に `#` が含まれるかで `split("#", 1)` を分岐し、ポインタ部分を `"#" + pointer_part` に戻してから `_resolve_json_pointer` に渡す設計が明確。
- linter（isort）が import 順を自動整形するため、テストファイルの import 順を標準ライブラリ → サードパーティ → ローカルの順に揃えること。
- `test_saved_error_is_known_literal` は `SaveErrorCode` の変更を追う regression guard。`schemas.py` に `SaveErrorCode` が追加された場合は期待値セットも更新が必要。
- `StrategyScenarioLoaded` Rust enum variant に `resolved_instruments: Option<Vec<String>>` を追加する際は `#[serde(default, skip_serializing_if = "Option::is_none")]` を両方付けないと既存 Python サーバ（フィールド省略送信）でパニックする。
- proto の `repeated string` フィールドは Rust 側で空 Vec になる（absent 相当）。`grpc_transport.rs` で `is_empty()` を使って `None` に変換する設計が明確。
- 既存テストが `StrategyScenarioLoaded` を exhaustive match していた（`..` なし）場合、新フィールド追加で compile error になる。`..` を追加しても assertion の意味は変わらない。
- SCHEMA_MINOR を bump するとき：`engine-client/src/lib.rs`・`python/engine/schemas.py`・`engine-client/tests/schema_v2_4_nautilus.rs`・`python/tests/test_schemas_nautilus.py` の 4 か所すべてを同期する。
- `StrategyScenarioSaved.error` のコメントに "N 値に固定" という定数参照を書くと `SaveErrorCode` 拡張のたびに stale になる。コメントは値数を書かず「Literal で固定」と書き、詳細は `SaveErrorCode` 定義のコメントに委ねるほうがメンテしやすい。
- `replay_session._resolve_cli_params` では `from engine.scenario import ...` を関数内 import で書く慣例がある（モジュール先頭でなく関数内）。追加する `ScenarioValidationError` / `resolve_refs` の import も同じスタイルで揃えること。
- acceptance test で `Scenario_v3` TypedDict の `from engine.scenario import Scenario_v3` 型注釈は不要。`SCENARIO = {...}` の平文 dict で十分（`ast.literal_eval` は TypedDict annotation を評価しないため `extract()` が値を取れなくなる）。テスト内の strategy.py は `SCENARIO = {...}` の plain dict として書くこと。
- `_do_save_strategy_scenario` で `loaded_path_str` / `save_as` の取得を `validate` 呼び出し前に移動する必要がある（Step 9 では base_dir の選定に `loaded_path_str` が必要なため）。元の実装は `validate` の後で取得していたが、`resolve_refs → validate` の前に必要な情報なので順序を入れ替えた。
- `test_v3_writeback_preserves_instruments_ref` で「`instruments` キーが単独で書き込まれていない」を確認するアサーションは、ファイルテキストの文字列検索より `extract()` した dict でキーの存在を確認するほうが確実（`universe.json#/instruments` の値文字列に `instruments` が含まれるため）。
- Save As 別ディレクトリ + 絶対 ref のテストでは、保存先ファイルが事前に存在しないと `_check_path_guard`（Save As + `path != loaded_path` の条件）を通過しても `write_back` 内部で `_verify_writeback` が走り `_replace_or_insert_scenario` → 書き込みに失敗することがある。テストでは保存先 .py を事前に作成しておくこと。

## レビュー反映 (2026-05-08, ラウンド 1)

### 完了した指摘

- **M1 (MEDIUM)**: `schemas.py:1145` の `StrategyScenarioSaved.error` コメントが "9 値に固定" と古い → "Literal で固定・10 値に拡張済み" に更新。pydantic 定義・テストに変更なし（コメント修正のみ）。

### 残存 LOW（Step 10 スコープ外）

- **L1 (LOW)**: `src/main.rs:1626` の `..` が `resolved_instruments` をサイレント破棄する。`ReplayMsg::ScenarioLoaded` がフィールドを持たないため `prefill_from_scenario` に届かない。**Step 10（Rust prefill 署名拡張）で解消予定**。IPC 契約層（本 Agent 担当）には影響なし。

### テスト追加（本 Agent 担当分）

- `engine-client/tests/scenario_roundtrip.rs`: `strategy_scenario_loaded_with_resolved_instruments_deserializes` / `strategy_scenario_loaded_v1_resolved_instruments_absent_is_none` の 2 件追加
- `engine-client/tests/schema_v2_4_nautilus.rs`: SCHEMA_MINOR == 23 アサーション更新
- `python/tests/test_schemas_nautilus.py`: SCHEMA_MINOR == 23 アサーション更新（既存テスト `test_schema_minor_is_9_for_phase_b1` を更新）

合計 約 **6.5h**（v1/v2 のみ運用するユーザー向け編集 UI は別タスク）。

## レビュー反映 (2026-05-08, ラウンド 2) — Step 7b / 9 / 11 server.py 担当

### 完了した指摘

- **MEDIUM-1**: `_do_save_strategy_scenario` の `scenario_dict` が非 None の非 dict（例: list）のとき、`resolve_refs` 内の `.get()` で `AttributeError` が発生し `ScenarioValidationError` catch を素通りする → `scenario_dict is None or not isinstance(scenario_dict, dict)` に強化して `missing_scenario_field` で明示 reject。回帰防止テスト `test_v3_save_non_dict_scenario_emits_missing_scenario_field` を追加。

### 残存 LOW（対応不要）

- **LOW-1**: v1/v2 の `resolve_refs` コールが `dict(d)` コピーを生成するが実害なし。
- **LOW-2**: v3 inline の `resolved_instruments=None` は設計・テスト済み。
- **LOW-3**: 空 `instruments_ref` パス（`""`）のエラーコードが `relative_ref_crosses_dir` vs `unresolved_ref` に分岐するが、どちらも reject で正しい動作。

### テスト追加（この担当分）

- `python/tests/test_scenario_writeback.py`: Step 11 として 6 件追加（`test_v3_writeback_*` 4 件 + `test_v3_save_*` 3 件）
- `python/tests/test_multi_instrument_acceptance.py`: 2 件追加（`test_strategy_scenario_loaded_carries_resolved_instruments` / `test_strategy_scenario_loaded_v1_v2_resolved_is_none`）
- 合計新テスト: 9 件（52 → 53 件, pytest 全緑確認）

### Step 5 レビュー反映 (2026-05-08, ラウンド 1)

**解消した指摘**:
- **M1 (MEDIUM)**: `test_replay_session_loads_v3_with_instruments_ref` が `resolve_refs` 単体テストになっており `_resolve_cli_params` 統合経路を pin していなかった → `_resolve_cli_params` を呼ぶ統合テストに変更し、非破壊性確認は末尾で継続。docstring も `_resolve_cli_params` 言及に修正済み（旧称 `_resolve_replay_params` との乖離を解消）。
- **M2 (MEDIUM)**: `instruments` と `instruments_ref` 共存（both_keys）の `ScenarioValidationError` が `sys.exit(1)` に変換される CLI 経路が未テスト → `test_replay_session_v3_both_keys_exits` を追加。

**残存 LOW（対応不要）**:
- **L2**: `base_dir=tmp_path` の直書きが `Path(strategy_py).parent` と偶然一致している表記 → 単体テスト範囲内で実害なし。

**テスト追加**: 1 件（`test_replay_session_v3_both_keys_exits`）、Step 5 acceptance テスト計 4 件で収束。

## 互換性・リスク

| 項目 | 影響 |
|---|---|
| v1 / v2 SCENARIO | 無変更（resolve_refs は v1/v2 では no-op） |
| 既存 GUI | `scenario.instruments` が resolve 後の値で届くため表示は変わらない。`instruments_ref` 編集 UI は別タスク（無くても運用可） |
| 既存 write_back の atomic / path guard / FCFS | 変更なし（resolve は読み取り経路のみ） |
| blacksheep `order_flow_02` | `SCENARIO` を `instruments_ref` に書き換え、`data/universe/*.json` 直接参照に移行可能。**戦略 .py から 50〜1000 行の銘柄コードが消える** |
| 攻撃面 | JSON 読み込みのみ（`json.loads`）。任意コード実行は発生しない |
| 参照先 JSON のサイズ | 想定 〜数 MB。`json.loads` で同期読み込みで十分 |

### 潜在リスク

- **R1: `_verify_writeback` が ref を解決できない race**
  → write_back 直前に server 層が resolve→validate 済み（Step 9）。但し ref JSON が
    write_back 中に削除される race は残る。`_verify_writeback` 内の resolve 失敗は
    `ScenarioValidationError` として捕捉され、rollback reason は **`validate_failed`
    に固定**（`unresolved_ref` を rollback reason 側には伝播させない / R1-Finding-5）。
    Step 11 の `test_v3_writeback_unresolved_ref_rolls_back_as_validate_failed` で確認。
- **R2: base_dir の文脈依存**
  → 文脈ごとに固定する（R1-Finding-2 対応）:
    - CLI（`replay_session`）: `strategy_path.parent`
    - GUI Load（`_do_load_strategy_scenario`）: `path_str.parent`（= loaded_path.parent）
    - GUI Save / Save As（`_do_save_strategy_scenario`）: `loaded_path.parent`（あれば）
      / なければ `path_str.parent`
    - `_verify_writeback`: `path.parent`（書き戻し先 .py 自身基準）
    Save As で `loaded_path.parent != path_str.parent` かつ相対 ref のときは
    `relative_ref_crosses_dir` で明示エラー。
- **R3: 多重 SCENARIO 定義との相互作用**
  → `extract()` の多重定義 reject ロジックは無変更。`resolve_refs` は extract 後の
    単一 dict にしか作用しない。
- **R4: wire schema 後方互換**
  → `StrategyScenarioLoaded.resolved_instruments` は default `None` の追加フィールド。
    既存 Rust client は未知フィールドを ignore するため逆方向互換は壊れない。
    schemas SCHEMA_MINOR を bump する（major は据え置き）。

## オープン項目（将来）

- **HTTP / S3 スキーム対応** — v3 ではローカル JSON のみ。HTTP は v4 以降で検討（Q2 確定）。
- **GUI 側 `instruments_ref` 編集 UI** — 読み取り専用で `instruments` 数のプレビューを出す
  程度から開始。本フェーズでは扱わない。
- **`start_ref` / `params_ref` への横展開** — 同じ `*_ref` 命名規約で `resolve_refs` に
  分岐を足せば対応可能。需要が出てから実装。
- **Save As + 相対 ref の自動絶対化オプション** — 現在は `relative_ref_crosses_dir` で
  拒否のみ。GUI ダイアログで「絶対パスに変換して保存しますか？」を提示する選択肢は
  ユースケースが溜まってから検討。

## 完了条件

1. v1 / v2 既存テストが全件 pass（Python + Rust 両方）
2. v3 系テスト（Step 6 の全テーブル）が全件 pass
3. Rust `prefill_from_scenario` の v3+`resolved_instruments` 経路が unit テストで覆われている
4. blacksheep `order_flow_02` で `instruments_ref` を使った SCENARIO が
   replay 起動 → load → resolve → run まで通る（手動確認）
5. write_back 後に元 .py の `instruments_ref` 文字列が保持されている（git diff 0 行）
6. Save As で別ディレクトリへ相対 ref のまま保存しようとすると
   `SaveErrorCode = relative_ref_crosses_dir` が GUI に届き、ファイルは書き換わらない
7. `SaveErrorCode = unresolved_ref` / `relative_ref_crosses_dir` が
   pydantic Literal validation を通る（schemas regression test）
8. SCHEMA_MINOR が bump され、Rust engine-client の DTO 再生成テストが通る

---
