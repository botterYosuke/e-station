"""Dispatch integration tests for GetBuyingPower IPC (J-7, J-9, J-11)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_helpers import TachibanaError
from engine.exchanges.tachibana_orders import BuyingPowerResult, CreditBuyingPowerResult


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
# J-9: session=None → Error[SESSION_NOT_ESTABLISHED]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_buying_power_session_none_returns_error():
    server = _make_server(session=None)

    await server._do_get_buying_power(
        {"op": "GetBuyingPower", "request_id": "req-1", "venue": "tachibana"}
    )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "Error"
    assert ev["request_id"] == "req-1"
    assert ev["code"] == "SESSION_NOT_ESTABLISHED"


# ---------------------------------------------------------------------------
# J-11: normal path → BuyingPowerUpdated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_buying_power_success_returns_updated():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    cash = BuyingPowerResult(available_amount=1_000_000, shortfall=0)
    credit = CreditBuyingPowerResult(available_amount=500_000)

    with patch(
        "engine.server.tachibana_fetch_buying_power", new_callable=AsyncMock, return_value=cash
    ), patch(
        "engine.server.tachibana_fetch_credit_buying_power",
        new_callable=AsyncMock,
        return_value=credit,
    ):
        await server._do_get_buying_power(
            {"op": "GetBuyingPower", "request_id": "req-2", "venue": "tachibana"}
        )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "BuyingPowerUpdated"
    assert ev["request_id"] == "req-2"
    assert ev["venue"] == "tachibana"
    assert ev["cash_available"] == 1_000_000
    assert ev["cash_shortfall"] == 0
    assert ev["credit_available"] == 500_000
    assert isinstance(ev["ts_ms"], int)


# ---------------------------------------------------------------------------
# J-7 (HIGH): cash OK but credit raises TachibanaError → Error[INTERNAL_ERROR]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_buying_power_credit_fetch_fails_returns_internal_error():
    mock_session = MagicMock()
    server = _make_server(session=mock_session)

    cash = BuyingPowerResult(available_amount=300_000, shortfall=0)

    with patch(
        "engine.server.tachibana_fetch_buying_power", new_callable=AsyncMock, return_value=cash
    ), patch(
        "engine.server.tachibana_fetch_credit_buying_power",
        new_callable=AsyncMock,
        side_effect=TachibanaError("credit API unavailable"),
    ):
        await server._do_get_buying_power(
            {"op": "GetBuyingPower", "request_id": "req-3", "venue": "tachibana"}
        )

    assert len(server._outbox) == 1
    ev = server._outbox[0]
    assert ev["event"] == "Error"
    assert ev["request_id"] == "req-3"
    assert ev["code"] == "INTERNAL_ERROR"
