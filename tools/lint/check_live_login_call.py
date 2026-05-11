#!/usr/bin/env python3
"""issue #42 Phase 6 lint: ``LiveSession.login()`` 呼出の AST 検査.

統一決定 #19 / 受け入れ基準 #10 の対応 lint。
``python/engine/live_session_cli.py`` を AST 解析し、

    with LiveSession(...) as s:
        s.run(...)        # ← s.login(...) が同 with 内に無い

というパターンが存在しないことを assert する。``with LiveSession(...) as s:``
ブロックを束縛しているコンテキストマネージャ変数（例: ``s``）について、
ブロック内（および ``run()`` を呼び出す関数内）に ``s.login(...)`` 呼び出しが
存在することを要求する。

呼び出し方:

    from tools.lint.check_live_login_call import check_live_login_call
    check_live_login_call(cli_path=Path("python/engine/live_session_cli.py"))

    python -m tools.lint.check_live_login_call [--cli-path <PATH>]

Exit code:
    0  pass
    1  failure（login 未呼出パターンを検出 / ファイル不在）
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[2]
CLI_PATH_DEFAULT = REPO_ROOT_DEFAULT / "python" / "engine" / "live_session_cli.py"

LIVE_SESSION_CLASS_NAMES = ("LiveSession",)


class MissingLoginCallError(RuntimeError):
    """``LiveSession.login()`` 未呼出パターンを検出したときに raise."""


def _is_live_session_call(call: ast.expr) -> bool:
    """式 ``call`` が ``LiveSession(...)`` 形式の Call かどうか."""
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in LIVE_SESSION_CLASS_NAMES
    if isinstance(func, ast.Attribute):
        # e.g. ``replay_session.LiveSession(...)``
        return func.attr in LIVE_SESSION_CLASS_NAMES
    return False


def _collect_call_names(node: ast.AST, var_name: str, method: str) -> list[ast.Call]:
    """``node`` 配下から ``<var_name>.<method>(...)`` 呼出を全部集める."""
    found: list[ast.Call] = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == method
            and isinstance(f.value, ast.Name)
            and f.value.id == var_name
        ):
            found.append(n)
    return found


def _audit_with_block(
    with_node: ast.With | ast.AsyncWith,
    enclosing_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
    *,
    cli_path: Path,
    errors: list[str],
) -> None:
    """単一 ``with LiveSession(...) as s:`` ブロックを検査する."""
    for item in with_node.items:
        if not _is_live_session_call(item.context_expr):
            continue
        # `as <name>` で束縛された変数名のみを対象（束縛なしの ``with LiveSession():``
        # は ``run()`` 呼び出し経路を持てないので skip）
        if item.optional_vars is None or not isinstance(item.optional_vars, ast.Name):
            continue
        var_name = item.optional_vars.id

        # with ブロック内で run() が呼ばれているか?
        run_calls = _collect_call_names(with_node, var_name, "run")
        if not run_calls:
            continue
        # login() 呼び出しは「with ブロック自体」または「with を含む関数全体」
        # のどちらに存在しても良い（外側で login → with run の慣用句もありうる）。
        login_in_with = _collect_call_names(with_node, var_name, "login")
        login_in_func: list[ast.Call] = []
        if enclosing_func is not None:
            login_in_func = _collect_call_names(enclosing_func, var_name, "login")
        if login_in_with or login_in_func:
            continue
        first_run = run_calls[0]
        errors.append(
            f"{cli_path}:{first_run.lineno}: `{var_name}.run(...)` is called inside "
            f"`with LiveSession(...) as {var_name}:` but no `{var_name}.login(...)` "
            "call is found in the same with-block or enclosing function. "
            "受け入れ基準 #10 (issue #42) に違反します。"
        )


def check_live_login_call(*, cli_path: Path) -> None:
    """``cli_path`` の AST を解析し ``LiveSession.login`` 未呼出を reject する.

    Raises:
        MissingLoginCallError: pattern を検出 or ファイル不在.
    """
    cli_path = Path(cli_path)
    if not cli_path.is_file():
        raise MissingLoginCallError(
            f"CLI file not found: {cli_path!s}. "
            "受け入れ基準 #10 (issue #42): live_session_cli.py が必要です。"
        )
    source = cli_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(cli_path))
    except SyntaxError as exc:  # pragma: no cover — CI で SyntaxError は別途検知される
        raise MissingLoginCallError(
            f"{cli_path!s}: SyntaxError while parsing AST: {exc}"
        ) from exc

    errors: list[str] = []

    # 関数 / メソッド / モジュール直下それぞれで with 文を走査する。
    # enclosing_func を渡すことで「外側で login → with run」慣用句も許容する。
    def _walk(parent_func: ast.FunctionDef | ast.AsyncFunctionDef | None,
              body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.With, ast.AsyncWith)):
                _audit_with_block(
                    stmt, parent_func, cli_path=cli_path, errors=errors,
                )
                # with ブロック内にも入れ子の関数 / with があり得る
                _walk(parent_func, stmt.body)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk(stmt, stmt.body)
            elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try)):
                # 制御構文の body / orelse / handlers / finalbody も走査する
                _walk(parent_func, stmt.body)
                if hasattr(stmt, "orelse"):
                    _walk(parent_func, stmt.orelse)
                if isinstance(stmt, ast.Try):
                    for h in stmt.handlers:
                        _walk(parent_func, h.body)
                    _walk(parent_func, stmt.finalbody)
            elif isinstance(stmt, ast.ClassDef):
                _walk(parent_func, stmt.body)

    _walk(None, tree.body)

    if errors:
        msg = "\n".join(errors)
        raise MissingLoginCallError(msg)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.lint.check_live_login_call",
        description=(
            "issue #42 Phase 6 lint: assert LiveSession.login() is called "
            "before LiveSession.run() in live_session_cli.py (受け入れ基準 #10)."
        ),
    )
    parser.add_argument(
        "--cli-path", type=Path, default=CLI_PATH_DEFAULT,
        help="検査対象 CLI ファイル（既定: python/engine/live_session_cli.py）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        check_live_login_call(cli_path=args.cli_path.resolve())
    except MissingLoginCallError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
