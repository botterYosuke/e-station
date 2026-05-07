"""KabuOrderClient (先物・OP 発注) / KabuRestClient (余力・銘柄名照会) テスト (Phase 3)。

Acceptance criteria:
- test_send_order_future_posts_to_correct_url
- test_send_order_future_buy_side_is_2
- test_send_order_future_sell_side_is_1
- test_send_order_future_market_order_front_order_type_120
- test_send_order_future_limit_order_includes_price
- test_send_order_future_uses_order_bucket
- test_send_order_future_no_password_in_body
- test_send_order_option_posts_to_correct_url
- test_send_order_option_buy_side_is_2
- test_send_order_option_sell_side_is_1
- test_send_order_option_no_password_in_body
- test_fetch_wallet_future_calls_correct_url
- test_fetch_wallet_option_calls_correct_url
- test_fetch_symbolname_future_calls_correct_url
- test_fetch_symbolname_option_calls_correct_url
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_httpx

from engine.exchanges.kabusapi_auth import KabuTradePasswordHolder
from engine.exchanges.kabusapi_orders import KabuOrderClient
from engine.exchanges.kabusapi_rest import KabuRestClient
from engine.exchanges.kabusapi_register import RegisterSet
from engine.exchanges.kabusapi_url import endpoint


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def make_order_client(
    httpx_mock: pytest_httpx.HTTPXMock,
    holder: KabuTradePasswordHolder | None = None,
) -> KabuOrderClient:
    if holder is None:
        holder = KabuTradePasswordHolder()
        holder.set_password("trade_pass_xyz")
    return KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
    )


def make_rest_client() -> KabuRestClient:
    return KabuRestClient(
        token="test_token_abc",
        env="verify",
        register_set=RegisterSet(),
    )


_FUTURE_ORDER_RESP = {"OrderID": "20240101A01N00000001", "Result": 0}
_OPTION_ORDER_RESP = {"OrderID": "20240101A01N00000002", "Result": 0}
_WALLET_FUTURE_RESP = {"StockAccountWallet": 1000000.0}
_WALLET_OPTION_RESP = {"StockAccountWallet": 500000.0}
_SYMBOLNAME_FUTURE_RESP = {"Symbol": "169090018", "SymbolName": "日経225先物 22/03"}
_SYMBOLNAME_OPTION_RESP = {"Symbol": "169090019", "SymbolName": "日経225op 23/06 C27250"}


# ---------------------------------------------------------------------------
# KabuOrderClient — 先物発注テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_future_posts_to_correct_url(httpx_mock: pytest_httpx.HTTPXMock):
    """send_order_future() が /sendorder/future に POST することを assert。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/future", env="verify"),
        json=_FUTURE_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    result = await client.send_order_future(
        symbol="169090018",
        side="buy",
        qty=1,
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert "sendorder/future" in str(requests[0].url)
    assert result["OrderID"] == _FUTURE_ORDER_RESP["OrderID"]


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_future_buy_side_is_2(httpx_mock: pytest_httpx.HTTPXMock):
    """先物買い注文の Side は "2"。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/future", env="verify"),
        json=_FUTURE_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_future(symbol="169090018", side="buy", qty=1)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["Side"] == "2", f"buy の Side は '2' でなければならない。実際: {body['Side']!r}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_future_sell_side_is_1(httpx_mock: pytest_httpx.HTTPXMock):
    """先物売り注文の Side は "1"。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/future", env="verify"),
        json=_FUTURE_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_future(symbol="169090018", side="sell", qty=1)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["Side"] == "1", f"sell の Side は '1' でなければならない。実際: {body['Side']!r}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_future_market_order_front_order_type_120(httpx_mock: pytest_httpx.HTTPXMock):
    """成行注文のデフォルト FrontOrderType は 120（マーケットオーダー）。OpenAPI 有効値: 18/20/28/30/120。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/future", env="verify"),
        json=_FUTURE_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_future(
        symbol="169090018",
        side="buy",
        qty=1,
    )

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["FrontOrderType"] == 120, f"成行のデフォルトは 120 でなければならない。実際: {body['FrontOrderType']!r}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_future_limit_order_includes_price(httpx_mock: pytest_httpx.HTTPXMock):
    """指値注文の FrontOrderType は 2 かつ Price フィールドが含まれる。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/future", env="verify"),
        json=_FUTURE_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_future(
        symbol="169090018",
        side="buy",
        qty=1,
        front_order_type=20,  # 20=指値（OpenAPI 有効値）
        price=28500.0,
    )

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["FrontOrderType"] == 20
    assert "Price" in body
    assert body["Price"] == 28500.0


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_future_uses_order_bucket(httpx_mock: pytest_httpx.HTTPXMock):
    """send_order_future() が order_bucket を経由する（流量制限 5/s が掛かる）。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/future", env="verify"),
        json=_FUTURE_ORDER_RESP,
    )

    bucket = MagicMock()
    bucket.__aenter__ = AsyncMock(return_value=None)
    bucket.__aexit__ = AsyncMock(return_value=False)

    holder = KabuTradePasswordHolder()
    holder.set_password("trade_pass_xyz")

    client = KabuOrderClient(
        token="test_token_abc",
        env="verify",
        trade_password_holder=holder,
        order_bucket=bucket,
    )

    await client.send_order_future(symbol="169090018", side="buy", qty=1)

    bucket.__aenter__.assert_called_once()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_future_no_password_in_body(httpx_mock: pytest_httpx.HTTPXMock):
    """先物発注 body に Password フィールドが含まれないこと（OpenAPI RequestSendOrderDerivFuture に Password なし）。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/future", env="verify"),
        json=_FUTURE_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_future(symbol="169090018", side="buy", qty=1)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "Password" not in body, "先物発注に Password フィールドを含めてはならない（OpenAPI 仕様）"


# ---------------------------------------------------------------------------
# KabuOrderClient — OP 発注テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_option_posts_to_correct_url(httpx_mock: pytest_httpx.HTTPXMock):
    """send_order_option() が /sendorder/option に POST することを assert。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/option", env="verify"),
        json=_OPTION_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    result = await client.send_order_option(
        symbol="169090019",
        side="buy",
        qty=1,
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert "sendorder/option" in str(requests[0].url)
    assert result["OrderID"] == _OPTION_ORDER_RESP["OrderID"]


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_option_buy_side_is_2(httpx_mock: pytest_httpx.HTTPXMock):
    """OP 買い注文の Side は "2"。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/option", env="verify"),
        json=_OPTION_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_option(symbol="169090019", side="buy", qty=1)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["Side"] == "2", f"buy の Side は '2' でなければならない。実際: {body['Side']!r}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_option_sell_side_is_1(httpx_mock: pytest_httpx.HTTPXMock):
    """OP 売り注文の Side は "1"。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/option", env="verify"),
        json=_OPTION_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_option(symbol="169090019", side="sell", qty=1)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["Side"] == "1", f"sell の Side は '1' でなければならない。実際: {body['Side']!r}"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_send_order_option_no_password_in_body(httpx_mock: pytest_httpx.HTTPXMock):
    """OP 発注 body に Password フィールドが含まれないこと（OpenAPI RequestSendOrderDerivOption に Password なし）。"""
    httpx_mock.add_response(
        method="POST",
        url=endpoint("sendorder/option", env="verify"),
        json=_OPTION_ORDER_RESP,
    )

    client = make_order_client(httpx_mock)
    await client.send_order_option(symbol="169090019", side="buy", qty=1)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert "Password" not in body, "OP 発注に Password フィールドを含めてはならない（OpenAPI 仕様）"


# ---------------------------------------------------------------------------
# KabuRestClient — 余力・銘柄名照会テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_wallet_future_calls_correct_url(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_wallet_future() が GET /wallet/future を呼ぶ。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("wallet/future", env="verify"),
        json=_WALLET_FUTURE_RESP,
    )

    client = make_rest_client()
    result = await client.fetch_wallet_future()

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert "wallet/future" in str(requests[0].url)
    assert result == _WALLET_FUTURE_RESP


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_wallet_option_calls_correct_url(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_wallet_option() が GET /wallet/option を呼ぶ。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("wallet/option", env="verify"),
        json=_WALLET_OPTION_RESP,
    )

    client = make_rest_client()
    result = await client.fetch_wallet_option()

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert "wallet/option" in str(requests[0].url)
    assert result == _WALLET_OPTION_RESP


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_symbolname_future_calls_correct_url(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_symbolname_future() が GET /symbolname/future?FutureCode=NK225&DerivMonth=202012 を呼ぶ。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("symbolname/future", env="verify") + "?FutureCode=NK225&DerivMonth=202012",
        json=_SYMBOLNAME_FUTURE_RESP,
    )

    client = make_rest_client()
    result = await client.fetch_symbolname_future(
        future_code="NK225",
        deriv_month=202012,
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert "symbolname/future" in str(requests[0].url)
    assert "FutureCode=NK225" in str(requests[0].url)
    assert "DerivMonth=202012" in str(requests[0].url)
    assert result == _SYMBOLNAME_FUTURE_RESP


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_fetch_symbolname_option_calls_correct_url(httpx_mock: pytest_httpx.HTTPXMock):
    """fetch_symbolname_option() が GET /symbolname/option?OptionCode=NK225op&... を呼ぶ。"""
    httpx_mock.add_response(
        method="GET",
        url=endpoint("symbolname/option", env="verify")
        + "?OptionCode=NK225op&DerivMonth=202306&PutOrCall=C&StrikePrice=27250",
        json=_SYMBOLNAME_OPTION_RESP,
    )

    client = make_rest_client()
    result = await client.fetch_symbolname_option(
        option_code="NK225op",
        deriv_month=202306,
        put_or_call="C",
        strike_price=27250,
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert "symbolname/option" in str(requests[0].url)
    assert "OptionCode=NK225op" in str(requests[0].url)
    assert "DerivMonth=202306" in str(requests[0].url)
    assert "PutOrCall=C" in str(requests[0].url)
    assert "StrikePrice=27250" in str(requests[0].url)
    assert result == _SYMBOLNAME_OPTION_RESP
