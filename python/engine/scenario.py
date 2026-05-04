"""engine.scenario — SCENARIO 定数の読み込み・検証・書き戻しユーティリティ。

Public API:
    extract(path: Path) -> Optional[dict]
        .py から SCENARIO 定数を ast.parse + ast.literal_eval で安全抽出。
        import は発火しない（副作用ゼロ）。

    validate(d: dict) -> None
        Scenario TypedDict 形状を runtime 検証。失敗時は ScenarioValidationError を raise。

    write_back(path, scenario, *, save_as, loaded_path) -> None
        libcst で SCENARIO ブロックを atomic 書き戻し。
        tempfile + os.replace() による atomic write、世代付き .bak、2段検証 + rollback。

レビュー反映 (2026-05-04 ラウンド1, 方針 B):
    `current_path` 引数は本モジュールから削除。loaded_path 一軸の FCFS 不変条件
    のみを保証する（current_path は GUI 側責務）。
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
import sys
import tempfile
import time
import typing
from pathlib import Path
from typing import Optional, TypedDict

import libcst as cst

log = logging.getLogger(__name__)

SCHEMA_VERSION: int = 1

_NON_LITERAL_ERROR = (
    "dict literal 以外（unpacking {**...} / comprehension / 関数呼び出しを含む dict）は "
    "SCENARIO として読めません。リテラルの dict だけを使ってください"
)


class Scenario(TypedDict):
    schema_version: int
    instrument: str
    start: str
    end: str
    granularity: str
    initial_cash: int


# Scenario TypedDict から自動生成（3点管理廃止）
_EXPECTED_TYPES: dict[str, type] = typing.get_type_hints(Scenario)
REQUIRED_KEYS: frozenset[str] = frozenset(_EXPECTED_TYPES.keys())


class ScenarioValidationError(Exception):
    """SCENARIO 辞書の形状違反（必須キー欠落・型違反・余剰キー）。"""


# ---------------------------------------------------------------------------
# 永続状態ディレクトリ（path guard で書き込みを禁止する対象）
# ---------------------------------------------------------------------------


def _get_persistent_dirs() -> list[Path]:
    """OS ごとの永続状態ファイルディレクトリを返す。

    M-R2-5 (ラウンド2): macOS の `~/Library/Application Support/flowsurface` も
    含めることで、Linux/Windows と同等の path guard カバレッジを揃える。

    M-R3-1 (ラウンド3): `Path.home()` は HOME / USERPROFILE が解決できない環境で
    `RuntimeError` を raise するため、try/except で graceful degradation する。
    APPDATA だけが取れる環境ではそれだけを返し、何も取れなければ空リストを返す。

    呼び出し元は本リスト（絶対パス完全一致）に加えて、
    `_path_under_persistent_suffix` (suffix-based fallback) も併用すること。
    両者が併用されることで、HOME / APPDATA がいずれも解決できない環境でも
    path guard が消失しない（M-R4-1 ラウンド4）。
    """
    dirs: list[Path] = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        dirs.append(Path(appdata) / "flowsurface")
    try:
        home = Path.home()
    except RuntimeError:
        return dirs
    if sys.platform == "darwin":
        dirs.append(home / "Library" / "Application Support" / "flowsurface")
    dirs.append(home / ".cache" / "flowsurface" / "engine")
    return dirs


# M-R4-1 (ラウンド4): HOME / APPDATA がいずれも解決できない環境でも path guard が
# 消失しないようにするための suffix-based fallback。`_get_persistent_dirs()` が
# 空リストを返したときのみ発火し、誤検知を抑える。
_PERSISTENT_DIR_SUFFIXES: tuple[Path, ...] = (
    Path("flowsurface"),                                          # %APPDATA%/flowsurface
    Path("Library") / "Application Support" / "flowsurface",      # macOS
    Path(".cache") / "flowsurface" / "engine",                    # Linux
)


def _path_under_persistent_suffix(target: Path) -> bool:
    """target のいずれかの連続部分列が `_PERSISTENT_DIR_SUFFIXES` と一致するか判定。

    HOME / APPDATA がいずれも解決できない環境向けの保守的 fallback。
    `<anywhere>/.cache/flowsurface/engine/x.py` や `<anywhere>/flowsurface/x.py` の
    ような典型的な永続ディレクトリレイアウトを発見次第 True を返す。
    """
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        resolved = target
    parts = resolved.parts
    for suffix in _PERSISTENT_DIR_SUFFIXES:
        suffix_parts = suffix.parts
        if len(parts) < len(suffix_parts):
            continue
        for i in range(len(parts) - len(suffix_parts) + 1):
            if parts[i:i + len(suffix_parts)] == suffix_parts:
                return True
    return False


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def validate(d: dict) -> None:  # type: ignore[type-arg]
    """Scenario TypedDict の runtime 検証。失敗時は ScenarioValidationError を raise。

    - 必須キー欠落 → ScenarioValidationError
    - 余剰キー → ScenarioValidationError
    - 型違反（bool は int のサブクラスだが int として認めない） → ScenarioValidationError
    """
    if not isinstance(d, dict):
        raise ScenarioValidationError(f"SCENARIO must be a dict, got {type(d).__name__}")

    missing = REQUIRED_KEYS - d.keys()
    if missing:
        raise ScenarioValidationError(f"SCENARIO missing required keys: {sorted(missing)}")

    extra = d.keys() - REQUIRED_KEYS
    if extra:
        raise ScenarioValidationError(f"SCENARIO has unknown keys: {sorted(extra)}")

    for key, expected_type in _EXPECTED_TYPES.items():
        val = d[key]
        # bool は Python では int のサブクラス。schema では bool を許可しない
        if isinstance(val, bool) and expected_type is int:
            raise ScenarioValidationError(
                f"SCENARIO[{key!r}] must be int, got bool"
            )
        if not isinstance(val, expected_type):
            raise ScenarioValidationError(
                f"SCENARIO[{key!r}] must be {expected_type.__name__}, got {type(val).__name__}"
            )

    # schema_version 値チェック（型チェック通過後）
    if d.get("schema_version") != SCHEMA_VERSION:
        raise ScenarioValidationError(
            f"SCENARIO schema_version must be {SCHEMA_VERSION}, got {d.get('schema_version')!r}"
        )


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def extract(path: Path) -> Optional[dict]:  # type: ignore[type-arg]
    """path の .py から SCENARIO 定数を安全抽出する。

    ast.parse + ast.literal_eval のみ使用。import は一切発火しない。
    AnnAssign 形（`SCENARIO: Scenario = {...}`）と Assign 形（`SCENARIO = {...}`）の両方を許容。
    AnnAssign.value が None（注釈のみ宣言）はスキャンを継続する（後続の Assign を見つけるため）。

    Returns:
        SCENARIO dict が見つかった場合はその dict、見つからない場合は None。

    Raises:
        SyntaxError: path が構文エラーの .py の場合。
        ValueError: SCENARIO の値がリテラル dict でない場合（unpacking / comprehension / 関数呼び出し）。
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    found_result: Optional[dict] = None  # type: ignore[type-arg]

    for node in ast.iter_child_nodes(tree):
        scenario_value: Optional[ast.expr] = None

        if isinstance(node, ast.Assign):
            # SCENARIO = {...}
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "SCENARIO"
            ):
                scenario_value = node.value

        elif isinstance(node, ast.AnnAssign):
            # SCENARIO: Scenario = {...}  または  SCENARIO: Scenario
            if isinstance(node.target, ast.Name) and node.target.id == "SCENARIO":
                if node.value is None:
                    # SCENARIO: Scenario（注釈のみ宣言）→ スキャンを継続する
                    # 後続に SCENARIO = {...} が存在する可能性があるため return しない
                    log.debug("scenario.load path=%s annotation_only_decl (continue scanning)", path)
                    continue
                scenario_value = node.value

        if scenario_value is not None:
            # dict comprehension は拒否
            if isinstance(scenario_value, ast.DictComp):
                raise ValueError(_NON_LITERAL_ERROR)

            # plain dict literal 以外は拒否
            if not isinstance(scenario_value, ast.Dict):
                raise ValueError(_NON_LITERAL_ERROR)

            # dict unpacking（**other_dict → key が None）は拒否
            if any(k is None for k in scenario_value.keys):
                raise ValueError(_NON_LITERAL_ERROR)

            # safe 評価（任意コード実行なし）
            try:
                result = ast.literal_eval(scenario_value)
            except (ValueError, TypeError) as exc:
                raise ValueError(_NON_LITERAL_ERROR) from exc

            if not isinstance(result, dict):
                raise ValueError(_NON_LITERAL_ERROR)

            # M5 (レビュー反映 2026-05-04 ラウンド1): 多重 SCENARIO 定義は明示エラー
            if found_result is not None:
                raise ScenarioValidationError(
                    "multiple SCENARIO assignments are not supported"
                )
            found_result = result

    if found_result is not None:
        log.info("scenario.load path=%s keys=%d", path, len(found_result))
    return found_result


