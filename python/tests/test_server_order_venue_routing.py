"""Issue #25: GetOrderList / GetBuyingPower / GetPositions の venue 分岐テスト。

TDD RED → GREEN: venue='kabu_station' が kabu ハンドラにルーティングされることを確認する。
venue='tachibana'（デフォルト）は既存の立花ハンドラを使用する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.server import DataEngineServer, LiveState


# ---------------------------------------------------------------------------
# テストヘルパー
# ---------------------------------------------------------------------------


def _make_server(*, session=None, live_state=None, kabu_venue=None):
    """DataEngineServer を最低限の属性で構築するヘルパー。"""
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        srv = DataEngineServer()

    srv._outbox = []
    srv._workers = {"tachibana": MagicMock()}
    srv._tachibana_session = session
    srv._tachibana_p_no_counter = MagicMock()
    srv._session_holder = MagicMock()
    srv._live_state = live_state if live_state is not None else LiveState.CONNECTED
    srv._kabu_venue = kabu_venue
    return srv


def _make_kabu_venue(token: str = "test-token"):
    """_token を持つ最低限の kabu venue mock を返す。"""
    venue = MagicMock()
    venue._token = token
    venue.is_connected = True
    return venue


# ---------------------------------------------------------------------------
# GetOrderList venue ルーティング
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_order_list_with_venue_kabu_routes_to_kabu_handler():
    """GetOrderList{venue='kabu_station'} が kabu ハンドラ (_do_get_order_list_kabu) を呼ぶ。"""
    srv = _make_server(kabu_venue=_make_kabu_venue())

    with patch.object(srv, "_do_get_order_list_kabu", new_callable=AsyncMock) as mock_kabu:
        await srv._do_get_order_list(
            {"op": "GetOrderList", "request_id": "req-kabu-1", "venue": "kabu_station"}
        )

    mock_kabu.assert_called_once()
    call_msg = mock_kabu.call_args[0][0]
    assert call_msg["venue"] == "kabu_station"


@pytest.mark.asyncio
async def test_get_order_list_with_venue_tachibana_routes_to_tachibana_handler():
    """GetOrderList{venue='tachibana'} が立花ハンドラを呼ぶ（kabu ハンドラは呼ばない）。"""
    mock_session = MagicMock()
    srv = _make_server(session=mock_session)

    fake_records: list = []

    with patch.object(srv, "_do_get_order_list_kabu", new_callable=AsyncMock) as mock_kabu, \
         patch(
             "engine.server.tachibana_fetch_order_list",
             new_callable=AsyncMock,
             return_value=fake_records,
         ):
        await srv._do_get_order_list(
            {"op": "GetOrderList", "request_id": "req-tachi-1", "venue": "tachibana"}
        )

    mock_kabu.assert_not_called()
    events = [e.get("event") for e in srv._outbox]
    assert "OrderListUpdated" in events


# ---------------------------------------------------------------------------
# GetBuyingPower venue ルーティング
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_buying_power_venue_routing_kabu():
    """GetBuyingPower{venue='kabu_station'} が kabu ハンドラ (_do_get_buying_power_kabu) を呼ぶ。"""
    srv = _make_server(kabu_venue=_make_kabu_venue())

    with patch.object(srv, "_do_get_buying_power_kabu", new_callable=AsyncMock) as mock_kabu:
        await srv._do_get_buying_power(
            {"op": "GetBuyingPower", "request_id": "req-kabu-2", "venue": "kabu_station"}
        )

    mock_kabu.assert_called_once()
    call_msg = mock_kabu.call_args[0][0]
    assert call_msg["venue"] == "kabu_station"


@pytest.mark.asyncio
async def test_get_buying_power_venue_routing_tachibana():
    """GetBuyingPower{venue='tachibana'} が立花ハンドラを呼ぶ（kabu ハンドラは呼ばない）。"""
    mock_session = MagicMock()
    srv = _make_server(session=mock_session)

    from engine.exchanges.tachibana_orders import BuyingPowerResult, CreditBuyingPowerResult

    cash = BuyingPowerResult(available_amount=1_000_000, shortfall=0)
    credit = CreditBuyingPowerResult(available_amount=500_000)

    with patch.object(srv, "_do_get_buying_power_kabu", new_callable=AsyncMock) as mock_kabu, \
         patch("engine.server.tachibana_fetch_buying_power", new_callable=AsyncMock, return_value=cash), \
         patch("engine.server.tachibana_fetch_credit_buying_power", new_callable=AsyncMock, return_value=credit):
        await srv._do_get_buying_power(
            {"op": "GetBuyingPower", "request_id": "req-tachi-2", "venue": "tachibana"}
        )

    mock_kabu.assert_not_called()
    events = [e.get("event") for e in srv._outbox]
    assert "BuyingPowerUpdated" in events


# ---------------------------------------------------------------------------
# GetPositions venue ルーティング
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_venue_routing_kabu():
    """GetPositions{venue='kabu_station'} が kabu ハンドラ (_do_get_positions_kabu) を呼ぶ。"""
    srv = _make_server(kabu_venue=_make_kabu_venue())

    with patch.object(srv, "_do_get_positions_kabu", new_callable=AsyncMock) as mock_kabu:
        await srv._do_get_positions(
            {"op": "GetPositions", "request_id": "req-kabu-3", "venue": "kabu_station"}
        )

    mock_kabu.assert_called_once()
    call_msg = mock_kabu.call_args[0][0]
    assert call_msg["venue"] == "kabu_station"


@pytest.mark.asyncio
async def test_get_positions_venue_routing_tachibana():
    """GetPositions{venue='tachibana'} が立花ハンドラを呼ぶ（kabu ハンドラは呼ばない）。"""
    mock_session = MagicMock()
    srv = _make_server(session=mock_session)

    fake_records: list = []

    with patch.object(srv, "_do_get_positions_kabu", new_callable=AsyncMock) as mock_kabu, \
         patch("engine.server.tachibana_fetch_positions", new_callable=AsyncMock, return_value=fake_records):
        await srv._do_get_positions(
            {"op": "GetPositions", "request_id": "req-tachi-3", "venue": "tachibana"}
        )

    mock_kabu.assert_not_called()
    events = [e.get("event") for e in srv._outbox]
    assert "PositionsUpdated" in events


# ---------------------------------------------------------------------------
# kabu 実装: _do_get_order_list_kabu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_get_order_list_kabu_returns_order_list_updated():
    """_do_get_order_list_kabu が OrderListUpdated を emit する。"""
    srv = _make_server(kabu_venue=_make_kabu_venue())

    fake_orders = [
        {
            "ID": "20230101A01234567",
            "Symbol": "9433",
            "Side": "1",
            "OrdType": "1",
            "Price": 0,
            "OrderQty": 100,
            "CumQty": 0,
            "OrdStatus": "2",
        }
    ]

    mock_client = MagicMock()
    mock_client.fetch_orders = AsyncMock(return_value=fake_orders)

    with patch.object(srv, "_make_kabu_rest_client", return_value=mock_client):
        await srv._do_get_order_list_kabu(
            {"op": "GetOrderList", "request_id": "req-kabu-impl-1", "venue": "kabu_station"}
        )

    events = [e.get("event") for e in srv._outbox]
    assert "OrderListUpdated" in events
    ev = next(e for e in srv._outbox if e.get("event") == "OrderListUpdated")
    assert ev["request_id"] == "req-kabu-impl-1"
    assert isinstance(ev["orders"], list)


@pytest.mark.asyncio
async def test_do_get_order_list_kabu_no_token_returns_error():
    """kabu_venue が None（未ログイン）の場合 Error を emit する。"""
    srv = _make_server(kabu_venue=None)

    await srv._do_get_order_list_kabu(
        {"op": "GetOrderList", "request_id": "req-kabu-no-token", "venue": "kabu_station"}
    )

    events = [e.get("event") for e in srv._outbox]
    assert "Error" in events
    ev = next(e for e in srv._outbox if e.get("event") == "Error")
    assert ev["code"] == "NOT_LOGGED_IN"


# ---------------------------------------------------------------------------
# kabu 実装: _do_get_buying_power_kabu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_get_buying_power_kabu_returns_buying_power_updated():
    """_do_get_buying_power_kabu が BuyingPowerUpdated を emit する。"""
    srv = _make_server(kabu_venue=_make_kabu_venue())

    fake_cash = {"StockAccountWallet": 1000000.0}
    fake_margin = {"MarginAccountWallet": 500000.0}

    mock_client = MagicMock()
    mock_client.fetch_wallet_cash = AsyncMock(return_value=fake_cash)
    mock_client.fetch_wallet_margin = AsyncMock(return_value=fake_margin)

    with patch.object(srv, "_make_kabu_rest_client", return_value=mock_client):
        await srv._do_get_buying_power_kabu(
            {"op": "GetBuyingPower", "request_id": "req-kabu-impl-2", "venue": "kabu_station"}
        )

    events = [e.get("event") for e in srv._outbox]
    assert "BuyingPowerUpdated" in events
    ev = next(e for e in srv._outbox if e.get("event") == "BuyingPowerUpdated")
    assert ev["request_id"] == "req-kabu-impl-2"
    assert ev["venue"] == "kabu_station"
    assert ev["cash_available"] == 1000000.0


@pytest.mark.asyncio
async def test_do_get_buying_power_kabu_no_token_returns_error():
    """kabu_venue が None（未ログイン）の場合 Error を emit する。"""
    srv = _make_server(kabu_venue=None)

    await srv._do_get_buying_power_kabu(
        {"op": "GetBuyingPower", "request_id": "req-kabu-no-token-2", "venue": "kabu_station"}
    )

    events = [e.get("event") for e in srv._outbox]
    assert "Error" in events
    ev = next(e for e in srv._outbox if e.get("event") == "Error")
    assert ev["code"] == "NOT_LOGGED_IN"


# ---------------------------------------------------------------------------
# kabu 実装: _do_get_positions_kabu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_do_get_positions_kabu_returns_positions_updated():
    """_do_get_positions_kabu が PositionsUpdated を emit する。"""
    srv = _make_server(kabu_venue=_make_kabu_venue())

    fake_positions = [
        {
            "Symbol": "9433",
            "Side": "1",
            "Leaves": 100,
            "Price": 1500.0,
        }
    ]

    mock_client = MagicMock()
    mock_client.fetch_positions = AsyncMock(return_value=fake_positions)

    with patch.object(srv, "_make_kabu_rest_client", return_value=mock_client):
        await srv._do_get_positions_kabu(
            {"op": "GetPositions", "request_id": "req-kabu-impl-3", "venue": "kabu_station"}
        )

    events = [e.get("event") for e in srv._outbox]
    assert "PositionsUpdated" in events
    ev = next(e for e in srv._outbox if e.get("event") == "PositionsUpdated")
    assert ev["request_id"] == "req-kabu-impl-3"
    assert ev["venue"] == "kabu_station"
    assert isinstance(ev["positions"], list)


@pytest.mark.asyncio
async def test_do_get_positions_kabu_no_token_returns_error():
    """kabu_venue が None（未ログイン）の場合 Error を emit する。"""
    srv = _make_server(kabu_venue=None)

    await srv._do_get_positions_kabu(
        {"op": "GetPositions", "request_id": "req-kabu-no-token-3", "venue": "kabu_station"}
    )

    events = [e.get("event") for e in srv._outbox]
    assert "Error" in events
    ev = next(e for e in srv._outbox if e.get("event") == "Error")
    assert ev["code"] == "NOT_LOGGED_IN"
