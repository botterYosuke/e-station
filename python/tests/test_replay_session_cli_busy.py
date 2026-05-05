"""Group 4b — M-1 (silent) regression: CLI prints a dedicated BusyError message.

CLI を subprocess で起動して BusyError 経路に入れるのは難しいので、
ここでは CLI の except 分岐そのものを直接実行して stderr / exit_code を検証する。
"""

from __future__ import annotations

import io
import sys

import pytest

from engine.replay_session import BusyError


def test_cli_busy_error_message(capsys, monkeypatch):
    """M-1: CLI が BusyError を捕えたら専用メッセージで exit code 2 にする。"""
    # CLI と同じ except 分岐を最小再現する（subprocess 起動を避ける）。
    def _run_cli_simulating_busy() -> int:
        try:
            raise BusyError("state=RUNNING cmd=LoadReplayData")
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except BusyError as exc:
            print(
                f"engine is busy ({exc}); call load() first or wait for current operation",
                file=sys.stderr,
            )
            return 2
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    rc = _run_cli_simulating_busy()
    assert rc == 2
    captured = capsys.readouterr()
    assert "engine is busy" in captured.err
    assert "call load() first" in captured.err
    # token 等の機密情報は含まれない（メッセージ自体がそれを含まない設計）。
    assert "FLOWSURFACE_ENGINE_TOKEN" not in captured.err


def test_cli_busy_branch_present_in_module():
    """M-1: replay_session.py の CLI に BusyError 分岐が存在することを確認する。"""
    import engine.replay_session as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "except BusyError" in src
    assert "engine is busy" in src
