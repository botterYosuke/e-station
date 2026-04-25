"""HIGH-D1 — Python side of the `dev_tachibana_login_allowed` integration.

The Python `tachibana_login_flow.run_login` must NEVER read
`DEV_TACHIBANA_*` / `DEV_*` env vars to skip the tkinter dialog when
`dev_login_allowed=False`. This is the release-build guard: even if
operators leave dev creds in the environment, a release build (which
sets `dev_tachibana_login_allowed=False` in the stdin payload) cannot
auto-login. Falling back to the dialog path is acceptable; silently
proceeding via env is not.

The test patches the dialog spawn to a sentinel that records calls and
returns "cancelled", so we observe whether the env fast path was taken
without involving a real tkinter subprocess or live HTTP.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from unittest.mock import patch

import pytest

from engine.exchanges.tachibana_helpers import PNoCounter
from engine.exchanges import tachibana_login_flow


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def env_with_dev_creds(monkeypatch):
    """Set DEV_TACHIBANA_* env vars (the canonical names per SKILL.md)."""
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", "envuser")
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", "envpass")
    monkeypatch.setenv("DEV_TACHIBANA_DEMO", "true")
    # Ensure no legacy aliases pollute the test.
    monkeypatch.delenv("DEV_USER_ID", raising=False)
    monkeypatch.delenv("DEV_PASSWORD", raising=False)
    monkeypatch.delenv("DEV_IS_DEMO", raising=False)


@pytest.mark.asyncio
async def test_dev_login_disallowed_does_not_fast_path_even_with_full_env(
    env_with_dev_creds,
):
    """`dev_login_allowed=False` must NEVER read env — even if every
    env var is present, the dialog spawn must be the only login path."""
    spawn_called = []

    async def _fake_spawn(prefill: Optional[dict]) -> Optional[dict]:
        spawn_called.append(prefill)
        return None  # simulate user cancellation

    with patch.object(
        tachibana_login_flow, "_spawn_login_dialog", _fake_spawn
    ):
        events = await tachibana_login_flow.run_login(
            request_id="rid-1",
            p_no_counter=PNoCounter(),
            dev_login_allowed=False,
        )

    # Dialog spawn was attempted (and cancelled).
    assert len(spawn_called) == 1, (
        f"dialog must be the only login path when "
        f"dev_login_allowed=False, but spawn calls = {spawn_called}"
    )

    # Event sequence must be VenueLoginStarted → VenueLoginCancelled.
    assert events[0]["event"] == "VenueLoginStarted"
    assert events[-1]["event"] == "VenueLoginCancelled"


@pytest.mark.asyncio
async def test_dev_login_allowed_uses_env_without_spawning_dialog(
    env_with_dev_creds,
):
    """Counter-positive: when `dev_login_allowed=True` AND env creds
    are present, the dialog must NOT be spawned — the fast path must
    call `tachibana_auth.login` directly. This pins the spec semantics
    so we know the previous test's no-fast-path is genuine."""
    spawn_called = []

    async def _fake_spawn(prefill: Optional[dict]) -> Optional[dict]:
        spawn_called.append(prefill)
        return None

    async def _fake_login(*args, **kwargs):
        # Fail with a typed login error so the test does not need an
        # HTTP mock; the assertion is purely about the *path taken*.
        from engine.exchanges.tachibana_helpers import LoginError

        raise LoginError(code="login_failed", message="dummy")

    with (
        patch.object(tachibana_login_flow, "_spawn_login_dialog", _fake_spawn),
        patch.object(tachibana_login_flow, "tachibana_login", _fake_login),
    ):
        events = await tachibana_login_flow.run_login(
            request_id="rid-2",
            p_no_counter=PNoCounter(),
            dev_login_allowed=True,
        )

    assert spawn_called == [], (
        "dialog must NOT be spawned when env fast path is available"
    )
    # Fast path executed → only VenueError. **No VenueLoginStarted**:
    # that event is reserved for the dialog-spawn path so the UI banner
    # "別ウィンドウでログイン中" is never a lie (Findings #2).
    assert any(e.get("event") == "VenueError" for e in events)
    assert all(e.get("event") != "VenueLoginStarted" for e in events), (
        f"VenueLoginStarted must not fire on env fast path; got {events}"
    )


@pytest.mark.asyncio
async def test_dev_login_allowed_falls_back_to_dialog_when_env_missing(monkeypatch):
    """`dev_login_allowed=True` but env not set → dialog is spawned."""
    for name in (
        "DEV_TACHIBANA_USER_ID",
        "DEV_TACHIBANA_PASSWORD",
        "DEV_TACHIBANA_DEMO",
        "DEV_USER_ID",
        "DEV_PASSWORD",
        "DEV_IS_DEMO",
    ):
        monkeypatch.delenv(name, raising=False)

    spawn_called = []

    async def _fake_spawn(prefill: Optional[dict]) -> Optional[dict]:
        spawn_called.append(prefill)
        return None

    with patch.object(tachibana_login_flow, "_spawn_login_dialog", _fake_spawn):
        events = await tachibana_login_flow.run_login(
            request_id="rid-3",
            p_no_counter=PNoCounter(),
            dev_login_allowed=True,
        )

    assert len(spawn_called) == 1
    assert events[0]["event"] == "VenueLoginStarted"
    assert events[-1]["event"] == "VenueLoginCancelled"
