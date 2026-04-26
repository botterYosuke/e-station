"""TDD Red → Green: T0.4 — submit_order() HTTP 送信テスト（mock）。

submit_order() が:
- _envelope_to_wire() → _compose_request_payload() → build_request_url() → httpx GET を順に呼ぶ
- レスポンスの sOrderNumber を SubmitOrderResult.venue_order_id に返す
- warning_code / warning_text を SubmitOrderResult に格納する
- check_response() でエラーを検知したら SessionExpiredError / TachibanaError を上げる
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_helpers import PNoCounter, SessionExpiredError, TachibanaError
from engine.exchanges.tachibana_orders import (
    NautilusOrderEnvelope,
    SubmitOrderResult,
    submit_order,
)
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://demo.example/request/"),
        url_master=MasterUrl("https://demo.example/master/"),
        url_price=PriceUrl("https://demo.example/price/"),
        url_event=EventUrl("https://demo.example/event/"),
        url_event_ws="wss://demo.example/event/",
        zyoutoeki_kazei_c="1",
    )


def _market_buy_envelope() -> NautilusOrderEnvelope:
    return NautilusOrderEnvelope(
        client_order_id="cid-001",
        instrument_id="7203.T/TSE",
        order_side="BUY",
        order_type="MARKET",
        quantity="100",
        time_in_force="DAY",
        post_only=False,
        reduce_only=False,
        tags=["cash_margin=cash"],
    )


def _make_mock_response(
    *,
    p_errno: str = "0",
    result_code: str = "0",
    order_number: str = "ORD-12345",
    warning_code: str = "",
    warning_text: str = "",
) -> bytes:
    data = {
        "p_errno": p_errno,
        "sResultCode": result_code,
        "sOrderNumber": order_number,
        "sEigyouDay": "20260426",
        "sWarningCode": warning_code,
        "sWarningText": warning_text,
    }
    # Encode as Shift-JIS (as Tachibana returns)
    return json.dumps(data, ensure_ascii=False).encode("shift_jis")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_order_returns_venue_order_id():
    """正常系: sOrderNumber が SubmitOrderResult.venue_order_id に入ること。"""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = _make_mock_response(order_number="ORD-001")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await submit_order(_session(), "password", _market_buy_envelope())

    assert isinstance(result, SubmitOrderResult)
    assert result.venue_order_id == "ORD-001"
    assert result.client_order_id == "cid-001"


@pytest.mark.asyncio
async def test_submit_order_propagates_warning_code():
    """warning_code / warning_text が SubmitOrderResult に格納されること。"""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = _make_mock_response(
        order_number="ORD-002",
        warning_code="W001",
        warning_text="注意メッセージ",
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await submit_order(_session(), "password", _market_buy_envelope())

    assert result.warning_code == "W001"
    assert result.warning_text == "注意メッセージ"


@pytest.mark.asyncio
async def test_submit_order_raises_on_session_expired():
    """p_errno=2 → SessionExpiredError が上がること。"""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = _make_mock_response(p_errno="2")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(SessionExpiredError):
            await submit_order(_session(), "password", _market_buy_envelope())


@pytest.mark.asyncio
async def test_submit_order_raises_on_api_error():
    """sResultCode!=0 → TachibanaError が上がること。"""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = _make_mock_response(result_code="ERR001")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(TachibanaError):
            await submit_order(_session(), "password", _market_buy_envelope())


@pytest.mark.asyncio
async def test_submit_order_venue_order_id_is_none_when_missing():
    """sOrderNumber が欠落した場合、venue_order_id は None になること（H-5）。"""
    import json as _json

    data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sEigyouDay": "20260426",
        "sWarningCode": "",
        "sWarningText": "",
        # sOrderNumber は意図的に省略
    }
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = _json.dumps(data, ensure_ascii=False).encode("shift_jis")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await submit_order(_session(), "password", _market_buy_envelope())

    assert result.venue_order_id is None, (
        f"venue_order_id must be None when sOrderNumber is missing, got {result.venue_order_id!r}"
    )
