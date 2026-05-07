"""KabuApiError 派生クラスの発火条件テスト。"""
import pytest
import pytest_httpx

from engine.exchanges.kabusapi_auth import (
    KabuTokenExpiredError,
    KabuRateLimitError,
    KabuRegisterFullError,
    KabuTradePasswordHolder,
    fetch_token,
    check_response,
)
from engine.exchanges.kabusapi_url import endpoint


@pytest.mark.demo_kabu
def test_check_response_4001005_raises_token_expired():
    with pytest.raises(KabuTokenExpiredError):
        check_response({"Code": 4001005, "Message": "token expired"}, 401)


@pytest.mark.demo_kabu
def test_check_response_4001001_raises_token_expired():
    with pytest.raises(KabuTokenExpiredError):
        check_response({"Code": 4001001, "Message": "not logged in"}, 401)


@pytest.mark.demo_kabu
def test_check_response_4002006_raises_rate_limit():
    with pytest.raises(KabuRateLimitError):
        check_response({"Code": 4002006, "Message": "rate limit"}, 429)


@pytest.mark.demo_kabu
def test_check_response_4002001_raises_register_full():
    with pytest.raises(KabuRegisterFullError):
        check_response({"Code": 4002001, "Message": "register full"}, 400)


@pytest.mark.demo_kabu
def test_check_response_ok():
    # code=0 は正常
    check_response({"Code": 0, "Message": ""}, 200)


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_token_success(httpx_mock: pytest_httpx.HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=endpoint("token", env="verify"),
        json={"ResultCode": 0, "Token": "abc1234567890abcd"},
    )
    token = await fetch_token("test_password", env="verify")
    assert token == "abc1234567890abcd"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_token_401_raises_token_expired(httpx_mock: pytest_httpx.HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=endpoint("token", env="verify"),
        status_code=401,
        json={"Code": 4001001, "Message": "not logged in"},
    )
    with pytest.raises(KabuTokenExpiredError):
        await fetch_token("wrong_password", env="verify")


# ---------------------------------------------------------------------------
# R2 review-fix M-3 / M-4: KabuTradePasswordHolder の単体テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
def test_kabu_station_venue_is_trade_locked_out_after_3_invalids():
    """KabuStationVenue.is_trade_locked_out() が 3 回連続失敗後 True を返す。"""
    from engine.exchanges.kabusapi import KabuStationVenue

    venue = KabuStationVenue(env="verify")
    # 失敗前は False
    assert venue.is_trade_locked_out() is False

    # on_invalid を 3 回呼んで lockout
    holder = venue._trade_password_holder
    holder.set_password("dummy")
    holder.on_invalid()
    holder.on_invalid()
    holder.on_invalid()

    assert venue.is_trade_locked_out() is True


@pytest.mark.demo_kabu
def test_kabu_station_venue_is_trade_locked_out_false_before_lockout():
    """lockout 前は is_trade_locked_out() が False を返す（負の不変条件）。"""
    from engine.exchanges.kabusapi import KabuStationVenue

    venue = KabuStationVenue(env="verify")
    holder = venue._trade_password_holder
    holder.set_password("dummy")
    # 1 回 / 2 回失敗ではまだ lockout しない
    holder.on_invalid()
    assert venue.is_trade_locked_out() is False
    holder.on_invalid()
    assert venue.is_trade_locked_out() is False


@pytest.mark.demo_kabu
def test_on_invalid_clears_last_use_time():
    """on_invalid() が _last_use_time を None にリセットする pin（R8 M-3 後退防止）。"""
    holder = KabuTradePasswordHolder()
    holder.set_password("dummy")
    assert holder._last_use_time is not None
    holder.on_invalid()
    assert holder._last_use_time is None
