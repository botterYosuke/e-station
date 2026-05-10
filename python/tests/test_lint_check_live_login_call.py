"""issue #42 Phase 6: ``tools/lint/check_live_login_call.py`` のユニットテスト.

統一決定 #19 / 受け入れ基準 #10: ``python/engine/live_session_cli.py`` で
``LiveSession.login()`` を呼ばずに ``s.run()`` を呼ぶ AST パターンが存在しないこと
を assert する lint script の挙動を pin する。

検出ターゲット pattern:

    with LiveSession(...) as s:
        s.run(...)             # ← s.login(...) なし → reject

OK pattern:

    with LiveSession(...) as s:
        s.login(...)
        s.run(...)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools.lint.check_live_login_call import (
    MissingLoginCallError,
    check_live_login_call,
)


def _write_cli(tmp_path: Path, body: str) -> Path:
    """tmp_path に ``live_session_cli.py`` を擬似配置する.

    関数はファイルパスを直接受けるので最小ハーネスで足りる。
    """
    f = tmp_path / "live_session_cli.py"
    f.write_text(body, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# GREEN: login() 呼出があるケース
# ---------------------------------------------------------------------------


def test_with_block_calls_login_before_run_passes(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        def main():
            with LiveSession(venue="tachibana") as s:
                s.login(user_id="u", password="p")
                s.run(instrument_id="x", strategy_file="x.py", max_qty=1, max_notional_jpy=1)
        """
    )
    f = _write_cli(tmp_path, body)
    assert check_live_login_call(cli_path=f) is None


def test_login_in_try_block_still_counts(tmp_path: Path) -> None:
    """try / except に包まれた login() でも通る（関数内で 1 度でも呼ばれていれば OK）."""
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        def main():
            with LiveSession() as s:
                try:
                    s.login(user_id="u")
                except RuntimeError:
                    return 1
                s.run()
        """
    )
    f = _write_cli(tmp_path, body)
    assert check_live_login_call(cli_path=f) is None


def test_with_block_without_run_call_passes(tmp_path: Path) -> None:
    """run() を呼ばない with 文（例: フォーム validate のみ）は対象外で pass."""
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        def main():
            with LiveSession() as s:
                s.connect()
        """
    )
    f = _write_cli(tmp_path, body)
    assert check_live_login_call(cli_path=f) is None


# ---------------------------------------------------------------------------
# RED: login() なしで run() を呼ぶケース → reject
# ---------------------------------------------------------------------------


def test_with_block_run_without_login_fails(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        def main():
            with LiveSession() as s:
                s.run(instrument_id="x", strategy_file="x.py")
        """
    )
    f = _write_cli(tmp_path, body)
    with pytest.raises(MissingLoginCallError):
        check_live_login_call(cli_path=f)


def test_run_called_in_separate_function_without_login_fails(tmp_path: Path) -> None:
    """同じ ``with LiveSession(...) as s`` ブロック内で run() を呼ぶが login() がない."""
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        def main():
            with LiveSession() as s:
                # 登録忘れ
                s.run()
        """
    )
    f = _write_cli(tmp_path, body)
    with pytest.raises(MissingLoginCallError):
        check_live_login_call(cli_path=f)


def test_module_level_with_block_without_login_fails(tmp_path: Path) -> None:
    """モジュール直下（関数の外）の ``with LiveSession(...) as s:`` でも検出する.

    ``enclosing_func is None`` 経路の正しさを pin する。
    """
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        with LiveSession() as s:
            s.run()
        """
    )
    f = _write_cli(tmp_path, body)
    with pytest.raises(MissingLoginCallError):
        check_live_login_call(cli_path=f)


def test_class_method_with_login_passes(tmp_path: Path) -> None:
    """``class`` 内のメソッドにある ``with LiveSession(...) as s:`` でも login() を検出する.

    ``ast.ClassDef`` の body 走査経路を pin する（_walk が ClassDef を再帰している）。
    """
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        class Runner:
            def run_strategy(self):
                with LiveSession() as s:
                    s.login()
                    s.run()
        """
    )
    f = _write_cli(tmp_path, body)
    assert check_live_login_call(cli_path=f) is None


def test_class_method_with_run_without_login_fails(tmp_path: Path) -> None:
    """``class`` 内メソッドで run() を呼ぶが login() がない → reject."""
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        class Runner:
            def run_strategy(self):
                with LiveSession() as s:
                    s.run()
        """
    )
    f = _write_cli(tmp_path, body)
    with pytest.raises(MissingLoginCallError):
        check_live_login_call(cli_path=f)


def test_check_live_login_call_fails_when_cli_path_missing(tmp_path: Path) -> None:
    """``cli_path`` で指定したファイルが存在しないときは MissingLoginCallError."""
    nonexistent = tmp_path / "does_not_exist.py"
    with pytest.raises(MissingLoginCallError):
        check_live_login_call(cli_path=nonexistent)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def test_main_exit_code_zero_on_pass(tmp_path: Path) -> None:
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        def main():
            with LiveSession() as s:
                s.login()
                s.run()
        """
    )
    f = _write_cli(tmp_path, body)
    from tools.lint.check_live_login_call import main as cli_main
    rc = cli_main(["--cli-path", str(f)])
    assert rc == 0


def test_main_exit_code_nonzero_on_fail(tmp_path: Path, capsys) -> None:
    body = textwrap.dedent(
        """\
        from engine.replay_session import LiveSession

        def main():
            with LiveSession() as s:
                s.run()
        """
    )
    f = _write_cli(tmp_path, body)
    from tools.lint.check_live_login_call import main as cli_main
    rc = cli_main(["--cli-path", str(f)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "login" in err.lower()


# ---------------------------------------------------------------------------
# 受け入れ基準 #10: 実 repo の live_session_cli.py に対する pin
# ---------------------------------------------------------------------------


def test_live_session_cli_calls_login_before_run() -> None:
    """実 repo の ``live_session_cli.py`` で ``s.run()`` 前に ``s.login()`` が呼ばれている.

    受け入れ基準 #10 の対応 test 関数（issue #42 受け入れ表）。
    """
    repo_root = Path(__file__).resolve().parents[2]
    cli_path = repo_root / "python" / "engine" / "live_session_cli.py"
    check_live_login_call(cli_path=cli_path)
