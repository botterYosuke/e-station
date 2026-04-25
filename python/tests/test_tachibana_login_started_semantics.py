"""Regression for Findings #2 / #3 (post-T3 review round 2).

#2: `VenueLoginStarted` must fire **only when the tkinter dialog
    subprocess is actually spawned**. Earlier the event was appended
    unconditionally at the top of `run_login`, so the env fast path
    and the silent keyring-creds path both surfaced "ログインダイアログを
    別ウィンドウで表示中" to the UI even though no window opened.

#3: When `_do_set_venue_credentials` falls through to a fresh login
    because the stored session is stale, the `user_id` / `password` /
    `is_demo` carried by the `SetVenueCredentials` payload must be
    used as a silent fallback before the dialog spawns. The dialog's
    `prefill` must additionally surface the user_id so the user does
    not retype it.
"""

from __future__ import annotations

from typing import List, Optional
from unittest.mock import patch

import pytest

from engine.exchanges import tachibana_login_flow
from engine.exchanges.tachibana_helpers import (
    LoginError,
    PNoCounter,
    SessionExpiredError,
    UnreadNoticesError,
)


def _stub_session():
    from engine.exchanges.tachibana_auth import TachibanaSession
    from engine.exchanges.tachibana_url import (
        EventUrl,
        MasterUrl,
        PriceUrl,
        RequestUrl,
    )

    return TachibanaSession(
        url_request=RequestUrl(
            "https://demo-kabuka.e-shiten.jp/e_api_v4r8/req/SES/"
        ),
        url_master=MasterUrl(
            "https://demo-kabuka.e-shiten.jp/e_api_v4r8/mst/SES/"
        ),
        url_price=PriceUrl(
            "https://demo-kabuka.e-shiten.jp/e_api_v4r8/prc/SES/"
        ),
        url_event=EventUrl(
            "https://demo-kabuka.e-shiten.jp/e_api_v4r8/evt/SES/"
        ),
        url_event_ws="wss://demo-kabuka.e-shiten.jp/e_api_v4r8/evt/SES/",
        zyoutoeki_kazei_c="1",
        expires_at_ms=None,
    )


# ── #2: VenueLoginStarted only when dialog spawns ────────────────────────────


@pytest.mark.asyncio
async def test_env_fast_path_does_not_emit_venue_login_started(monkeypatch):
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", "envuser")
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", "envpass")
    monkeypatch.setenv("DEV_TACHIBANA_DEMO", "true")
    for legacy in ("DEV_USER_ID", "DEV_PASSWORD", "DEV_IS_DEMO"):
        monkeypatch.delenv(legacy, raising=False)

    spawn_called: List = []

    async def fake_spawn(prefill):
        spawn_called.append(prefill)
        return None

    async def fake_login(*args, **kwargs):
        return _stub_session()

    with (
        patch.object(tachibana_login_flow, "_spawn_login_dialog", fake_spawn),
        patch.object(tachibana_login_flow, "tachibana_login", fake_login),
    ):
        events = await tachibana_login_flow.run_login(
            request_id="rid-fast",
            p_no_counter=PNoCounter(),
            dev_login_allowed=True,
        )

    assert spawn_called == [], "dialog must not spawn on fast path"
    types = [e["event"] for e in events]
    assert "VenueLoginStarted" not in types, (
        f"VenueLoginStarted must NOT fire when no dialog is spawned; got {types}"
    )
    # Fast path success → VenueCredentialsRefreshed + VenueReady.
    assert types == ["VenueCredentialsRefreshed", "VenueReady"], types


@pytest.mark.asyncio
async def test_dialog_path_does_emit_venue_login_started(monkeypatch):
    for name in (
        "DEV_TACHIBANA_USER_ID",
        "DEV_TACHIBANA_PASSWORD",
        "DEV_TACHIBANA_DEMO",
        "DEV_USER_ID",
        "DEV_PASSWORD",
        "DEV_IS_DEMO",
    ):
        monkeypatch.delenv(name, raising=False)

    async def fake_spawn(prefill):
        return None  # cancelled

    with patch.object(tachibana_login_flow, "_spawn_login_dialog", fake_spawn):
        events = await tachibana_login_flow.run_login(
            request_id="rid-dialog",
            p_no_counter=PNoCounter(),
            dev_login_allowed=True,
        )

    types = [e["event"] for e in events]
    assert types[0] == "VenueLoginStarted", types
    assert types[-1] == "VenueLoginCancelled", types


# ── #3: payload fallback creds + dialog prefill ──────────────────────────────


