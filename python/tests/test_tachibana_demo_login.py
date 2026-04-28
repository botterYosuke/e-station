"""Real-network acceptance test for Tachibana demo account login.

Run with:
    set -a && source .env && set +a
    uv run pytest python/tests/ -m demo_tachibana -v

Required env vars:
    DEV_TACHIBANA_USER_ID   - demo account user ID
    DEV_TACHIBANA_PASSWORD  - demo account password
    DEV_TACHIBANA_DEMO      - must be "true" / "1" / "yes" / "on"

Tests are skipped automatically when credentials are absent so the regular
CI suite (no .env) remains unaffected.
"""

from __future__ import annotations

import os

import pytest

from engine.exchanges.tachibana_auth import TachibanaSession, login, validate_session_on_startup, StartupLatch
from engine.exchanges.tachibana_helpers import LoginError, PNoCounter

# Tachibana demo API is only available during market hours.
_OUTSIDE_HOURS_ERRNO = "-62"

pytestmark = pytest.mark.demo_tachibana

_SKIP_MSG = (
    "Demo credentials not set "
    "(DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD / DEV_TACHIBANA_DEMO=true)"
)


def _get_demo_creds() -> tuple[str, str]:
    """Return (user_id, password) or skip the test."""
    user_id = os.environ.get("DEV_TACHIBANA_USER_ID", "")
    password = os.environ.get("DEV_TACHIBANA_PASSWORD", "")
    raw_demo = os.environ.get("DEV_TACHIBANA_DEMO", "")
    is_demo = raw_demo.strip().lower() in ("1", "true", "yes", "on")
    if not user_id or not password:
        pytest.skip(_SKIP_MSG)
    if not is_demo:
        pytest.skip("DEV_TACHIBANA_DEMO is not set to a truthy value — aborting to protect prod account")
    return user_id, password


@pytest.mark.asyncio
async def test_demo_login_returns_valid_session() -> None:
    """Smoke: login returns a TachibanaSession with valid https:// and wss:// URLs."""
    user_id, password = _get_demo_creds()

    counter = PNoCounter()
    try:
        session = await login(user_id, password, is_demo=True, p_no_counter=counter)
    except LoginError as e:
        if e.code == _OUTSIDE_HOURS_ERRNO:
            pytest.skip("Tachibana demo API is outside service hours (p_errno=-62)")
        raise

    assert isinstance(session, TachibanaSession)
    assert str(session.url_request).startswith("https://"), f"url_request={session.url_request}"
    assert str(session.url_master).startswith("https://"), f"url_master={session.url_master}"
    assert str(session.url_price).startswith("https://"), f"url_price={session.url_price}"
    assert str(session.url_event).startswith("https://"), f"url_event={session.url_event}"
    assert session.url_event_ws.startswith("wss://"), f"url_event_ws={session.url_event_ws}"


@pytest.mark.asyncio
async def test_demo_session_validates_on_startup() -> None:
    """Smoke: a freshly obtained session passes validate_session_on_startup."""
    user_id, password = _get_demo_creds()

    counter = PNoCounter()
    try:
        session = await login(user_id, password, is_demo=True, p_no_counter=counter)
    except LoginError as e:
        if e.code == _OUTSIDE_HOURS_ERRNO:
            pytest.skip("Tachibana demo API is outside service hours (p_errno=-62)")
        raise

    latch = StartupLatch()
    result = await validate_session_on_startup(session, latch=latch, p_no_counter=counter)
    assert result is True
