"""M16 / M-4 regressions for `tachibana_login_dialog`.

* M16: headless mode with `allow_prod_choice=False` must force
  `is_demo=True` even when the prefill carried `is_demo=False`.
* M-4: an empty stdin payload must abort with a non-zero exit code
  and a `{"status": "cancelled"}` line on stdout, instead of silently
  falling through as `{}`.
"""

from __future__ import annotations

import json
import subprocess
import sys


def _run_dialog_helper(stdin_text: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "engine.exchanges.tachibana_login_dialog", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_headless_forces_is_demo_true_when_prod_choice_disallowed():
    payload = {
        "allow_prod_choice": False,
        "prefill": {"user_id": "u", "password": "p", "is_demo": False},
    }
    proc = _run_dialog_helper(json.dumps(payload) + "\n", "--headless")
    assert proc.returncode == 0, f"helper exit {proc.returncode} stderr={proc.stderr!r}"
    last_line = next(
        (line for line in reversed(proc.stdout.splitlines()) if line.strip()),
        "",
    )
    result = json.loads(last_line)
    assert result["status"] == "ok", result
    # M16: is_demo must be True regardless of prefill request.
    assert result["is_demo"] is True, f"is_demo must be forced to True; got {result}"


def test_headless_honours_is_demo_when_prod_choice_allowed():
    payload = {
        "allow_prod_choice": True,
        "prefill": {"user_id": "u", "password": "p", "is_demo": False},
    }
    proc = _run_dialog_helper(json.dumps(payload) + "\n", "--headless")
    assert proc.returncode == 0
    last_line = next(
        (line for line in reversed(proc.stdout.splitlines()) if line.strip()),
        "",
    )
    result = json.loads(last_line)
    assert result["status"] == "ok"
    # When prod is allowed, the prefill is honoured.
    assert result["is_demo"] is False


def test_empty_stdin_exits_non_zero_with_cancelled_payload():
    """M-4: empty stdin used to silently become `{}` and exit 0."""
    proc = _run_dialog_helper("", "--headless")
    assert proc.returncode != 0, (
        f"empty stdin must exit non-zero; got {proc.returncode} stdout={proc.stdout!r}"
    )
    last_line = next(
        (line for line in reversed(proc.stdout.splitlines()) if line.strip()),
        "",
    )
    result = json.loads(last_line)
    assert result == {"status": "cancelled"}


def test_oserror_on_stdin_exits_non_zero(monkeypatch):
    """M-IO ラウンド 5: `_read_stdin_payload` must guard against
    `OSError` from `sys.stdin.readline()` (e.g. detached console on
    Windows, pty tear-down on Linux). Previously the OSError would
    bubble up as an unhandled exception and exit with a stack trace
    instead of the structured `{"status":"cancelled"}` line, leaving
    the parent helper unable to attribute the failure."""
    from engine.exchanges import tachibana_login_dialog as dialog_mod

    class _FakeStdin:
        def readline(self):
            raise OSError("simulated detached stdin")

    monkeypatch.setattr(dialog_mod.sys, "stdin", _FakeStdin())

    import pytest as _pytest
    with _pytest.raises(SystemExit) as excinfo:
        dialog_mod._read_stdin_payload()
    assert excinfo.value.code == 2
