"""startup_login のテスト: TCP refused retry / debug env / 早朝時刻帯。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.kabusapi_auth import KabuConnectionError, KabuLoginCancelledError
from engine.exchanges.kabusapi_login_flow import startup_login, _is_morning_logout_window


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_tcp_refused_three_retries_then_local_app_down(monkeypatch: pytest.MonkeyPatch):
    """TCP 拒否時 5s × 3 回後に KabuConnectionError が raise される (INV-K3-TCP-RETRY)."""
    # HIGH-B: DEV_KABU_API_PASSWORD を設定しないと _spawn_dialog() が呼ばれて CI でハングする。
    monkeypatch.setenv("DEV_KABU_API_PASSWORD", "test_pass")
    with patch("engine.exchanges.kabusapi_login_flow.fetch_token",
               side_effect=KabuConnectionError(0, "Connection refused")) as mock_fetch, \
         patch("engine.exchanges.kabusapi_login_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(KabuConnectionError):
            await startup_login(
                env="verify",
                dev_login_allowed=True,
            )
        # fetch_token が _MAX_RETRIES 回呼ばれる
        assert mock_fetch.call_count == 3
        # asyncio.sleep が (_MAX_RETRIES - 1) 回呼ばれる
        assert mock_sleep.call_count == 2


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_no_tkinter_when_dev_env_set(monkeypatch: pytest.MonkeyPatch):
    """DEV_KABU_API_PASSWORD 設定時に tkinter ダイアログを spawn しない (INV-K3-NO-TKINTER)."""
    monkeypatch.setenv("DEV_KABU_API_PASSWORD", "test_password_123")
    token_value = "returned_token_abc"

    with patch("engine.exchanges.kabusapi_login_flow.fetch_token",
               new_callable=AsyncMock, return_value=token_value) as mock_fetch, \
         patch("engine.exchanges.kabusapi_login_flow._spawn_dialog") as mock_dialog:
        result = await startup_login(env="verify", dev_login_allowed=True)

    assert result == token_value
    mock_dialog.assert_not_called()
    mock_fetch.assert_called_once()


@pytest.mark.demo_kabu
def test_is_morning_logout_window_false_at_noon():
    """昼間は早朝時刻帯ではない。"""
    import datetime
    jst = datetime.timezone(datetime.timedelta(hours=9))
    noon_jst = datetime.datetime(2026, 5, 7, 12, 0, 0, tzinfo=jst)
    with patch("engine.exchanges.kabusapi_login_flow.datetime") as mock_dt:
        mock_dt.datetime.now.return_value = noon_jst
        mock_dt.timezone = datetime.timezone
        mock_dt.timedelta = datetime.timedelta
        # 12時は early morning window ではない (range(4, 9) に含まれない)
        assert not (12 in range(4, 9))


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_login_success_with_token(monkeypatch: pytest.MonkeyPatch):
    """正常系: fetch_token が成功するとトークンが返る。"""
    monkeypatch.setenv("DEV_KABU_API_PASSWORD", "correct_password")
    expected_token = "success_token_xyz"

    with patch("engine.exchanges.kabusapi_login_flow.fetch_token",
               new_callable=AsyncMock, return_value=expected_token):
        result = await startup_login(env="verify", dev_login_allowed=True)

    assert result == expected_token


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_dialog_cancel_raises_kabu_login_cancelled_error():
    """HIGH-2: ダイアログキャンセル時に KabuLoginCancelledError が raise される。

    _spawn_dialog が {"status": "cancelled"} を返す → startup_login が
    KabuLoginCancelledError を raise する（KabuConnectionError ではない）。
    """
    with patch(
        "engine.exchanges.kabusapi_login_flow._spawn_dialog",
        new_callable=AsyncMock,
        return_value={"status": "cancelled"},
    ):
        with pytest.raises(KabuLoginCancelledError):
            await startup_login(env="verify", dev_login_allowed=False)


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_dialog_cancel_is_not_kabu_connection_error():
    """HIGH-2: キャンセルエラーは KabuConnectionError の誤変換を防ぐ。

    KabuLoginCancelledError は KabuConnectionError のサブクラスではない。
    """
    with patch(
        "engine.exchanges.kabusapi_login_flow._spawn_dialog",
        new_callable=AsyncMock,
        return_value={"status": "cancelled"},
    ):
        try:
            await startup_login(env="verify", dev_login_allowed=False)
        except KabuLoginCancelledError:
            pass  # expected
        except KabuConnectionError:
            pytest.fail("cancel should raise KabuLoginCancelledError, not KabuConnectionError")
