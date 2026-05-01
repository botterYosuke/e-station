"""Dispatch integration tests for GetPositions IPC (PP1)."""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from engine.exchanges.tachibana_orders import PositionRecord
from engine.exchanges.tachibana_helpers import SessionExpiredError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(session=None):
    """Create a minimal DataEngineServer with mocked dependencies."""
    from engine.server import DataEngineServer

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = []
    server._workers = {"tachibana": MagicMock()}
    server._tachibana_session = session
    server._tachibana_p_no_counter = MagicMock()
    server._session_holder = MagicMock()
    return server


# ---------------------------------------------------------------------------
# session=None → Error[SESSION_NOT_ESTABLISHED]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_session_none_returns_error():
    server = _make_server(session=None)

    await server._do_get_positions(
        {"op": "GetPositions", "request_id": "req-1", "venue": "tachibana"}
    )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "Error"
    assert ev["request_id"] == "req-1"
    assert ev["code"] == "SESSION_NOT_ESTABLISHED"


# ---------------------------------------------------------------------------
# unknown_venue → Error[unknown_venue]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_unknown_venue_returns_error():
    server = _make_server(session=MagicMock())

    await server._do_get_positions(
        {"op": "GetPositions", "request_id": "req-2", "venue": "binance"}
    )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "Error"
    assert ev["request_id"] == "req-2"
    assert ev["code"] == "unknown_venue"


# ---------------------------------------------------------------------------
# cash のみ → PositionsUpdated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_cash_only_returns_positions_updated():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    records = [
        PositionRecord(
            instrument_id="7203.TSE",
            qty=100,
            market_value=345600,
            position_type="cash",
        )
    ]

    with patch("engine.server.tachibana_fetch_positions", new_callable=AsyncMock, return_value=records):
        await server._do_get_positions(
            {"op": "GetPositions", "request_id": "req-3", "venue": "tachibana"}
        )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "PositionsUpdated"
    assert ev["request_id"] == "req-3"
    assert ev["venue"] == "tachibana"
    assert len(ev["positions"]) == 1
    pos = ev["positions"][0]
    assert pos["instrument_id"] == "7203.TSE"
    assert pos["qty"] == "100"
    assert pos["market_value"] == "345600"
    assert pos["position_type"] == "cash"
    assert pos["tategyoku_id"] is None
    assert pos["venue"] == "tachibana"
    assert isinstance(ev["ts_ms"], int)


# ---------------------------------------------------------------------------
# margin のみ → PositionsUpdated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_margin_only_returns_positions_updated():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    records = [
        PositionRecord(
            instrument_id="9984.TSE",
            qty=50,
            market_value=2134500,
            position_type="margin_credit",
            tategyoku_id="T-12345",
        )
    ]

    with patch("engine.server.tachibana_fetch_positions", new_callable=AsyncMock, return_value=records):
        await server._do_get_positions(
            {"op": "GetPositions", "request_id": "req-4", "venue": "tachibana"}
        )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "PositionsUpdated"
    assert len(ev["positions"]) == 1
    pos = ev["positions"][0]
    assert pos["instrument_id"] == "9984.TSE"
    assert pos["qty"] == "50"
    assert pos["market_value"] == "2134500"
    assert pos["position_type"] == "margin_credit"
    assert pos["tategyoku_id"] == "T-12345"


# ---------------------------------------------------------------------------
# 混在 → PositionsUpdated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_mixed_returns_positions_updated():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    records = [
        PositionRecord(
            instrument_id="7203.TSE",
            qty=100,
            market_value=345600,
            position_type="cash",
        ),
        PositionRecord(
            instrument_id="9984.TSE",
            qty=50,
            market_value=2134500,
            position_type="margin_credit",
            tategyoku_id="T-12345",
        ),
    ]

    with patch("engine.server.tachibana_fetch_positions", new_callable=AsyncMock, return_value=records):
        await server._do_get_positions(
            {"op": "GetPositions", "request_id": "req-5", "venue": "tachibana"}
        )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "PositionsUpdated"
    assert len(ev["positions"]) == 2


# ---------------------------------------------------------------------------
# 空配列 → PositionsUpdated (positions=[])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_empty_returns_positions_updated():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    with patch("engine.server.tachibana_fetch_positions", new_callable=AsyncMock, return_value=[]):
        await server._do_get_positions(
            {"op": "GetPositions", "request_id": "req-6", "venue": "tachibana"}
        )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "PositionsUpdated"
    assert ev["positions"] == []


# ---------------------------------------------------------------------------
# SessionExpiredError → Error[SESSION_EXPIRED]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_session_expired_returns_error():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    with patch(
        "engine.server.tachibana_fetch_positions",
        new_callable=AsyncMock,
        side_effect=SessionExpiredError("session expired"),
    ):
        await server._do_get_positions(
            {"op": "GetPositions", "request_id": "req-7", "venue": "tachibana"}
        )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "Error"
    assert ev["request_id"] == "req-7"
    assert ev["code"] == "SESSION_EXPIRED"
    # session_holder.clear() が呼ばれたことを確認
    server._session_holder.clear.assert_called_once()


# ---------------------------------------------------------------------------
# market_value=0 → wire "0"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_market_value_zero_serialized_as_string():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    records = [
        PositionRecord(
            instrument_id="6758.TSE",
            qty=10,
            market_value=0,  # デフォルト 0
            position_type="cash",
        )
    ]

    with patch("engine.server.tachibana_fetch_positions", new_callable=AsyncMock, return_value=records):
        await server._do_get_positions(
            {"op": "GetPositions", "request_id": "req-8", "venue": "tachibana"}
        )

    ev = server._outbox[0]
    pos = ev["positions"][0]
    assert pos["market_value"] == "0"


# ---------------------------------------------------------------------------
# tategyoku_id=None → wire null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_positions_tategyoku_id_none_in_wire():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    records = [
        PositionRecord(
            instrument_id="7203.TSE",
            qty=100,
            market_value=345600,
            position_type="cash",
            tategyoku_id=None,
        )
    ]

    with patch("engine.server.tachibana_fetch_positions", new_callable=AsyncMock, return_value=records):
        await server._do_get_positions(
            {"op": "GetPositions", "request_id": "req-9", "venue": "tachibana"}
        )

    ev = server._outbox[0]
    pos = ev["positions"][0]
    assert pos["tategyoku_id"] is None
