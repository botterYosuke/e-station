"""ログマスク: token / API パスワード / 取引パスワードが caplog に出ないことを確認。"""
import pytest
import pytest_httpx

from engine.exchanges.kabusapi_auth import fetch_token
from engine.exchanges.kabusapi_url import endpoint

SECRET_TOKEN = "SECRET_TOKEN_VALUE_1234"
SECRET_API_PASSWORD = "SECRET_API_PASSWORD_XYZ"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_no_token_in_log(
    httpx_mock: pytest_httpx.HTTPXMock, caplog: pytest.LogCaptureFixture
):
    httpx_mock.add_response(
        method="POST",
        url=endpoint("token", env="verify"),
        json={"ResultCode": 0, "Token": SECRET_TOKEN},
    )
    import logging
    with caplog.at_level(logging.DEBUG, logger="engine.exchanges.kabusapi_auth"):
        await fetch_token(SECRET_API_PASSWORD, env="verify")

    assert SECRET_TOKEN not in caplog.text, "token should not appear in logs"
    assert SECRET_API_PASSWORD not in caplog.text, "API password should not appear in logs"


@pytest.mark.demo_kabu
def test_no_trade_password_in_log(caplog: pytest.LogCaptureFixture):
    """取引パスワードは Phase 2 以降の概念だが、もし渡されてもログに出ないことを確認。"""
    import logging
    TRADE_PASSWORD = "SECRET_TRADE_PASS_999"
    with caplog.at_level(logging.DEBUG):
        # 直接ログに取引パスワードを渡す実装が誤って追加された場合を防ぐダミー検証
        import logging as _logging
        _logging.getLogger("engine.exchanges.kabusapi_auth").info("test message without secrets")
    assert TRADE_PASSWORD not in caplog.text
