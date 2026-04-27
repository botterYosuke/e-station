"""M2 / M-5 / M-17 — `python/engine/__main__.py` must let CLI / env-var
boot paths opt into the dev Tachibana login fast path via the
`FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED` env var. Without this, a
developer running `uv run python -m engine --port N --token T` could
not exercise the env fast path even on a debug build.

The stdin boot path remains Rust-controlled (release builds always
write `dev_tachibana_login_allowed=false`); the env var only affects
boot paths where stdin is absent.
"""

from __future__ import annotations

from engine.__main__ import _env_dev_login_allowed


def test_env_dev_login_allowed_default_false(monkeypatch):
    monkeypatch.delenv("FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED", raising=False)
    assert _env_dev_login_allowed() is False


def test_env_dev_login_allowed_truthy(monkeypatch):
    for v in ("1", "true", "TRUE", "Yes", "on"):
        monkeypatch.setenv("FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED", v)
        assert _env_dev_login_allowed() is True, f"truthy value {v!r} must enable flag"


def test_env_dev_login_allowed_falsy(monkeypatch):
    for v in ("", "0", "false", "no", "off", "anything-else"):
        monkeypatch.setenv("FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED", v)
        assert _env_dev_login_allowed() is False, f"non-truthy value {v!r} must NOT enable flag"


def test_parse_stdin_config_warns_and_falls_back_when_dev_flag_is_not_bool(
    monkeypatch, caplog
):
    """M-CFG ラウンド 5: the stdin payload is Rust-controlled but
    forward-compat means a future bug or a third-party launcher might
    write `"false"` (string) instead of `false` (bool) for the
    `dev_tachibana_login_allowed` field. The Python side must NOT
    treat a truthy string as True via `bool("false") == True`. Pin:
    a string value emits a warning and falls back to False."""
    import io
    import logging
    from engine import __main__ as engine_main

    # Stub stdin with a payload carrying the wrong type.
    raw = '{"port": 19876, "token": "tok", "dev_tachibana_login_allowed": "false"}\n'
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))

    cfg = engine_main._parse_stdin_config()
    # The raw value survives parse (it's just JSON), but the bool
    # coercion path must reject non-bool and warn.
    with caplog.at_level(logging.WARNING):
        flag = engine_main._coerce_dev_login_allowed(
            cfg.get("dev_tachibana_login_allowed", False)
        )
    assert flag is False, "non-bool dev_tachibana_login_allowed must fall back to False"
    assert any(
        "non-bool" in rec.getMessage().lower()
        for rec in caplog.records
    ), f"expected a warning about non-bool, got {[r.getMessage() for r in caplog.records]}"


def test_coerce_dev_login_allowed_passes_through_real_bools():
    from engine import __main__ as engine_main

    assert engine_main._coerce_dev_login_allowed(True) is True
    assert engine_main._coerce_dev_login_allowed(False) is False


def test_parse_stdin_config_exits_with_fatal_on_invalid_json(monkeypatch, capsys):
    """MEDIUM-7 (ラウンド 6): a malformed stdin payload must not crash
    with an opaque JSONDecodeError traceback. Surface a FATAL line on
    stderr and `sys.exit(2)` so the Rust supervisor's restart loop sees
    a clean signal."""
    import io
    from engine import __main__ as engine_main

    monkeypatch.setattr("sys.stdin", io.StringIO("{not valid json\n"))
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        engine_main._parse_stdin_config()
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "FATAL" in captured.err
    assert "invalid stdin payload" in captured.err
    # Defence-in-depth: the original raw text must not be echoed (the
    # malformed payload could carry a token).
    assert "{not valid json" not in captured.err


def test_token_cli_emits_deprecation_warning(monkeypatch, caplog):
    """HIGH-6 (ラウンド 6): the `--token=<value>` CLI is hidden from
    `--help` (argparse.SUPPRESS) but still accepted for dev convenience.
    When used, a one-shot deprecation warning must reach the log so
    operators are nudged toward the stdin-payload / env-var path that
    doesn't expose secrets to process listings.
    """
    import logging
    from unittest.mock import patch, MagicMock
    from engine import __main__ as engine_main

    # Patch sys.argv so `_parse_args()` picks up our CLI.
    monkeypatch.setattr("sys.argv", ["engine", "--port", "12345", "--token", "secret"])
    # MEDIUM-2 (ラウンド 7): also patch `_run` itself so the coroutine
    # produced by `_run(...)` inside `main()` does not leak as an
    # unawaited coroutine RuntimeWarning. The previous version only
    # patched `asyncio.run` to a no-op, which left the `_run(...)`
    # coroutine (created by the unpatched coroutine function) garbage-
    # collected without being awaited.
    def _fake_run(*args, **kwargs):
        return None  # not a coroutine — main()'s asyncio.run is also mocked
    fake_run = MagicMock(side_effect=_fake_run)  # plain Mock, not AsyncMock
    with patch.object(engine_main, "asyncio") as mock_asyncio, patch.object(
        engine_main, "_run", fake_run
    ), caplog.at_level(logging.WARNING):
        # Keep `asyncio.run` as a no-op so main() returns without spawning anything.
        mock_asyncio.run.return_value = None
        engine_main.main()
    messages = [r.getMessage() for r in caplog.records]
    assert any("--token CLI" in m and "deprecated" in m for m in messages), (
        f"expected deprecation warning for --token CLI, got {messages}"
    )
