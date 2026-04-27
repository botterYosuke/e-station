"""MEDIUM-D2-1 — supervisor escalation when `StartupLatch` is violated.

The Tachibana session validation latch (L6) is a one-shot guard: a
second call within the same process is a programmer bug and must
terminate the engine subprocess so the Rust `ProcessManager` can
restart it cleanly.

This test spawns a Python subprocess that drives the **real**
`engine.server.DataEngineServer._do_set_venue_credentials` handler
twice in a row. The first call consumes the latch normally; the second
call hits the latch and triggers the supervisor path inside
`_do_set_venue_credentials` (`sys.stderr.write` + `os._exit(2)`).

If a future change moves the supervisor escalation out of
`_do_set_venue_credentials` or stops re-raising RuntimeError from the
StartupLatch, this test breaks (no tautology — we exercise the
production handler, not a copy).

Asserts:
  (a) the subprocess exited with code 2,
  (b) stderr contains the L6 banner,
  (c) the subprocess never wrote any session token / password / user_id
      string to either stream.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


# MEDIUM-3 (ラウンド 7): use sentinels that cannot collide with the
# operator's `.env` (whose values are treated as production-shape
# `uxNNNNNN` / 8-char password). A collision would make the
# `assert SENTINEL not in output` check pass for the wrong reason
# (the output legitimately contained the value because the dev login
# fast path consumed the env). Unique high-entropy markers eliminate
# that ambiguity.
SECRETS = (
    "TEST_SENTINEL_USER_5e8a1f3c",
    "TEST_SENTINEL_PWD_9b2d7e4a",
    "SESSION_TOKEN_SHOULD_NOT_LEAK",
)


_HELPER_SOURCE = textwrap.dedent(
    """
    import asyncio
    import sys

    import engine.server as server_module
    from engine.server import DataEngineServer

    # Sentinel secret material — must NOT appear in subprocess output.
    user_id = "TEST_SENTINEL_USER_5e8a1f3c"  # noqa: F841
    password = "TEST_SENTINEL_PWD_9b2d7e4a"  # noqa: F841
    session_token = "SESSION_TOKEN_SHOULD_NOT_LEAK"  # noqa: F841

    # Patch validate_session_on_startup at the import site used by
    # `engine.server` so the production handler still runs `latch.run_once`
    # but does NOT hit the network. The first invocation consumes the
    # latch; the second hits the same latch → RuntimeError → the
    # supervisor escalation path inside _do_set_venue_credentials runs.
    async def fake_validate(session, *, latch, p_no_counter, http_client=None):
        async def _inner():
            return True
        return await latch.run_once(_inner())

    server_module.validate_session_on_startup = fake_validate

    server = DataEngineServer(port=0, token="t")

    msg = {
        "request_id": "rid-1",
        "payload": {
            "venue": "tachibana",
            "user_id": user_id,
            "password": password,
            "is_demo": True,
            "session": {
                "url_request": "https://demo/req/" + session_token + "/",
                "url_master": "https://demo/mst/" + session_token + "/",
                "url_price": "https://demo/prc/" + session_token + "/",
                "url_event": "https://demo/evt/" + session_token + "/",
                "url_event_ws": "wss://demo/evt/" + session_token + "/",
                "expires_at_ms": None,
                "zyoutoeki_kazei_c": "1",
            },
        },
    }

    async def main():
        # First call: validate succeeds, latch consumed.
        await server._do_set_venue_credentials(msg)
        # Second call: same latch → RuntimeError → supervisor exits the
        # process before this `await` returns. If we DO return here, the
        # supervisor escalation regressed and the test must fail.
        await server._do_set_venue_credentials(msg)
        sys.stderr.write("UNREACHABLE: second call returned without exit\\n")
        sys.exit(3)

    asyncio.run(main())
    """
)


def test_runtime_error_from_validate_terminates_process_with_log():
    proc = subprocess.run(
        [sys.executable, "-c", _HELPER_SOURCE],
        capture_output=True,
        text=True,
        # errors="replace" prevents UnicodeDecodeError in _readerthread when
        # the child writes UTF-8 (e.g. PYTHONUTF8=1 set by VS Code) but the
        # parent decodes with the Windows system codepage (cp932). Without
        # this, the reader thread crashes and proc.stderr is left as None.
        # All checked strings (L6 banner, SECRETS) are pure ASCII, so
        # replacement of non-ASCII bytes with '?' does not affect correctness.
        errors="replace",
        timeout=30,
    )

    # (a) exit code 2 — pinned to the supervisor's `os._exit(2)` value
    # so future regressions to a generic `sys.exit(1)` are caught.
    assert proc.returncode == 2, (
        f"supervisor must exit with code 2; got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )

    # (b) stderr carries the L6 banner from `_do_set_venue_credentials`.
    assert "StartupLatch invariant violated (L6)" in proc.stderr, (
        f"stderr missing L6 banner: {proc.stderr!r}"
    )
    # The "UNREACHABLE" line proves the production handler did NOT
    # silently swallow the RuntimeError and continue.
    assert "UNREACHABLE" not in proc.stderr, (
        f"second call returned without exit: {proc.stderr!r}"
    )

    # (c) zero secret leakage — no creds in either stream.
    combined = proc.stdout + proc.stderr
    for secret in SECRETS:
        assert secret not in combined, (
            f"FATAL: subprocess output leaked secret {secret!r}: {combined!r}"
        )