@pytest.mark.asyncio
async def test_fallback_creds_used_silently_before_dialog(monkeypatch):
    """Startup with fallback creds in keyring → silent login attempt
    succeeds → no dialog, no `VenueLoginStarted`."""
    for name in (
        "DEV_TACHIBANA_USER_ID",
        "DEV_TACHIBANA_PASSWORD",
        "DEV_USER_ID",
        "DEV_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    spawn_called: List = []
    seen_login_args: List[dict] = []

    async def fake_spawn(prefill):
        spawn_called.append(prefill)
        return None

    async def fake_login(user_id, password, *, is_demo, **kwargs):
        seen_login_args.append(
            {"user_id": user_id, "password": password, "is_demo": is_demo}
        )
        return _stub_session()

    with (
        patch.object(tachibana_login_flow, "_spawn_login_dialog", fake_spawn),
        patch.object(tachibana_login_flow, "tachibana_login", fake_login),
    ):
        events = await tachibana_login_flow.run_login(
            request_id="rid-fb",
            p_no_counter=PNoCounter(),
            dev_login_allowed=False,
            is_startup=True,
            fallback_user_id="storeduser",
            fallback_password="storedpass",
            fallback_is_demo=True,
        )

    assert spawn_called == [], "dialog must not spawn when fallback creds work"
    assert seen_login_args == [
        {"user_id": "storeduser", "password": "storedpass", "is_demo": True}
    ], "fallback creds must be passed to the silent login"
    types = [e["event"] for e in events]
    assert "VenueLoginStarted" not in types, types
    assert types == ["VenueCredentialsRefreshed", "VenueReady"], types


@pytest.mark.asyncio
async def test_fallback_creds_rejected_falls_through_to_dialog_with_prefill(
    monkeypatch,
):
    """Fallback creds yield a clean LoginError → dialog opens prefilled
    with the user_id (so the user only retypes the password)."""
    for name in (
        "DEV_TACHIBANA_USER_ID",
        "DEV_TACHIBANA_PASSWORD",
        "DEV_USER_ID",
        "DEV_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    spawn_calls: List[Optional[dict]] = []

    async def fake_spawn(prefill):
        spawn_calls.append(prefill)
        return None  # user cancels the dialog

    async def fake_login(*args, **kwargs):
        raise LoginError(code="login_failed", message="bad password")

    with (
        patch.object(tachibana_login_flow, "_spawn_login_dialog", fake_spawn),
        patch.object(tachibana_login_flow, "tachibana_login", fake_login),
    ):
        events = await tachibana_login_flow.run_login(
            request_id="rid-prefill",
            p_no_counter=PNoCounter(),
            dev_login_allowed=False,
            is_startup=True,
            fallback_user_id="storeduser",
            fallback_password="staletoo",
            fallback_is_demo=True,
        )

    assert len(spawn_calls) == 1, "dialog must be reached after fallback rejection"
    assert spawn_calls[0] == {"user_id": "storeduser", "is_demo": True}, (
        f"dialog prefill must surface fallback user_id; got {spawn_calls[0]}"
    )
    types = [e["event"] for e in events]
    # Dialog WAS spawned now → VenueLoginStarted is correct.
    assert types[0] == "VenueLoginStarted", types
    assert types[-1] == "VenueLoginCancelled", types


@pytest.mark.asyncio
async def test_fallback_creds_with_unread_notices_is_terminal(monkeypatch):
    """Fallback path UnreadNoticesError must be terminal — do NOT
    fall through to the dialog (consistent with Findings #3 prior-round
    rule for the keyring-stored-session path)."""
    for name in (
        "DEV_TACHIBANA_USER_ID",
        "DEV_TACHIBANA_PASSWORD",
        "DEV_USER_ID",
        "DEV_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    spawn_calls: List = []

    async def fake_spawn(prefill):
        spawn_calls.append(prefill)
        return None

    async def fake_login(*args, **kwargs):
        raise UnreadNoticesError(message="未読通知あり")

    with (
        patch.object(tachibana_login_flow, "_spawn_login_dialog", fake_spawn),
        patch.object(tachibana_login_flow, "tachibana_login", fake_login),
    ):
        events = await tachibana_login_flow.run_login(
            request_id="rid-unread",
            p_no_counter=PNoCounter(),
            dev_login_allowed=False,
            is_startup=True,
            fallback_user_id="u",
            fallback_password="p",
            fallback_is_demo=True,
        )

    assert spawn_calls == [], "unread_notices must not open the dialog"
    types = [e["event"] for e in events]
    assert "VenueLoginStarted" not in types
    venue_errors = [e for e in events if e.get("event") == "VenueError"]
    assert len(venue_errors) == 1
    assert venue_errors[0]["code"] == "unread_notices"
