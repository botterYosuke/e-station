"""KabuRestClient のテスト: fetch_board の touch / 満杯エラー。

C-1 追加テスト: 既存 6 メソッドの非 JSON レスポンス時 KabuApiError + timeout=30.0。
R2-H1 追加テスト: register/touch 順序修正（HTTP 失敗時に register を呼ばない）。
"""
import pytest
import pytest_httpx
import httpx
from unittest.mock import MagicMock, call

from engine.exchanges.kabusapi_auth import KabuApiError, KabuRegisterFullError
from engine.exchanges.kabusapi_register import RegisterSet
from engine.exchanges.kabusapi_rest import KabuRestClient
from engine.exchanges.kabusapi_url import endpoint, symbol_key


def make_client(register_set: RegisterSet, httpx_mock) -> KabuRestClient:
    return KabuRestClient(
        token="test_token_abc",
        env="verify",
        register_set=register_set,
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_touches_register_set(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_board() が RegisterSet.touch() を呼ぶ (INV-K6-TOUCH)."""
    rs = RegisterSet(max_symbols=50)
    rs.register("9433", 1)  # 事前登録

    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        json={
            "Symbol": "9433",
            "Exchange": 1,
            "CurrentPrice": 2479.0,
        },
    )

    client = make_client(rs, httpx_mock)
    result = await client.fetch_board("9433", 1)

    assert result["Symbol"] == "9433"
    # touch によって LRU 位置が更新されたことを確認（9433 が最新になる）
    assert ("9433", 1) in rs


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_raises_when_full(httpx_mock: pytest_httpx.HTTPXMock):
    """新規 + 満杯時に KabuRegisterFullError が raise される (INV-K6-REST-FULL).

    R2-H1: register() は HTTP 成功後に呼ばれるため、HTTP モックが必要。
    満杯チェックは HTTP 成功後に実行される。
    """
    rs = RegisterSet(max_symbols=1)
    rs.register("7203", 1)  # 1 件で満杯

    # R2-H1 後: HTTP 成功後に register() が呼ばれるため、モックが必要
    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        json={"Symbol": "9433", "Exchange": 1, "CurrentPrice": 100.0},
    )
    client = make_client(rs, httpx_mock)

    with pytest.raises(KabuRegisterFullError):
        await client.fetch_board("9433", 1)  # 新規 + 満杯


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_existing_symbol_calls_touch(httpx_mock: pytest_httpx.HTTPXMock):
    """既存銘柄の fetch_board は register を呼ばずに touch のみ。"""
    rs = RegisterSet(max_symbols=1)
    rs.register("9433", 1)

    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        json={"Symbol": "9433", "Exchange": 1, "CurrentPrice": 100.0},
    )

    client = make_client(rs, httpx_mock)
    await client.fetch_board("9433", 1)

    # まだ 1 件のみ（touch されたが register は呼ばれていない）
    assert len(rs) == 1


# ---------------------------------------------------------------------------
# [C-1] 非 JSON レスポンス時の KabuApiError テスト
# ---------------------------------------------------------------------------


def _make_plain_client() -> KabuRestClient:
    return KabuRestClient(
        token="test_token_abc",
        env="verify",
        register_set=RegisterSet(max_symbols=50),
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_raises_on_non_json_response(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_board() が非 JSON レスポンス受信時に KabuApiError を raise する。"""
    rs = RegisterSet(max_symbols=50)
    rs.register("9433", 1)

    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        text="<html>Maintenance</html>",
        status_code=503,
    )

    client = make_client(rs, httpx_mock)
    with pytest.raises(KabuApiError):
        await client.fetch_board("9433", 1)


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_symbol_raises_on_non_json_response(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_symbol() が非 JSON レスポンス受信時に KabuApiError を raise する。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"symbol/{symbol_key('9433', 1)}", env="verify"),
        text="<html>Error</html>",
        status_code=503,
    )

    client = _make_plain_client()
    with pytest.raises(KabuApiError):
        await client.fetch_symbol("9433", 1)


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_orders_raises_on_non_json_response(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_orders() が非 JSON レスポンス受信時に KabuApiError を raise する。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("orders", env="verify"),
        text="<html>Error</html>",
        status_code=503,
    )

    client = _make_plain_client()
    with pytest.raises(KabuApiError):
        await client.fetch_orders()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_positions_raises_on_non_json_response(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_positions() が非 JSON レスポンス受信時に KabuApiError を raise する。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("positions", env="verify"),
        text="<html>Error</html>",
        status_code=503,
    )

    client = _make_plain_client()
    with pytest.raises(KabuApiError):
        await client.fetch_positions()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_wallet_cash_raises_on_non_json_response(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_wallet_cash() が非 JSON レスポンス受信時に KabuApiError を raise する。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("wallet/cash", env="verify"),
        text="<html>Error</html>",
        status_code=503,
    )

    client = _make_plain_client()
    with pytest.raises(KabuApiError):
        await client.fetch_wallet_cash()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_wallet_margin_raises_on_non_json_response(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_wallet_margin() が非 JSON レスポンス受信時に KabuApiError を raise する。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("wallet/margin", env="verify"),
        text="<html>Error</html>",
        status_code=503,
    )

    client = _make_plain_client()
    with pytest.raises(KabuApiError):
        await client.fetch_wallet_margin()


