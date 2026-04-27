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
        "password": "SENTINEL_PW_dXk9Qa",
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
        "password": "SENTINEL_PW_dXk9Qa",
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
        "password": "SENTINEL_PW_g5Wm2R",
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


# ---------------------------------------------------------------------------
# B-1 TDD: startup_login RuntimeError → clear_session must be called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_login_runtime_error_calls_clear_session(
    tmp_path: Path, latch: StartupLatch, p_no_counter: PNoCounter
) -> None:
    """B-1: validate_session_on_startup が RuntimeError を raise したとき
    clear_session が呼ばれてから RuntimeError が伝播すること。
    """
    with (
        patch(f"{_MODULE}.load_session", return_value=_SAMPLE_SESSION),
        patch(f"{_MODULE}._is_session_fresh", return_value=True),
        patch(
            f"{_MODULE}.validate_session_on_startup",
            new_callable=AsyncMock,
            side_effect=RuntimeError("StartupLatch violated"),
        ),
        patch(f"{_MODULE}.clear_session") as mock_clear,
    ):
        with pytest.raises(RuntimeError, match="StartupLatch violated"):
            await startup_login(
                tmp_path,
                tmp_path,
                p_no_counter=p_no_counter,
                startup_latch=latch,
            )

    mock_clear.assert_called_once_with(tmp_path)


# ---------------------------------------------------------------------------
# D-2: T-SC5 必須テスト 3件 — server._startup_tachibana / _do_request_venue_login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_tachibana_login_cancelled_emits_venue_login_cancelled(
    tmp_path: Path,
) -> None:
    """D-2-1: startup_login が LoginCancelled を raise したとき
    VenueLoginCancelled{venue:"tachibana"} が outbox に入ること。
    """
    from engine.server import DataEngineServer
    from engine.exchanges.tachibana_login_flow import LoginCancelled

    server = DataEngineServer(
        port=19999,
        token="tok",
        cache_dir=tmp_path,
        config_dir=tmp_path,
    )

    emitted: list[dict] = []

    def _fake_emit(event: dict) -> None:
        emitted.append(event)

    with patch.object(server, "_emit", side_effect=_fake_emit):
        with patch(
            "engine.server.tachibana_startup_login",
            new_callable=AsyncMock,
            side_effect=LoginCancelled(),
        ):
            await server._startup_tachibana(request_id="req-cancel")

    assert any(
        e.get("event") == "VenueLoginCancelled" and e.get("venue") == "tachibana"
        for e in emitted
    ), f"Expected VenueLoginCancelled in emitted events, got: {emitted}"


@pytest.mark.asyncio
async def test_startup_tachibana_network_error_emits_venue_error_login_failed(
    tmp_path: Path,
) -> None:
    """D-2-2: startup_login が LoginError(code='login_failed') を raise したとき
    VenueError{code:'login_failed'} が outbox に入ること。
    """
    from engine.server import DataEngineServer
    from engine.exchanges.tachibana_helpers import LoginError

    server = DataEngineServer(
        port=19999,
        token="tok",
        cache_dir=tmp_path,
        config_dir=tmp_path,
    )

    emitted: list[dict] = []

    def _fake_emit(event: dict) -> None:
        emitted.append(event)

    with patch.object(server, "_emit", side_effect=_fake_emit):
        with patch(
            "engine.server.tachibana_startup_login",
            new_callable=AsyncMock,
            side_effect=LoginError(code="login_failed", message="network error"),
        ):
            await server._startup_tachibana(request_id="req-err")

    venue_errors = [e for e in emitted if e.get("event") == "VenueError"]
    assert venue_errors, f"Expected VenueError in emitted events, got: {emitted}"
    assert venue_errors[0].get("code") == "login_failed"
    assert venue_errors[0].get("venue") == "tachibana"


@pytest.mark.asyncio
async def test_do_request_venue_login_inflight_emits_only_venue_login_started(
    tmp_path: Path,
) -> None:
    """D-2-3: _tachibana_login_inflight.locked() が True のとき
    _do_request_venue_login が VenueLoginStarted のみを emit して
    startup_login を再実行しないこと。
    """
    from engine.server import DataEngineServer

    server = DataEngineServer(
        port=19999,
        token="tok",
        cache_dir=tmp_path,
        config_dir=tmp_path,
    )

    emitted: list[dict] = []

    def _fake_emit(event: dict) -> None:
        emitted.append(event)

    startup_called = []

    async def _fake_startup(request_id: str | None = None) -> None:
        startup_called.append(request_id)

    # Simulate in-flight by acquiring the lock
    async with server._tachibana_login_inflight:
        with patch.object(server, "_emit", side_effect=_fake_emit):
            with patch.object(server, "_startup_tachibana", side_effect=_fake_startup):
                await server._do_request_venue_login({
                    "op": "RequestVenueLogin",
                    "request_id": "req-inflight",
                    "venue": "tachibana",
                })

    assert startup_called == [], (
        f"_startup_tachibana must not be called while login is in-flight, "
        f"but got calls: {startup_called}"
    )
    assert any(
        e.get("event") == "VenueLoginStarted" for e in emitted
    ), f"Expected VenueLoginStarted in emitted events, got: {emitted}"
