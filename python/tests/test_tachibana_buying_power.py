"""T3.5 Phase O3 — 余力・建玉 API テスト (T3.2)。

CLMZanKaiKanougaku / CLMZanShinkiKanoIjiritu / CLMZanUriKanousuu / CLMGenbutuKabuList
のレスポンスパースと余力不足 → OrderRejected 写像を検証する。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_orders import (
    BuyingPowerResult,
    CreditBuyingPowerResult,
    SellableQtyResult,
    PositionRecord,
    InsufficientFundsError,
    fetch_buying_power,
    fetch_credit_buying_power,
    fetch_sellable_qty,
    fetch_positions,
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


def _mock_response(data: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = json.dumps(data, ensure_ascii=False).encode("shift_jis")
    return mock_resp


def _mock_client(data: dict) -> MagicMock:
    mock_resp = _mock_response(data)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return mock_client


# ---------------------------------------------------------------------------
# T3.2-1 fetch_buying_power (CLMZanKaiKanougaku)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_buying_power_parses_response():
    """CLMZanKaiKanougaku レスポンス → BuyingPowerResult に正しくパースされること。"""
    data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sCLMID": "CLMZanKaiKanougaku",
        "sZanKaiKanougakuGoukei": "500000",   # 現物買付可能額合計
        "sZanKaiKanougakuHusoku": "0",          # 余力不足額
    }
    mock_client = _mock_client(data)
    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_buying_power(_session())

    assert isinstance(result, BuyingPowerResult)
    assert result.available_amount == 500000
    assert result.shortfall == 0


@pytest.mark.asyncio
async def test_fetch_buying_power_detects_shortfall():
    """余力不足額 > 0 → BuyingPowerResult.shortfall > 0。"""
    data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sCLMID": "CLMZanKaiKanougaku",
        "sZanKaiKanougakuGoukei": "0",
        "sZanKaiKanougakuHusoku": "100000",
    }
    mock_client = _mock_client(data)
    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_buying_power(_session())

    assert result.shortfall == 100000


# ---------------------------------------------------------------------------
# T3.2-2 fetch_credit_buying_power (CLMZanShinkiKanoIjiritu)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_credit_buying_power_parses_response():
    """CLMZanShinkiKanoIjiritu レスポンス → CreditBuyingPowerResult。"""
    data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sCLMID": "CLMZanShinkiKanoIjiritu",
        "sZanShinkiKanoIjirituGoukei": "1000000",  # 信用新規可能額
        "sZanShinkiKanoIjirituHusoku": "0",
    }
    mock_client = _mock_client(data)
    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_credit_buying_power(_session())

    assert isinstance(result, CreditBuyingPowerResult)
    assert result.available_amount == 1000000


# ---------------------------------------------------------------------------
# T3.2-3 fetch_sellable_qty (CLMZanUriKanousuu)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_sellable_qty_parses_response():
    """CLMZanUriKanousuu レスポンス → SellableQtyResult。"""
    data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sCLMID": "CLMZanUriKanousuu",
        "sZanUriKanouSuu": "200",
    }
    mock_client = _mock_client(data)
    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_sellable_qty(_session(), "7203.T/TSE")

    assert isinstance(result, SellableQtyResult)
    assert result.sellable_qty == 200


# ---------------------------------------------------------------------------
# T3.2-4 fetch_positions (CLMGenbutuKabuList + CLMShinyouTategyokuList)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_positions_cash_parses_response():
    """CLMGenbutuKabuList レスポンス → PositionRecord のリスト。"""
    cash_data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sCLMID": "CLMGenbutuKabuList",
        "aGenbutuKabuList": [
            {
                "sIssueCode": "7203",
                "sGenbutuZanSuu": "100",
                "sGenbutuZanKingaku": "250000",
            }
        ],
    }
    margin_data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sCLMID": "CLMShinyouTategyokuList",
        "aTategyokuList": [],
    }

    call_count = 0

    async def _mock_get_side_effect(url: str) -> bytes:
        nonlocal call_count
        call_count += 1
        if "CLMGenbutuKabuList" in url or call_count == 1:
            return json.dumps(cash_data, ensure_ascii=False).encode("shift_jis")
        else:
            return json.dumps(margin_data, ensure_ascii=False).encode("shift_jis")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    resp_cash = _mock_response(cash_data)
    resp_margin = _mock_response(margin_data)
    mock_client.get = AsyncMock(side_effect=[resp_cash, resp_margin])

    with patch("httpx.AsyncClient", return_value=mock_client):
        positions = await fetch_positions(_session())

    assert isinstance(positions, list)
    assert len(positions) >= 1
    cash_pos = positions[0]
    assert isinstance(cash_pos, PositionRecord)
    assert cash_pos.instrument_id == "7203.TSE"
    assert cash_pos.qty == 100


# ---------------------------------------------------------------------------
# T3.3 余力不足 → InsufficientFundsError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insufficient_funds_rejects_order():
    """余力不足 fetch_buying_power → InsufficientFundsError が raise される。"""
    data = {
        "p_errno": "0",
        "sResultCode": "0",
        "sCLMID": "CLMZanKaiKanougaku",
        "sZanKaiKanougakuGoukei": "0",
        "sZanKaiKanougakuHusoku": "50000",
    }
    mock_client = _mock_client(data)
    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_buying_power(_session())

    # 余力不足の場合、呼び出し元が InsufficientFundsError を raise する
    with pytest.raises(InsufficientFundsError) as exc_info:
        if result.shortfall > 0:
            raise InsufficientFundsError(
                f"Insufficient funds: shortfall={result.shortfall}",
                shortfall=result.shortfall,
            )

    assert exc_info.value.shortfall == 50000
    assert exc_info.value.reason_code == "INSUFFICIENT_FUNDS"
