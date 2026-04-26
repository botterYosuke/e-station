"""F-M2c tkinter smoke tests.

These tests verify that the tkinter login dialog helper can be invoked as a
subprocess and returns the expected JSON without requiring a real display.

The `--auto-cancel` flag (T7) is exercised here: it must immediately write
`{"status":"cancelled"}` to stdout and exit 0, with no stdin read and no GUI
initialisation.

CI command:
    xvfb-run uv run pytest python/tests/ -m tk_smoke -v
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_dialog(*args: str, stdin: str = "") -> tuple[int, dict]:
    """Spawn the login dialog helper and return (exit_code, parsed_stdout)."""
    result = subprocess.run(
        [sys.executable, "-m", "engine.exchanges.tachibana_login_dialog", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
    )
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        pytest.fail(
            f"Dialog stdout is not valid JSON.\n"
            f"  stdout: {result.stdout!r}\n"
            f"  stderr: {result.stderr!r}\n"
            f"  returncode: {result.returncode}"
        )
    return result.returncode, payload


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.tk_smoke
def test_auto_cancel_returns_cancelled_without_stdin() -> None:
    """--auto-cancel must emit {"status":"cancelled"} and exit 0, no stdin."""
    rc, payload = _run_dialog("--auto-cancel")
    assert rc == 0, f"expected exit 0, got {rc} (payload={payload})"
    assert payload.get("status") == "cancelled", (
        f"expected {{status: cancelled}}, got {payload}"
    )


@pytest.mark.tk_smoke
def test_auto_cancel_ignores_stdin() -> None:
    """--auto-cancel must cancel even when valid JSON is piped on stdin."""
    valid_payload = json.dumps(
        {"prefill": {"user_id": "u", "password": "p"}, "allow_prod_choice": False}
    )
    rc, payload = _run_dialog("--auto-cancel", stdin=valid_payload)
    assert rc == 0, f"expected exit 0, got {rc}"
    assert payload.get("status") == "cancelled"


@pytest.mark.tk_smoke
def test_headless_valid_input_returns_ok() -> None:
    """--headless with valid credentials must return {"status":"ok"}.

    Design note: in headless mode the dialog subprocess always exits with
    code 0 regardless of input validity — the exit code is not meaningful
    here.  Pass/fail is determined solely by the ``status`` field in the
    JSON output.  This avoids conflating GUI-level errors (non-zero exit)
    with application-level outcomes.
    """
    valid_payload = json.dumps(
        {
            "prefill": {"user_id": "testuser", "password": "testpass"},
            "allow_prod_choice": False,
        }
    )
    rc, payload = _run_dialog("--headless", stdin=valid_payload)
    assert rc == 0, f"expected exit 0, got {rc} (payload={payload})"
    assert payload.get("status") == "ok"
    assert payload.get("is_demo") is True, "allow_prod_choice=False must force is_demo=True"


@pytest.mark.tk_smoke
def test_headless_empty_user_id_cancels() -> None:
    """--headless with empty user_id must return {"status":"cancelled"}."""
    payload = json.dumps(
        {"prefill": {"user_id": "", "password": "p"}, "allow_prod_choice": False}
    )
    rc, result = _run_dialog("--headless", stdin=payload)
    assert rc == 0
    assert result.get("status") == "cancelled"