# ---------------------------------------------------------------------------
# libcst utilities（write_back 専用）
# ---------------------------------------------------------------------------


def _dict_to_cst_expr(d: dict) -> cst.BaseExpression:  # type: ignore[type-arg]
    """Python dict を libcst Dict ノードに変換する（str / int / bool 対応）。

    M1 (レビュー反映 2026-05-04 ラウンド1): str / int / bool 以外の値型は明示的に
    `TypeError` で reject する（暗黙の `repr()` フォールバックは Scenario の値型
    contract を破る危険があるため削除）。
    """
    elements: list[cst.DictElement] = []
    items = list(d.items())

    for i, (k, v) in enumerate(items):
        if isinstance(v, bool):
            v_node: cst.BaseExpression = cst.Name("True" if v else "False")
        elif isinstance(v, int):
            v_node = cst.Integer(str(v))
        elif isinstance(v, str):
            v_node = cst.SimpleString(repr(v))
        else:
            raise TypeError(
                f"unsupported scenario value type: {type(v).__name__} for key {k!r}"
            )

        is_last = i == len(items) - 1
        comma: cst.BaseParenthesizableWhitespace | cst.MaybeSentinel
        if is_last:
            comma = cst.MaybeSentinel.DEFAULT
        else:
            comma = cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))

        elements.append(
            cst.DictElement(
                key=cst.SimpleString(repr(k)),
                value=v_node,
                comma=comma,
            )
        )

    return cst.Dict(elements=elements)


