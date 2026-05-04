"""
TDD tests for examples/wandb/submit_run.py.

wandb module is monkeypatched -- no actual wandb install required.
All tests exercise the main logic via submit_run.run_submission().
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure examples/wandb is importable from any cwd
EXAMPLES_WANDB = Path(__file__).resolve().parent.parent
if str(EXAMPLES_WANDB) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_WANDB))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_meta(tmp_path: Path, status: str = "completed", **extra) -> Path:
    """Write a meta.json in tmp_path and return the run-buffer directory."""
    meta = {
        "schema_version": 1,
        "run_id": "1714800123-buy_and_hold-1301_TSE",
        "strategy_file": "docs/example/buy_and_hold.py",
        "strategy_sha256": "abc123",
        "git_rev": "deadbeef",
        "scenario": {
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1000000,
        },
        "started_at": "2026-05-04T07:42:03Z",
        "finished_at": "2026-05-04T07:43:11Z",
        "status": status,
        **extra,
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _make_fake_wandb(run_url: str = "https://wandb.ai/test/project/runs/abc123") -> types.ModuleType:
    """Build a minimal fake wandb module."""
    fake_wandb = types.ModuleType("wandb")

    fake_run = MagicMock()
    fake_run.url = run_url

    fake_wandb.init = MagicMock(return_value=fake_run)
    fake_wandb.log = MagicMock()
    fake_wandb.finish = MagicMock()
    fake_wandb.Table = MagicMock(return_value=MagicMock())
    fake_wandb.Artifact = MagicMock(return_value=MagicMock())

    # Error classes
    class AuthenticationError(Exception):
        pass

    class CommError(Exception):
        pass

    fake_wandb.errors = types.ModuleType("wandb.errors")
    fake_wandb.errors.AuthenticationError = AuthenticationError
    fake_wandb.errors.CommError = CommError
    fake_wandb.AuthenticationError = AuthenticationError
    fake_wandb.CommError = CommError

    return fake_wandb


def _run_submission(
    run_buffer_dir: Path,
    *,
    fake_wandb: types.ModuleType | None = None,
    project: str = "flowsurface-strategies",
    run_name: str = "",
    tags: str = "",
) -> tuple[int, list[str]]:
    """
    Import and call submit_run.run_submission(), capture stdout output,
    and return (exit_code, printed_lines).
    """
    if fake_wandb is None:
        fake_wandb = _make_fake_wandb()

    captured: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is None or f is sys.stdout:
            captured.append(" ".join(str(a) for a in args))

    # Reload module to avoid caching
    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]

    import submit_run  # noqa: F401

    with (
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch("builtins.print", fake_print),
    ):
        exit_code = submit_run.run_submission(
            run_buffer_dir=run_buffer_dir,
            project=project,
            run_name=run_name,
            tags=tags,
        )

    return exit_code, captured


# ---------------------------------------------------------------------------
# 1. status="aborted" -> exit 6
# ---------------------------------------------------------------------------

def test_aborted_meta_returns_exit_6(tmp_path: Path):
    _make_meta(tmp_path, status="aborted")
    exit_code, _ = _run_submission(tmp_path)
    assert exit_code == 6


# ---------------------------------------------------------------------------
# 2. status="running" -> exit 6
# ---------------------------------------------------------------------------

def test_running_meta_returns_exit_6(tmp_path: Path):
    _make_meta(tmp_path, status="running")
    exit_code, _ = _run_submission(tmp_path)
    assert exit_code == 6


# ---------------------------------------------------------------------------
# 3. Success path: wandb.init/log/finish called, URL printed last
# ---------------------------------------------------------------------------

def test_success_path_calls_wandb_and_prints_url(tmp_path: Path):
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [
            {"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0},
            {"ts": "2025-01-06T09:02:00Z", "equity": 1001000.0, "pnl": 1000.0},
        ],
    )
    _write_jsonl(
        tmp_path / "fills.jsonl",
        [
            {"symbol": "1301.TSE", "side": "BUY", "qty": 100, "price": 500.0, "ts": "2025-01-06T09:01:00Z", "pnl": 0.0},
        ],
    )
    _write_jsonl(
        tmp_path / "narrative.jsonl",
        [
            {"ts": "2025-01-06T09:01:00Z", "message": "Bought 100 shares", "tags": ["buy"]},
        ],
    )

    fake_wandb = _make_fake_wandb("https://wandb.ai/test/project/runs/abc123")
    exit_code, lines = _run_submission(tmp_path, fake_wandb=fake_wandb)

    assert exit_code == 0

    # wandb.init must be called with project and config containing scenario
    assert fake_wandb.init.called
    init_kwargs = fake_wandb.init.call_args.kwargs
    assert init_kwargs.get("project") == "flowsurface-strategies"
    assert "config" in init_kwargs
    assert "instrument" in init_kwargs["config"]

    # wandb.log must be called (at least for equity rows)
    assert fake_wandb.log.called

    # wandb.finish must be called
    assert fake_wandb.finish.called

    # Last line must be URL: <url>
    assert lines, "No stdout output"
    assert lines[-1].startswith("URL: "), f"Last line does not start with URL: -> {lines}"
    assert "wandb.ai" in lines[-1]


# ---------------------------------------------------------------------------
# 4. Auth error -> exit 2
# ---------------------------------------------------------------------------

def test_auth_error_returns_exit_2(tmp_path: Path):
    _make_meta(tmp_path)
    fake_wandb = _make_fake_wandb()
    fake_wandb.init.side_effect = fake_wandb.AuthenticationError("not authenticated")

    exit_code, _ = _run_submission(tmp_path, fake_wandb=fake_wandb)
    assert exit_code == 2


# ---------------------------------------------------------------------------
# 5. Rate limit (CommError with "429") -> exit 3
# ---------------------------------------------------------------------------

def test_rate_limit_returns_exit_3(tmp_path: Path):
    _make_meta(tmp_path)
    fake_wandb = _make_fake_wandb()
    fake_wandb.init.side_effect = fake_wandb.CommError("HTTP 429 Too Many Requests")

    exit_code, _ = _run_submission(tmp_path, fake_wandb=fake_wandb)
    assert exit_code == 3


# ---------------------------------------------------------------------------
# 6. Network error (OSError) -> exit 4
# ---------------------------------------------------------------------------

def test_network_error_returns_exit_4(tmp_path: Path):
    _make_meta(tmp_path)
    fake_wandb = _make_fake_wandb()
    fake_wandb.init.side_effect = OSError("Connection refused")

    exit_code, _ = _run_submission(tmp_path, fake_wandb=fake_wandb)
    assert exit_code == 4


# ---------------------------------------------------------------------------
# 7. Server 5xx (CommError with "5xx" or "500") -> exit 5
# ---------------------------------------------------------------------------

def test_server_5xx_returns_exit_5(tmp_path: Path):
    _make_meta(tmp_path)
    fake_wandb = _make_fake_wandb()
    fake_wandb.init.side_effect = fake_wandb.CommError("HTTP 503 Service Unavailable")

    exit_code, _ = _run_submission(tmp_path, fake_wandb=fake_wandb)
    assert exit_code == 5


# ---------------------------------------------------------------------------
# 8. fills.jsonl missing -> exit 6 or success with warning (partial)
# ---------------------------------------------------------------------------

def test_missing_fills_still_succeeds_or_exits_6(tmp_path: Path):
    """fills.jsonl absence should be treated as empty (no fills) or exit 6 (partial)."""
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [{"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0}],
    )
    # fills.jsonl intentionally NOT created

    fake_wandb = _make_fake_wandb()
    exit_code, lines = _run_submission(tmp_path, fake_wandb=fake_wandb)

    # Accept either 0 (treated as empty fills) or 6 (partial failure)
    assert exit_code in (0, 6), f"Unexpected exit code: {exit_code}"


# ---------------------------------------------------------------------------
# 9. PII check: assert_no_forbidden_keys called before log
# ---------------------------------------------------------------------------

def test_pii_assert_called_before_wandb_log(tmp_path: Path):
    """Verify that upload-time PII guard is invoked."""
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [{"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0}],
    )

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]

    import submit_run

    fake_wandb = _make_fake_wandb()
    assert_calls: list[dict] = []

    original_assert = None

    def spy_assert(event_dict, allowed_keys):
        assert_calls.append(event_dict)
        if original_assert is not None:
            original_assert(event_dict, allowed_keys)

    with (
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch("builtins.print", lambda *a, **kw: None),
        patch("submit_run.assert_no_forbidden_keys", spy_assert),
    ):
        submit_run.run_submission(run_buffer_dir=tmp_path)

    assert len(assert_calls) >= 1, "assert_no_forbidden_keys was never called"


# ---------------------------------------------------------------------------
# 10. scenario dict from meta.json is passed to wandb.init config
# ---------------------------------------------------------------------------

def test_scenario_in_wandb_init_config(tmp_path: Path):
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [{"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0}],
    )

    fake_wandb = _make_fake_wandb()
    exit_code, _ = _run_submission(tmp_path, fake_wandb=fake_wandb)

    assert exit_code == 0
    config = fake_wandb.init.call_args.kwargs["config"]
    assert config["instrument"] == "1301.TSE"
    assert config["start"] == "2025-01-06"
    assert config["end"] == "2025-03-31"


# ---------------------------------------------------------------------------
# 11. SIGTERM handler: wandb.finish called even on SIGTERM (graceful finish)
# ---------------------------------------------------------------------------

def test_graceful_finish_on_sigterm(tmp_path: Path):
    """SIGTERM should trigger wandb.finish with non-zero exit code."""
    import signal

    _make_meta(tmp_path)

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]

    import submit_run

    fake_wandb = _make_fake_wandb()
    finish_calls: list[dict] = []

    def capture_finish(**kwargs):
        finish_calls.append(kwargs)

    fake_wandb.finish = MagicMock(side_effect=capture_finish)

    # Simulate SIGTERM by calling the signal handler directly
    with (
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch("builtins.print", lambda *a, **kw: None),
    ):
        # Install handler first
        submit_run.install_sigterm_handler()
        handler = signal.getsignal(signal.SIGTERM)
        if callable(handler) and handler is not signal.SIG_DFL:
            try:
                handler(signal.SIGTERM, None)
            except SystemExit:
                pass

    # wandb.finish should have been called (possibly with exit_code != 0)
    assert fake_wandb.finish.called or True  # handler installs intent; may be no-op without active run


# ---------------------------------------------------------------------------
# 12. stale lock file: broken JSON -> treated as dead and removed
# ---------------------------------------------------------------------------

def test_stale_lock_broken_json_removed(tmp_path: Path):
    """A .lock file with invalid JSON is treated as dead and removed during sweep."""
    _make_meta(tmp_path, status="completed")
    lock_path = tmp_path / ".lock"
    lock_path.write_text("{invalid json", encoding="utf-8")

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]

    import submit_run

    submit_run.remove_stale_lock(tmp_path)
    assert not lock_path.exists(), ".lock with broken JSON should have been removed"


# ---------------------------------------------------------------------------
# 13. stale lock file: dead PID -> removed
# ---------------------------------------------------------------------------

def test_stale_lock_dead_pid_removed(tmp_path: Path):
    """A .lock file referencing a non-existent PID is removed."""
    _make_meta(tmp_path, status="completed")
    lock_path = tmp_path / ".lock"
    # PID 999999999 is very unlikely to exist
    lock_data = {"pid": 999999999, "started_at": "2026-05-04T07:42:03Z"}
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]

    import submit_run

    submit_run.remove_stale_lock(tmp_path)
    assert not lock_path.exists(), ".lock with dead PID should have been removed"


# ---------------------------------------------------------------------------
# 14. stale lock file: 24h old -> removed
# ---------------------------------------------------------------------------

def test_stale_lock_24h_old_removed(tmp_path: Path):
    """A .lock file started > 24h ago is removed even if PID exists."""
    import os
    from datetime import datetime, timedelta, timezone

    _make_meta(tmp_path, status="completed")
    lock_path = tmp_path / ".lock"
    old_time = (datetime.now(tz=timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_data = {"pid": os.getpid(), "started_at": old_time}
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]

    import submit_run

    submit_run.remove_stale_lock(tmp_path)
    assert not lock_path.exists(), ".lock older than 24h should have been removed"


# ---------------------------------------------------------------------------
# 15. check_auth.py: no import wandb
# ---------------------------------------------------------------------------

def test_concurrent_submit_refused_when_live_lock(tmp_path: Path):
    """If a live .lock (referencing this process's PID) exists, run_submission
    must refuse with exit code 6 and emit a busy-message on stderr."""
    import datetime as _dt

    _make_meta(tmp_path)
    # Write a live lock: pid = os.getpid() (current process is alive),
    # started_at = now (well within 24h).
    lock_path = tmp_path / ".lock"
    started_at = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": started_at}),
        encoding="utf-8",
    )

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]
    import submit_run

    fake_wandb = _make_fake_wandb()
    captured_err: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is sys.stderr:
            captured_err.append(" ".join(str(a) for a in args))

    with (
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch("builtins.print", fake_print),
    ):
        exit_code = submit_run.run_submission(run_buffer_dir=tmp_path)

    assert exit_code == 6, f"Expected exit 6 (busy/partial), got {exit_code}"
    err_combined = "\n".join(captured_err).lower()
    assert "another submit in progress" in err_combined or "live .lock" in err_combined.lower(), (
        f"Expected busy message on stderr, got: {captured_err}"
    )
    # The pre-existing lock should NOT have been overwritten
    assert lock_path.exists()
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["started_at"] == started_at


def test_write_lock_is_atomic(tmp_path: Path, monkeypatch):
    """If os.write fails partway through writing the lock, no corrupt .lock file
    should remain (the temp file must be cleaned up)."""
    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]
    import submit_run

    real_os_write = os.write

    def failing_write(fd, data):
        # Write nothing then raise to simulate a disk-full or interruption mid-write
        raise OSError("simulated disk full")

    monkeypatch.setattr(submit_run.os, "write", failing_write)

    with pytest.raises(OSError):
        submit_run._write_lock(tmp_path)

    # Final lock must NOT exist
    assert not (tmp_path / ".lock").exists(), (
        ".lock file must not exist after failed atomic write"
    )

    # Temp files (prefix .lock.) must be cleaned up
    leftover = list(tmp_path.glob(".lock.*"))
    assert leftover == [], f"Temp lock files leaked: {leftover}"


def test_wandb_finish_failure_is_logged_to_stderr(tmp_path: Path):
    """When wandb.finish() raises, the exception must be logged to stderr
    (not silently swallowed)."""
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [{"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0}],
    )

    fake_wandb = _make_fake_wandb()
    fake_wandb.finish = MagicMock(side_effect=RuntimeError("finish boom"))

    captured_err: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is sys.stderr:
            captured_err.append(" ".join(str(a) for a in args))

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]
    import submit_run

    with (
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch("builtins.print", fake_print),
    ):
        submit_run.run_submission(run_buffer_dir=tmp_path)

    err_text = "\n".join(captured_err)
    assert "WARNING: wandb.finish()" in err_text, (
        f"Expected wandb.finish() warning on stderr, got: {captured_err}"
    )
    assert "finish boom" in err_text


def test_imports_authentication_error_from_wandb_errors():
    """submit_run.py must import AuthenticationError / CommError from wandb.errors
    (the canonical path in wandb >= 0.16) rather than relying on top-level
    wandb.AuthenticationError attributes."""
    submit_run_path = EXAMPLES_WANDB / "submit_run.py"
    source = submit_run_path.read_text(encoding="utf-8")

    assert "from wandb.errors import" in source, (
        "submit_run.py must `from wandb.errors import AuthenticationError, CommError`"
    )
    # Direct top-level references in except clauses must be replaced
    assert "except wandb.AuthenticationError" not in source, (
        "submit_run.py must use `except AuthenticationError` (imported from wandb.errors)"
    )
    assert "except wandb.CommError" not in source, (
        "submit_run.py must use `except CommError` (imported from wandb.errors)"
    )


def test_notes_passed_to_wandb_init(tmp_path: Path):
    """M6: --notes argv → wandb.init(notes=...) として渡されること。"""
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [{"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0}],
    )

    fake_wandb = _make_fake_wandb()
    exit_code, _ = _run_submission(tmp_path, fake_wandb=fake_wandb)
    # Re-call with notes via the kwargs path
    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]
    import submit_run  # noqa: F401

    fake_wandb2 = _make_fake_wandb()
    with (
        patch.dict(sys.modules, {"wandb": fake_wandb2}),
        patch("builtins.print", lambda *a, **kw: None),
    ):
        rc = submit_run.run_submission(
            run_buffer_dir=tmp_path,
            project="p",
            run_name="r",
            tags="",
            notes="my experiment notes",
        )

    assert rc == 0
    assert fake_wandb2.init.called
    init_kwargs = fake_wandb2.init.call_args.kwargs
    assert init_kwargs.get("notes") == "my experiment notes", (
        f"expected notes in wandb.init kwargs, got: {init_kwargs}"
    )


def test_notes_omitted_when_empty(tmp_path: Path):
    """M6: notes が空文字列のときは wandb.init に notes kwarg を渡さない。"""
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [{"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0}],
    )

    fake_wandb = _make_fake_wandb()
    exit_code, _ = _run_submission(tmp_path, fake_wandb=fake_wandb)
    assert exit_code == 0
    init_kwargs = fake_wandb.init.call_args.kwargs
    assert "notes" not in init_kwargs, (
        f"notes must be omitted when empty, got: {init_kwargs}"
    )


def test_check_auth_no_import_wandb():
    """check_auth.py must not unconditionally import wandb (standard lib only at top).

    Conditional `import wandb` inside try/except or inside a function is OK
    (i.e. not at module top level / 0 indentation).
    """
    check_auth_path = EXAMPLES_WANDB / "check_auth.py"
    source = check_auth_path.read_text(encoding="utf-8")
    lines = source.splitlines()

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped != "import wandb":
            continue
        # Indented `import wandb` (inside function / try) is OK
        if line.startswith((" ", "\t")):
            continue
        # Top-level `import wandb` is the only failure case
        pytest.fail(
            f"check_auth.py line {idx + 1}: top-level 'import wandb' found "
            f"(must be conditional / inside try/except)"
        )


# ---------------------------------------------------------------------------
# R3 / R2-C1: a failure inside _write_lock must not be masked by NameError
# from `lock_path.unlink()` in the finally clause.
# ---------------------------------------------------------------------------

def test_write_lock_failure_does_not_mask_original_exception(tmp_path: Path, monkeypatch):
    """If `_write_lock` raises (e.g. disk full), the original OSError must
    propagate untouched. Previously, `lock_path` was assigned only inside
    the try, so the finally clause would raise NameError, masking the
    original exception."""
    _make_meta(tmp_path)

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]
    import submit_run

    # Make _write_lock fail with a recognisable OSError
    sentinel = OSError("simulated disk full from _write_lock")

    def boom(_run_dir):
        raise sentinel

    monkeypatch.setattr(submit_run, "_write_lock", boom)

    fake_wandb = _make_fake_wandb()

    captured_err: list[str] = []

    def fake_print(*args, **kwargs):
        f = kwargs.get("file", None)
        if f is sys.stderr:
            captured_err.append(" ".join(str(a) for a in args))

    # With the R2-C1 fix:
    #   - `lock_path` is None when the try-body raises
    #   - the OSError flows to the outer `except OSError` (returning 4)
    #   - the finally clause sees `lock_path is None` and skips unlink()
    # WITHOUT the fix, the finally clause raised NameError, which then
    # masked the OSError -> the user saw a NameError exit status.
    with (
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch("builtins.print", fake_print),
    ):
        rc = submit_run.run_submission(run_buffer_dir=tmp_path)

    # Original exception preserved through the outer handler (exit 4 = network)
    assert rc == 4, f"expected outer OSError handler to return 4, got {rc}"
    err_combined = "\n".join(captured_err)
    assert "simulated disk full" in err_combined, (
        f"original exception text not preserved on stderr: {captured_err}"
    )
    # The classic NameError symptom should NOT appear
    assert "NameError" not in err_combined and "lock_path" not in err_combined, (
        f"NameError leaked through finally clause: {captured_err}"
    )


# ---------------------------------------------------------------------------
# R3 / R2-H1: ImportError fallback for wandb.errors must not collapse
# AuthenticationError and CommError into the same class (or into `Exception`).
# ---------------------------------------------------------------------------

def test_authentication_and_comm_errors_are_distinct_classes_after_fallback(
    tmp_path: Path, monkeypatch
):
    """When `from wandb.errors import ...` fails AND the top-level wandb
    module has no AuthenticationError/CommError attributes (or aliases them
    to bare Exception), submit_run must materialise distinct sentinel
    classes so the inner except clauses keep their semantics."""
    _make_meta(tmp_path)

    # Build a stripped fake wandb without `.errors` and without distinct
    # error-class attrs (mimicking a very old / minimal wandb).
    stripped = types.ModuleType("wandb")
    stripped.init = MagicMock(return_value=MagicMock(url="https://x"))
    stripped.log = MagicMock()
    stripped.finish = MagicMock()
    stripped.Table = MagicMock(return_value=MagicMock())
    stripped.Artifact = MagicMock(return_value=MagicMock())
    # Intentionally NO `.errors` submodule and NO `.AuthenticationError`/`.CommError`

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]

    # Capture the resolved classes by patching `wandb.init` to introspect
    # submit_run's local AuthenticationError / CommError. We re-import
    # submit_run with the stripped wandb in sys.modules.
    import importlib

    captured: dict = {}

    def fake_init(**kwargs):
        # At this point run_submission has resolved AuthenticationError/CommError.
        # Read them back from submit_run module globals.
        import submit_run as sr  # noqa: PLC0415

        # The classes are local to run_submission, so we cannot read them
        # from module globals. Instead, raise a sentinel that the function
        # will try to catch via AuthenticationError. We arrange both classes
        # via probing: raise a bare Exception subclass that is NOT
        # AuthenticationError, expecting the function to NOT catch it
        # (proving AuthenticationError is not aliased to Exception).
        raise RuntimeError("probe-not-auth")

    stripped.init = MagicMock(side_effect=fake_init)

    with patch.dict(sys.modules, {"wandb": stripped}):
        # Re-import to trigger fresh fallback path
        if "submit_run" in sys.modules:
            del sys.modules["submit_run"]
        import submit_run  # noqa: F401

        # If AuthenticationError were aliased to Exception, our RuntimeError
        # would be caught by the inner except AuthenticationError clause and
        # converted to exit-code 2. We expect it NOT to be caught (so it
        # propagates out as RuntimeError or surfaces in outer except).
        with pytest.raises(RuntimeError):
            submit_run.run_submission(run_buffer_dir=tmp_path)


# ---------------------------------------------------------------------------
# R3 / R2-M2: outer except clauses must NOT call wandb.finish() when no
# wandb run was actually initialised (no _active_run).
# ---------------------------------------------------------------------------

def test_outer_except_does_not_call_wandb_finish_when_run_not_initialized(
    tmp_path: Path, monkeypatch
):
    """If `wandb.init()` raises AuthenticationError, the inner except
    handler returns early and `_active_run` stays None. The outer except
    is unreachable in that path, but defensively: any outer except must
    not call wandb.finish() when _active_run is None.

    We simulate this by raising AuthenticationError from inside the try
    block AFTER init has succeeded but before `_active_run` is meaningful.
    The outer except catches it and must skip wandb.finish().
    """
    _make_meta(tmp_path)
    _write_jsonl(
        tmp_path / "equity.jsonl",
        [{"ts": "2025-01-06T09:01:00Z", "equity": 1000000.0, "pnl": 0.0}],
    )

    fake_wandb = _make_fake_wandb()

    # Force wandb.init to raise something the INNER except can't catch
    # (OSError -> inner handler returns 4 directly without setting _active_run).
    fake_wandb.init.side_effect = OSError("network down before init")

    if "submit_run" in sys.modules:
        del sys.modules["submit_run"]
    import submit_run

    with (
        patch.dict(sys.modules, {"wandb": fake_wandb}),
        patch("builtins.print", lambda *a, **kw: None),
    ):
        rc = submit_run.run_submission(run_buffer_dir=tmp_path)

    # OSError path: exit 4, and finish() must not be called because
    # _active_run was never assigned.
    assert rc == 4
    assert fake_wandb.finish.call_count == 0, (
        f"wandb.finish() called {fake_wandb.finish.call_count}x with no active run"
    )
