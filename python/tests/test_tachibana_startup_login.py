"""Tests for startup_login() in tachibana_login_flow.py (T-SC5).

These tests mock the file-store functions and the login/validate helpers so
that no real network calls or tkinter subprocesses are spawned.

Patch targets are resolved to their *import-site* namespace
(`engine.exchanges.tachibana_login_flow.*`) because startup_login imports
the helpers with `from .tachibana_file_store import ...` and then calls them
as bare names inside the module.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_auth import StartupLatch, TachibanaSession
from engine.exchanges.tachibana_login_flow import LoginCancelled, startup_login
from engine.exchanges.tachibana_helpers import LoginError, PNoCounter
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SAMPLE_SESSION = TachibanaSession(
    url_request=RequestUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/request/"),
    url_master=MasterUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/master/"),
    url_price=PriceUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/price/"),
    url_event=EventUrl("https://demo-kabuka.e-shiten.jp/e_api_v4r8/event/"),
    url_event_ws="wss://demo-kabuka.e-shiten.jp/e_api_v4r8/ws/",
    zyoutoeki_kazei_c="0",
    expires_at_ms=9_999_999_999_000,  # far future — fresh in all tests
)

_MODULE = "engine.exchanges.tachibana_login_flow"


@pytest.fixture()
def latch() -> StartupLatch:
    return StartupLatch()


@pytest.fixture()
def p_no_counter() -> PNoCounter:
    return PNoCounter()


# ---------------------------------------------------------------------------
# test_startup_login_uses_cached_session_if_fresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_login_uses_cached_session_if_fresh(
    tmp_path: Path, latch: StartupLatch, p_no_counter: PNoCounter
) -> None:
    """load_session が TachibanaSession を返し、_is_session_fresh が True →
    validate_session_on_startup を呼んでセッションを返す（ダイアログは出ない）。
    """
    with (
        patch(f"{_MODULE}.load_session", return_value=_SAMPLE_SESSION) as mock_load,
        patch(f"{_MODULE}._is_session_fresh", return_value=True) as mock_fresh,
        patch(f"{_MODULE}.validate_session_on_startup", new_callable=AsyncMock) as mock_validate,
        patch(f"{_MODULE}._spawn_login_dialog") as mock_dialog,
    ):
        mock_validate.return_value = True
        result = await startup_login(
            tmp_path,
            tmp_path,
            p_no_counter=p_no_counter,
            startup_latch=latch,
        )

    assert result is _SAMPLE_SESSION
    mock_load.assert_called_once()
    mock_fresh.assert_called_once_with(_SAMPLE_SESSION)
    mock_validate.assert_called_once()
    mock_dialog.assert_not_called()


# ---------------------------------------------------------------------------
# test_startup_login_skips_cache_if_stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_login_skips_cache_if_stale(
    tmp_path: Path, latch: StartupLatch, p_no_counter: PNoCounter
) -> None:
    """load_session が TachibanaSession を返し、_is_session_fresh が False →
    validate_session_on_startup は呼ばれず、ダイアログ（_spawn_login_dialog）が呼ばれる。
    実装では stale のとき clear_session は呼ばれない（if ブロック自体がスキップ）。
    """
    dialog_result = {
        "status": "ok",
        "user_id": "test_user",
        "password": "test_pass",
        "is_demo": True,
    }

    with (
        patch(f"{_MODULE}.load_session", return_value=_SAMPLE_SESSION),
        patch(f"{_MODULE}._is_session_fresh", return_value=False),
        patch(f"{_MODULE}.validate_session_on_startup", new_callable=AsyncMock) as mock_validate,
        patch(f"{_MODULE}.load_account", return_value=None),
        patch(f"{_MODULE}._spawn_login_dialog", new_callable=AsyncMock) as mock_dialog,
        patch(f"{_MODULE}._do_login_call", new_callable=AsyncMock) as mock_login,
        patch(f"{_MODULE}.save_account") as _,
        patch(f"{_MODULE}.save_session") as _,
    ):
        mock_dialog.return_value = dialog_result
        mock_login.return_value = _SAMPLE_SESSION
        result = await startup_login(
            tmp_path,
            tmp_path,
            p_no_counter=p_no_counter,
            startup_latch=latch,
        )

    # stale のとき validate_session_on_startup は呼ばれない
    mock_validate.assert_not_called()
    mock_dialog.assert_called_once()
    assert result is _SAMPLE_SESSION


# ---------------------------------------------------------------------------
# test_startup_login_skips_cache_if_no_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_login_skips_cache_if_no_file(
    tmp_path: Path, latch: StartupLatch, p_no_counter: PNoCounter
) -> None:
    """load_session が None を返す → ダイアログ（_spawn_login_dialog）が呼ばれる。"""
    dialog_result = {
        "status": "ok",
        "user_id": "test_user",
        "password": "test_pass",
        "is_demo": True,
    }

    with (
        patch(f"{_MODULE}.load_session", return_value=None),
        patch(f"{_MODULE}.load_account", return_value=None),
        patch(f"{_MODULE}._spawn_login_dialog", new_callable=AsyncMock) as mock_dialog,
        patch(f"{_MODULE}._do_login_call", new_callable=AsyncMock) as mock_login,
        patch(f"{_MODULE}.save_account") as _,
        patch(f"{_MODULE}.save_session") as _,
    ):
        mock_dialog.return_value = dialog_result
        mock_login.return_value = _SAMPLE_SESSION
        result = await startup_login(
            tmp_path,
            tmp_path,
            p_no_counter=p_no_counter,
            startup_latch=latch,
        )

    mock_dialog.assert_called_once()
    assert result is _SAMPLE_SESSION


# ---------------------------------------------------------------------------
# test_startup_login_raises_login_cancelled_if_dialog_cancelled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_login_raises_login_cancelled_if_dialog_cancelled(
    tmp_path: Path, latch: StartupLatch, p_no_counter: PNoCounter
) -> None:
    """run_login がキャンセル（result=None）→ startup_login も LoginCancelled を raise。"""
    with (
        patch(f"{_MODULE}.load_session", return_value=None),
        patch(f"{_MODULE}.load_account", return_value=None),
        patch(f"{_MODULE}._spawn_login_dialog", new_callable=AsyncMock) as mock_dialog,
    ):
        mock_dialog.return_value = None  # user cancelled
        with pytest.raises(LoginCancelled):
            await startup_login(
                tmp_path,
                tmp_path,
                p_no_counter=p_no_counter,
                startup_latch=latch,
            )


# ---------------------------------------------------------------------------
# test_startup_login_saves_account_and_session_on_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_login_saves_account_and_session_on_success(
    tmp_path: Path, latch: StartupLatch, p_no_counter: PNoCounter
) -> None:
    """成功時に save_account と save_session が呼ばれること。"""
    dialog_result = {
        "status": "ok",
        "user_id": "save_user",
        "password": "save_pass",
        "is_demo": True,
    }

    with (
        patch(f"{_MODULE}.load_session", return_value=None),
        patch(f"{_MODULE}.load_account", return_value=None),
        patch(f"{_MODULE}._spawn_login_dialog", new_callable=AsyncMock) as mock_dialog,
        patch(f"{_MODULE}._do_login_call", new_callable=AsyncMock) as mock_login,
        patch(f"{_MODULE}.save_account") as mock_save_account,
        patch(f"{_MODULE}.save_session") as mock_save_session,
    ):
        mock_dialog.return_value = dialog_result
        mock_login.return_value = _SAMPLE_SESSION
        await startup_login(
            tmp_path,
            tmp_path,
            p_no_counter=p_no_counter,
            startup_latch=latch,
        )

    mock_save_account.assert_called_once_with(tmp_path, "save_user", True)
    mock_save_session.assert_called_once_with(tmp_path, _SAMPLE_SESSION)
