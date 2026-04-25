"""H1 / H3 / M-14 regression — unexpected exceptions inside the
Tachibana login dispatchers must surface as typed `VenueError` events
with the fixed Japanese banner. Earlier code shapes either swallowed
them silently (`except (KeyError, TypeError): pass`) or let them
bubble up to the generic `Error` event (which the UI banner cannot
classify under the venue contract).

The tests use `RuntimeError("forced")` as a stand-in for any unexpected
inner failure (helper subprocess crash, asyncio cancel translation,
bug in tachibana_run_login). They also assert that the secret-bearing
fields the test supplies (user_id "u", password "p") never appear in
the emitted VenueError — the banner stays generic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from engine.server import DataEngineServer


@pytest.mark.asyncio
async def test_request_venue_login_emits_venue_error_when_run_login_raises():
    """H1: `_do_request_venue_login` must convert unexpected exceptions
    from `tachibana_run_login` into `VenueError{code:'login_failed'}`."""
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=True)

    async def fake_run_login(**_kwargs):
        raise RuntimeError("forced")

    with patch("engine.server.tachibana_run_login", fake_run_login):
        await server._do_request_venue_login(
            {"request_id": "rid-1", "venue": "tachibana"}
        )

    events = []
    while server._outbox:
        events.append(server._outbox.popleft())

    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1, f"expected exactly one VenueError, got {events}"
    assert venue_errors[0]["code"] == "login_failed"
    assert venue_errors[0]["request_id"] == "rid-1"
    # Banner stays generic — no leakage of inner exception text.
    assert "forced" not in venue_errors[0]["message"]


_UNIQUE_USER_ID = "user-id-UNIQUE-67890"
_UNIQUE_PASSWORD = "secret-password-UNIQUE-12345"


@pytest.mark.asyncio
async def test_set_venue_credentials_emits_venue_error_when_run_login_raises(caplog):
    """H3 / M-14: `_do_set_venue_credentials` must convert unexpected
    exceptions from `tachibana_run_login` into `VenueError{code:
    'login_failed'}`. Also asserts the user-supplied creds (unique
    sentinels) never leak into the event **or** the `log.exception`
    record's traceback (M-LOG ラウンド 5: stack-frame locals can carry
    `fallback_password` through `exc_text` when an exception fires
    inside the same scope that bound it)."""
    import logging
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=True)

    async def fake_run_login(**_kwargs):
        raise RuntimeError("forced")

    msg = {
        "request_id": "rid-2",
        "payload": {
            "venue": "tachibana",
            "user_id": _UNIQUE_USER_ID,
            "password": _UNIQUE_PASSWORD,
            "is_demo": True,
            # No session → goes straight to Step 2 (run_login).
        },
    }
    with caplog.at_level(logging.DEBUG, logger="engine.server"):
        with patch("engine.server.tachibana_run_login", fake_run_login):
            await server._do_set_venue_credentials(msg)

    events = []
    while server._outbox:
        events.append(server._outbox.popleft())

    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1, f"expected exactly one VenueError, got {events}"
    assert venue_errors[0]["code"] == "login_failed"
    assert venue_errors[0]["request_id"] == "rid-2"
    serialized = repr(venue_errors[0])
    assert _UNIQUE_PASSWORD not in serialized, (
        f"password leaked in event: {venue_errors[0]}"
    )
    assert _UNIQUE_USER_ID not in serialized, (
        f"user_id leaked in event: {venue_errors[0]}"
    )
    # Inner exception text must not leak.
    assert "forced" not in serialized

    # M-LOG ラウンド 5: `log.exception` produces a record with
    # `exc_text` (set lazily by Formatter / explicitly by .exception()
    # via `_log(... exc_info=True)`). Force formatting so the field
    # is populated, then scan it for the password literal — the
    # production handler must scrub `fallback_password` BEFORE the
    # exception path runs so even verbose formatters cannot pull it
    # out of the engine.server frame.
    import logging as _logging
    fmt = _logging.Formatter("%(message)s")
    for record in caplog.records:
        rendered = fmt.format(record)  # populates exc_text via formatException
        assert _UNIQUE_PASSWORD not in rendered, (
            f"password leaked into log record exc_text: {rendered!r}"
        )
        # Also assert the production frame's locals (rendered with
        # capture_locals=True) no longer contain the password literal.
        # Frames OUTSIDE engine.server (e.g. test fixtures) are out
        # of scope — we only enforce the scrub on our own frame.
        if record.exc_info:
            import traceback as _tb
            etype, evalue, etb = record.exc_info
            for frame_summary in _tb.StackSummary.extract(
                _tb.walk_tb(etb), capture_locals=True
            ):
                if frame_summary.filename.endswith("server.py"):
                    locals_repr = repr(frame_summary.locals or {})
                    assert _UNIQUE_PASSWORD not in locals_repr, (
                        f"password leaked in engine.server frame locals: "
                        f"{frame_summary.filename}:{frame_summary.lineno} "
                        f"{locals_repr}"
                    )