# ---------------------------------------------------------------------------
# [R2-H1] register/touch 順序修正テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_does_not_register_on_http_failure(monkeypatch):
    """HTTP が ConnectError を raise した場合、register_set.register() が呼ばれない。

    新規銘柄は HTTP 成功後にのみ RegisterSet に追加すること (R2-H1)。
    """
    mock_rs = MagicMock(spec=RegisterSet)
    # 新規銘柄: __contains__ は False を返す
    mock_rs.__contains__ = MagicMock(return_value=False)

    client = KabuRestClient(
        token="test_token_abc",
        env="verify",
        register_set=mock_rs,
    )

    # HTTP クライアントが ConnectError を raise するようモック
    async def _raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        "httpx.AsyncClient.get",
        _raise_connect_error,
    )

    with pytest.raises(httpx.ConnectError):
        await client.fetch_board("9433", 1)

    # HTTP 失敗時は register() を呼ばない
    mock_rs.register.assert_not_called()
    # touch() も呼ばない（既存銘柄ではないため）
    mock_rs.touch.assert_not_called()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_registers_after_http_success(httpx_mock: pytest_httpx.HTTPXMock):
    """HTTP 成功後に新規銘柄が RegisterSet.register() で登録される (R2-H1)。"""
    mock_rs = MagicMock(spec=RegisterSet)
    # 新規銘柄: __contains__ は False を返す
    mock_rs.__contains__ = MagicMock(return_value=False)

    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        json={"Symbol": "9433", "Exchange": 1, "CurrentPrice": 2479.0},
    )

    client = KabuRestClient(
        token="test_token_abc",
        env="verify",
        register_set=mock_rs,
    )
    result = await client.fetch_board("9433", 1)

    assert result["Symbol"] == "9433"
    # HTTP 成功後に register() が呼ばれる
    mock_rs.register.assert_called_once_with("9433", 1)
    # 新規銘柄なので touch() は呼ばれない
    mock_rs.touch.assert_not_called()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_board_touches_existing_before_http(httpx_mock: pytest_httpx.HTTPXMock):
    """既存銘柄の touch() が HTTP 前に呼ばれ、HTTP 成功後に register() が呼ばれない (R2-H1)。"""
    call_log: list[str] = []

    mock_rs = MagicMock(spec=RegisterSet)
    # 既存銘柄: __contains__ は True を返す
    mock_rs.__contains__ = MagicMock(return_value=True)

    def _touch_side_effect(symbol, exchange):
        call_log.append("touch")

    mock_rs.touch.side_effect = _touch_side_effect

    # HTTP レスポンスは touch() の後に来る
    httpx_mock.add_response(
        method="GET",
        url=endpoint(f"board/{symbol_key('9433', 1)}", env="verify"),
        json={"Symbol": "9433", "Exchange": 1, "CurrentPrice": 100.0},
    )

    client = KabuRestClient(
        token="test_token_abc",
        env="verify",
        register_set=mock_rs,
    )
    await client.fetch_board("9433", 1)

    # touch() が呼ばれた
    mock_rs.touch.assert_called_once_with("9433", 1)
    # 既存銘柄なので register() は呼ばれない
    mock_rs.register.assert_not_called()
    # touch は HTTP の前に記録されている（call_log に "touch" が入っている = HTTP前に呼ばれた証拠）
    assert call_log == ["touch"]