class _ScenarioReplacer(cst.CSTTransformer):
    """既存の SCENARIO Assign/AnnAssign を新しい dict 値に差し替える。"""

    def __init__(self, new_value: cst.BaseExpression) -> None:
        self._new_value = new_value
        self.replaced: bool = False

    def leave_SimpleStatementLine(  # type: ignore[override]
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.BaseStatement:
        # M5 整合 (レビュー反映 2026-05-04 ラウンド1): 既に置換済みの場合は
        # 2 つ目以降の SCENARIO ノードに触らない。これにより
        # `SCENARIO: Scenario` (AnnAssign annotation-only) + `SCENARIO = {...}`
        # の両方を持つファイルでも、最初の AnnAssign のみ value 化され、
        # 重複した SCENARIO 定義が生まれない（extract() の多重定義 reject と整合）。
        if self.replaced:
            return updated_node

        for stmt in updated_node.body:
            if isinstance(stmt, cst.Assign):
                for target in stmt.targets:
                    if (
                        isinstance(target.target, cst.Name)
                        and target.target.value == "SCENARIO"
                    ):
                        new_stmt = stmt.with_changes(value=self._new_value)
                        self.replaced = True
                        return updated_node.with_changes(body=[new_stmt])

            elif isinstance(stmt, cst.AnnAssign):
                # AnnAssign annotation-only (value is None) は触らない。
                # 後続の `SCENARIO = {...}` Assign を置換対象とする
                # （test_replaces_scenario_when_annotation_only_precedes_assign 整合）。
                if (
                    isinstance(stmt.target, cst.Name)
                    and stmt.target.value == "SCENARIO"
                    and stmt.value is not None
                ):
                    new_stmt = stmt.with_changes(value=self._new_value)
                    self.replaced = True
                    return updated_node.with_changes(body=[new_stmt])

        return updated_node


def _insert_scenario_after_imports(
    module: cst.Module, new_value: cst.BaseExpression
) -> str:
    """SCENARIO が存在しない .py の場合、最後の import 直後に SCENARIO = {...} を挿入する。"""
    last_import_idx = -1
    for i, node in enumerate(module.body):
        if isinstance(node, cst.SimpleStatementLine):
            for stmt in node.body:
                if isinstance(stmt, (cst.Import, cst.ImportFrom)):
                    last_import_idx = i

    scenario_stmt = cst.SimpleStatementLine(
        body=[
            cst.Assign(
                targets=[
                    cst.AssignTarget(
                        target=cst.Name("SCENARIO"),
                    )
                ],
                value=new_value,
            )
        ],
        leading_lines=[cst.EmptyLine(), cst.EmptyLine()],
    )

    new_body = list(module.body)
    insert_pos = last_import_idx + 1  # 0 if no import (insert at top)
    new_body.insert(insert_pos, scenario_stmt)
    return module.with_changes(body=new_body).code


def _replace_or_insert_scenario(source: str, scenario: dict) -> str:  # type: ignore[type-arg]
    """libcst で SCENARIO ブロックを置換または挿入する。コメント・空白は完全保持。"""
    new_value = _dict_to_cst_expr(scenario)
    module = cst.parse_module(source)

    replacer = _ScenarioReplacer(new_value)
    new_module = module.visit(replacer)

    if replacer.replaced:
        return new_module.code

    # SCENARIO が存在しない場合は import 直後に挿入
    return _insert_scenario_after_imports(new_module, new_value)


# ---------------------------------------------------------------------------
# write_back 補助関数
# ---------------------------------------------------------------------------


def _make_bak_path(path: Path) -> Path:
    """世代付きバックアップパスを生成する。同秒重複時は -1, -2 ... suffix を付与する。"""
    utc_sec = int(time.time())
    bak = path.parent / f"{path.name}.bak.{utc_sec}"
    if not bak.exists():
        return bak
    idx = 1
    while True:
        bak = path.parent / f"{path.name}.bak.{utc_sec}-{idx}"
        if not bak.exists():
            return bak
        idx += 1


def _check_path_guard(
    path: Path,
    *,
    save_as: bool,
    loaded_path: Optional[Path],
) -> None:
    """path ガードを検証する。違反時は ValueError("path_guard_violation: ...") を raise。

    不変条件（Save / Save As 共通）:
        1. path の拡張子が .py のみ
        3. path が永続状態ディレクトリ配下でない

    Save（save_as=False）追加条件:
        2. loaded_path が Some(p) かつ path.resolve() == loaded_path.resolve()

    Save As（save_as=True）追加条件:
        2'. loaded_path が Some(p) のとき path.resolve() != loaded_path.resolve()
            （path == loaded_path の場合は Save 経路 `save_as=False` に倒すべき。
            UI 層で事前リダイレクトされる想定だが server-side でも防御する。
            H3 / レビュー反映 2026-05-04 ラウンド1）
        2''. loaded_path=None の場合は任意の新規 .py path を許可

    レビュー反映 (2026-05-04 ラウンド1, 方針 B): `current_path` 引数は削除。
    GUI 側責務として loaded_path 一軸の FCFS のみを保証する。
    """
    # 条件 1: 拡張子 .py 必須
    if path.suffix != ".py":
        raise ValueError(
            f"path_guard_violation: path must have .py extension, got {path.suffix!r}"
        )

    # 条件 3: 永続状態ディレクトリへの書き込み禁止（save_as でバイパス不可）
    # 二軸の検査:
    #   (1) 絶対パス完全一致（HOME/APPDATA が解決できる通常環境向け）
    #   (2) suffix-based fallback（HOME/APPDATA がいずれも解決できない環境で
    #       path guard が消失しないよう保守的に発火 / M-R4-1 ラウンド4）
    path_resolved = path.resolve()
    persistent_dirs = _get_persistent_dirs()
    for persistent_dir in persistent_dirs:
        try:
            persistent_dir_resolved = persistent_dir.resolve()
        except OSError:
            continue
        try:
            path_resolved.relative_to(persistent_dir_resolved)
        except ValueError:
            pass  # 配下でない → OK
        else:
            # relative_to が成功した = persistent_dir 配下 → 禁止
            raise ValueError(
                f"path_guard_violation: writing to persistent state directory is forbidden: {path}"
            )

    # M-R4-1 (ラウンド4): 絶対パス検査が空リストだった（HOME/APPDATA いずれも
    # 解決不能）場合のみ suffix-based fallback を発火させ、誤検知を最小化する。
    if not persistent_dirs and _path_under_persistent_suffix(path):
        raise ValueError(
            f"path_guard_violation: writing under persistent-dir suffix is forbidden: {path}"
        )

    # 条件 2: Save 経路は loaded_path と一致必須
    if not save_as:
        if loaded_path is None:
            raise ValueError(
                "path_guard_violation: Save requires a prior LoadStrategyScenario"
                " (loaded_path is None)"
            )
        if path_resolved != loaded_path.resolve():
            raise ValueError(
                f"path_guard_violation: Save path must match loaded_path"
                f" (path={path!r}, loaded_path={loaded_path!r})"
            )
    else:
        # 条件 2': Save As 経路で loaded_path がある場合、path == loaded_path は拒否
        # （UI 層で Save 経路に倒すべき。H3 / レビュー反映 2026-05-04 ラウンド1）
        if loaded_path is not None and path_resolved == loaded_path.resolve():
            raise ValueError(
                f"path_guard_violation: Save As with path == loaded_path is forbidden"
                f"; use Save (save_as=False) instead"
                f" (path={path!r}, loaded_path={loaded_path!r})"
            )


def _verify_writeback(path: Path, _scenario: dict) -> None:  # type: ignore[type-arg]
    """書き戻し後の二段検証: ast.parse + extract（構文）+ validate（形状）。

    レビュー反映 (2026-05-04 ラウンド1, M6):
        importlib による import 検証は採用しない。`nautilus_trader` 等の
        サードパーティ依存が読み込めない環境で誤検知するため要件から除外。
        構文エラーは extract() が raise する SyntaxError として検出され、
        形状違反は validate() が raise する ScenarioValidationError として検出される。

    Raises:
        SyntaxError: 書き戻したファイルが構文的に invalid な場合（extract 内 ast.parse）。
        ScenarioValidationError: SCENARIO 不在 または validate() が失敗した場合。
    """
    extracted = extract(path)
    if extracted is None:
        raise ScenarioValidationError("SCENARIO not found in written file")
    validate(extracted)


# ---------------------------------------------------------------------------
# write_back（公開 API）
# ---------------------------------------------------------------------------


def write_back(
    path: Path,
    scenario: dict,  # type: ignore[type-arg]
    *,
    save_as: bool,
    loaded_path: Optional[Path],
) -> None:
    """SCENARIO ブロックを path の .py に書き戻す。

    実行順序:
        1. path ガード検証（違反 → ValueError で即 abort）
        2. libcst で source を変換（SCENARIO を置換または挿入）
        3. tempfile 取得
        4. バックアップ（.bak.<UTC秒>）
        5. write + fsync
        6. os.replace（atomic）
        7. 二段検証（構文エラー → rollback / validate 失敗 → rollback）

    各ステップ失敗時の cleanup:
        (1) tempfile 失敗 → cleanup なし
        (2) backup 失敗 → tempfile 削除
        (3)/(4) write/fsync 失敗 → tempfile + .bak を削除
        (5) os.replace 失敗 → tempfile 削除（.bak は残す）
        (7) 検証失敗 → .bak から rollback

    レビュー反映 (2026-05-04 ラウンド1, 方針 B):
        `current_path` 引数は削除（GUI 側責務）。loaded_path 一軸の FCFS のみ保証。

    Args:
        path: 書き戻し先 .py
        scenario: 書き戻す Scenario dict（validate 済み推奨）
        save_as: True = Save As 経路, False = Save（上書き保存）経路
        loaded_path: 直前 LoadStrategyScenario で読み込んだ path（None = Load 履歴なし）

    Raises:
        ValueError: path ガード違反（"path_guard_violation: ..."）
        ScenarioValidationError: 書き戻し後の validate 失敗（rollback 済み）
        SyntaxError: 書き戻したファイルが構文的に invalid（rollback 済み）
        TypeError: scenario の値型が str/int/bool 以外（M1）
        OSError: ディスク IO エラー
    """
    _check_path_guard(path, save_as=save_as, loaded_path=loaded_path)

    if path.exists():
        source = path.read_text(encoding="utf-8")
        new_source = _replace_or_insert_scenario(source, scenario)
    else:
        # 新規ファイル（Save As 新規保存経路）
        new_value = _dict_to_cst_expr(scenario)
        new_source = cst.parse_module("").with_changes(
            body=[
                cst.SimpleStatementLine(
                    body=[
                        cst.Assign(
                            targets=[
                                cst.AssignTarget(
                                    target=cst.Name("SCENARIO"),
                                )
                            ],
                            value=new_value,
                        )
                    ]
                )
            ]
        ).code

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    bak_path = _make_bak_path(path) if path.exists() else None

    tmp_fd: Optional[int] = None
    tmp_path: Optional[Path] = None

    try:
        # Step 1: tempfile 取得
        try:
            fd, tmp_path_str = tempfile.mkstemp(dir=parent, suffix=".tmp")
            tmp_fd = fd
            tmp_path = Path(tmp_path_str)
        except OSError as exc:
            # ラウンド2 / H-R2-2: tempfile_failed コードは廃止。errno に応じて
            # parent_missing / disk_full / permission_denied として server.py が分類する。
            # ここは debug にとどめ ERROR 単一 SoT を server.py に集約する（M-R2-2）。
            log.debug(
                "scenario.writeback: tempfile creation failed (errno=%s): %s",
                getattr(exc, "errno", None), exc,
            )
            raise

        # Step 2: バックアップ（既存ファイルがある場合のみ）
        if bak_path is not None:
            try:
                shutil.copy2(path, bak_path)
            except OSError as exc:
                # M-R2-2: server.py が ERROR の単一 SoT。scenario.py 層は debug。
                log.debug("scenario.writeback: backup failed: %s", exc)
                _safe_unlink(tmp_path)
                tmp_path = None
                raise

        # Step 3: write
        encoded = new_source.encode("utf-8")
        try:
            os.write(fd, encoded)
        except OSError as exc:
            # M-R2-2: server.py が ERROR の単一 SoT。scenario.py 層は debug。
            log.debug("scenario.writeback: write failed: %s", exc)
            _safe_unlink(tmp_path)
            tmp_path = None
            _safe_unlink(bak_path)
            raise

        # Step 4: fsync + close
        try:
            os.fsync(fd)
        except OSError as exc:
            # M-R2-2: server.py が ERROR の単一 SoT。scenario.py 層は debug。
            log.debug("scenario.writeback: fsync failed: %s", exc)
            _safe_unlink(tmp_path)
            tmp_path = None
            _safe_unlink(bak_path)
            raise
        finally:
            try:
                os.close(fd)
            except OSError as exc:
                # M16 (レビュー反映 2026-05-04 ラウンド1): fd.close() 失敗は黙殺せず WARN
                log.warning("scenario.writeback: fd.close() failed: %s", exc)
            tmp_fd = None

        # Step 5: atomic replace
        try:
            os.replace(tmp_path, path)
            tmp_path = None  # 移動成功 → 削除不要
        except OSError as exc:
            # M-R2-2: server.py が ERROR の単一 SoT。scenario.py 層は debug。
            log.debug("scenario.writeback: rename failed: %s", exc)
            _safe_unlink(tmp_path)
            tmp_path = None
            raise

        # Step 6: 二段検証（構文 + 形状）
        # M-R2-1 (ラウンド2): BaseException 系（RecursionError / MemoryError 等）も
        # rollback 対象に含める。KeyboardInterrupt / SystemExit は raise で再送出する
        # ことで上位に伝播する（握り潰さない）。
        try:
            _verify_writeback(path, scenario)
        except BaseException as exc:
            # M6 (レビュー反映 2026-05-04 ラウンド1): rollback reason は
            # syntax_error / validate_failed の二択。importlib 検証は要件外。
            reason = "syntax_error" if isinstance(exc, SyntaxError) else "validate_failed"
            # M-R2-2: server.py が ERROR の単一 SoT。scenario.py 層は debug。
            log.debug(
                "scenario.writeback rollback reason=%s path=%s bak=%s",
                reason,
                path,
                bak_path,
            )
            # rollback
            if bak_path is not None and bak_path.exists():
                try:
                    shutil.copy2(bak_path, path)
                except OSError as rb_exc:
                    log.error(
                        "scenario.writeback rollback_failed path=%s bak=%s: %s (original: %s)",
                        path, bak_path, rb_exc, exc,
                    )
            else:
                # Save As 新規ファイル：.bak なし → 書き戻したファイルを削除する
                _safe_unlink(path)
            raise

        log.info(
            "scenario.writeback path=%s bak=%s bytes=%d",
            path,
            bak_path,
            len(encoded),
        )

    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError as exc:
                # M16: 黙殺せず WARN
                log.warning("scenario.writeback: tmp_fd.close() failed: %s", exc)
        if tmp_path is not None:
            _safe_unlink(tmp_path)


def _safe_unlink(p: Optional[Path]) -> None:
    if p is None:
        return
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
