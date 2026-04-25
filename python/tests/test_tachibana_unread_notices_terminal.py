"""Regression for Findings #3 — `UnreadNoticesError` from
`validate_session_on_startup` must surface as a terminal `VenueError{
code:"unread_notices"}` and must NOT trigger an automatic re-login flow.

`UnreadNoticesError` inherits from `LoginError`, so a previously broad
`except LoginError` in `_do_set_venue_credentials` was swallowing it
and dropping into `tachibana_run_login` (firing a wasteful
`VenueLoginStarted` event in the process). Spec §6 says the user must
visit the browser to acknowledge notices first.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from engine.exchanges.tachibana_helpers import UnreadNoticesError
from engine.server import DataEngineServer


@pytest.mark.asyncio
async def test_unread_notices_during_startup_validate_emits_terminal_venue_error():
    server = DataEngineServer(port=0, token="t", dev_tachibana_login_allowed=True)

    msg = {
        "request_id": "rid-unread",
        "payload": {
            "venue": "tachibana",
            "user_id": "u",
            "password": "p",
            "is_demo": True,
            "session": {
                "url_request": "https://demo-kabuka.e-shiten.jp/e_api_v4r8/req/SESS/",
                "url_master": "https://demo-kabuka.e-shiten.jp/e_api_v4r8/mst/SESS/",
                "url_price": "https://demo-kabuka.e-shiten.jp/e_api_v4r8/prc/SESS/",
                "url_event": "https://demo-kabuka.e-shiten.jp/e_api_v4r8/evt/SESS/",
                "url_event_ws": "wss://demo-kabuka.e-shiten.jp/e_api_v4r8/evt/SESS/",
                "expires_at_ms": None,
                "zyoutoeki_kazei_c": "1",
            },
        },
    }

    async def fake_validate(*_args, **_kwargs):
        raise UnreadNoticesError(
            message="立花からの未読通知があります。ブラウザで確認後に再ログインしてください"
        )

    spawn_called = []

    async def fake_spawn(prefill):
        spawn_called.append(prefill)
        return None

    with (
        patch(
            "engine.server.validate_session_on_startup",
            fake_validate,
        ),
        # Belt-and-suspenders: even if the wrong branch ran, this would
        # also confirm the dialog was NOT spawned.
        patch(
            "engine.exchanges.tachibana_login_flow._spawn_login_dialog",
            fake_spawn,
        ),
    ):
        await server._do_set_venue_credentials(msg)

    # Drain the outbox.
    events = []
    while server._outbox:
        events.append(server._outbox.popleft())

    # Expectation:
    #   * Exactly one VenueError with code=unread_notices.
    #   * NO VenueLoginStarted, NO VenueReady, NO VenueLoginCancelled.
    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1, f"expected one VenueError, got {events}"
    assert venue_errors[0]["code"] == "unread_notices"
    assert venue_errors[0]["request_id"] == "rid-unread"

    forbidden = [
        e
        for e in events
        if e.get("event")
        in ("VenueLoginStarted", "VenueReady", "VenueLoginCancelled")
    ]
    assert forbidden == [], (
        "unread_notices must be terminal — login flow must not fire. "
        f"Got: {events}"
    )

    # And no dialog spawn attempted, regardless of the path taken.
    assert spawn_called == [], (
        f"unread_notices must not spawn the login dialog; got {spawn_called}"
    )
