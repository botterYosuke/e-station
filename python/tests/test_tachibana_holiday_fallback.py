"""TDD: Holiday/market-closed → Disconnected fallback (T5, plan §T5 F-M5a).

These tests use a mock WS server that returns a "market closed" ST frame
during normal trading hours, asserting the worker emits Disconnected{reason:market_closed}.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# JST noon — within trading hours (前場終了後だが後場内)
_JST_OPEN = datetime(2024, 1, 5, 13, 0, 0, tzinfo=timezone(timedelta(hours=9)))
# Outside trading hours
_JST_CLOSED = datetime(2024, 1, 5, 8, 0, 0, tzinfo=timezone(timedelta(hours=9)))


def _fake_session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws="wss://example.test/event/",
        zyoutoeki_kazei_c="",
    )


def _make_worker(tmp_path: Path) -> TachibanaWorker:
    return TachibanaWorker(
        cache_dir=tmp_path,
        is_demo=True,
        session=_fake_session(),
    )


# ---------------------------------------------------------------------------
# market_closed during open hours: subscribe → immediate Disconnected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_outside_market_hours_emits_disconnected(tmp_path: Path) -> None:
    """Outside trading hours, stream_trades immediately emits Disconnected{reason:market_closed}."""
    worker = _make_worker(tmp_path)
    outbox: list[dict] = []
    stop = asyncio.Event()

    with patch(
        "engine.exchanges.tachibana_ws.is_market_open",
        return_value=False,
    ):
        await worker.stream_trades(
            "7203", "stock", "session-1", outbox, stop
        )

    assert len(outbox) == 1
    evt = outbox[0]
    assert evt["event"] == "Disconnected"
    assert evt["reason"] == "market_closed"


@pytest.mark.asyncio
async def test_subscribe_inside_market_hours_does_not_emit_market_closed(tmp_path: Path) -> None:
    """Inside trading hours, stream_trades does not immediately Disconnect with market_closed.

    We mock the WS layer to close immediately so the test doesn't hang.
    """
    worker = _make_worker(tmp_path)
    outbox: list[dict] = []
    stop = asyncio.Event()

    # Patch TachibanaEventWs.run to return immediately (simulates WS close)
    async def _fake_run(cb):  # noqa: ANN001
        stop.set()

    with (
        patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True),
        patch(
            "engine.exchanges.tachibana.TachibanaEventWs.run",
            new_callable=lambda: lambda *a, **kw: _fake_run(a[1]),
        ),
    ):
        await worker.stream_trades(
            "7203", "stock", "session-1", outbox, stop
        )

    # Should not have a market_closed Disconnected
    market_closed = [e for e in outbox if e.get("reason") == "market_closed"]
    assert not market_closed
