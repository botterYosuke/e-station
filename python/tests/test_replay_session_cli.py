"""test_replay_session_cli.py

subprocess 経由で CLI (python -m engine.replay_session) をテストする。
J-Quants データは不要（FileNotFoundError を出すことを確認するのが目的）。
"""
from __future__ import annotations

import os
import subprocess
import sys

# 子プロセスの stdout/stderr を UTF-8 で統一する（Windows の cp932 デフォルトを上書き）
_CHILD_ENV = {**os.environ, "PYTHONUTF8": "1"}


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "engine.replay_session",
            *args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        env=_CHILD_ENV,
    )


def test_cli_run_nonexistent_data_exits_nonzero():
    """存在しないデータで run すると非 0 終了する。"""
    result = _run_cli(
        "run",
        "--instrument", "NONEXISTENT.TSE",
        "--start", "2025-01-01",
        "--end", "2025-01-31",
        "--strategy", "/nonexistent/strategy.py",
    )
    assert result.returncode != 0


def test_cli_run_nonexistent_strategy_exits_nonzero(tmp_path):
    """データが存在しても strategy_file が無ければ非 0 終了する。

    check_data_exists をパスする環境がない可能性があるため、
    FileNotFoundError（データ or ストラテジー）のどちらかで非 0 になることを確認する。
    """
    strategy_path = str(tmp_path / "nonexistent.py")  # 作成しない
    result = _run_cli(
        "run",
        "--instrument", "1301.TSE",
        "--start", "2025-01-01",
        "--end", "2025-01-31",
        "--strategy", strategy_path,
    )
    assert result.returncode != 0


def test_cli_no_subcommand_exits_nonzero():
    """サブコマンドなしで実行すると非 0 終了する。"""
    result = _run_cli()
    assert result.returncode != 0


def test_cli_help_exits_zero():
    """--help は 0 終了する。"""
    result = _run_cli("--help")
    assert result.returncode == 0
    stdout = result.stdout or ""
    assert "replay_session" in stdout or "replay" in stdout.lower(), (
        f"Expected help text in stdout, got: {stdout!r} (stderr={result.stderr!r})"
    )
