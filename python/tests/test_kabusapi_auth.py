"""KabuApiError 派生クラスの発火条件テスト。"""
import pytest
import pytest_httpx

from engine.exchanges.kabusapi_auth import (
    KabuTokenExpiredError,
    KabuRateLimitError,
    KabuRegisterFullError,
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
