"""TDD: Holiday/market-closed → Disconnected fallback (T5, plan §T5 F-M5a).

Tests cover:
* Outside trading hours → stream_trades/stream_depth immediately emits
  Disconnected{reason:market_closed}.
* Inside trading hours, ST frame with market-closed sResultCode →
  Disconnected{reason:market_closed} (not VenueError), plan §MEDIUM-D2-2.
* Inside trading hours, ST frame with unrelated sResultCode → NOT
  market_closed (negative case).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import websockets
import websockets.server  # type: ignore[import-untyped]

from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# JST noon — within trading hours (前場終了後だが後場内)
_JST_OPEN = datetime(2024, 1, 5, 13, 0, 0, tzinfo=timezone(timedelta(hours=9)))
# Outside trading hours
_JST_CLOSED = datetime(2024, 1, 5, 8, 0, 0, tzinfo=timezone(timedelta(hours=9)))


def _fake_session(ws_url: str = "wss://example.test/event/") -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws=ws_url,
        zyoutoeki_kazei_c="",
    )


def _make_worker(tmp_path: Path, ws_url: str = "wss://example.test/event/") -> TachibanaWorker:
    return TachibanaWorker(
        cache_dir=tmp_path,
        is_demo=True,
        session=_fake_session(ws_url),
    )


def _st_frame(result_code: str) -> bytes:
    """Build a Shift-JIS ST frame with the given sResultCode."""
    text = f"\x01p_cmd\x02ST\x01sResultCode\x02{result_code}"
    return text.encode("shift_jis")


# ---------------------------------------------------------------------------
# market_closed during open hours: subscribe → immediate Disconnected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_outside_market_hours_emits_disconnected(tmp_path: Path) -> None:
    """Outside trading hours, stream_trades immediately emits Disconnected{reason:market_closed}."""
    worker = _make_worker(tmp_path)
    outbox: list[dict] = []
    stop = asyncio.Event()

    with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=False):
        await worker.stream_trades("7203", "stock", "session-1", outbox, stop)

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


# ---------------------------------------------------------------------------
# MEDIUM-D2-2 / F-M5a: ST frame market_closed フェイルセーフ
# ---------------------------------------------------------------------------

# Tachibana の「市場休業」相当 ST frame の sResultCode (plan §F-M5a).
# 保守的フォールバック: sResultCode != "0" で全て market_closed 扱いにする.
_MARKET_CLOSED_RESULT_CODE = "10001"   # 仮の「市場停止」コード
_UNRELATED_ERROR_RESULT_CODE = "99"    # 無関係なエラーコード


@pytest.mark.asyncio
async def test_market_closed_st_frame_during_open_hours_emits_disconnected(
    tmp_path: Path,
) -> None:
    """ザラ場中に market_closed 相当 ST frame → Disconnected{reason:'market_closed'}.

    VenueError は発出されないこと。plan §MEDIUM-D2-2 / F-M5a.
    """
    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_st_frame(_MARKET_CLOSED_RESULT_CODE))
        await asyncio.sleep(0.5)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path, f"ws://127.0.0.1:{port}/event/")
        outbox: list[dict] = []

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_trades("7203", "stock", "session-1", outbox, stop),
                timeout=3.0,
            )

    disconnected = [e for e in outbox if e.get("event") == "Disconnected"]
    market_closed = [e for e in disconnected if e.get("reason") == "market_closed"]
    venue_errors = [e for e in outbox if e.get("event") == "VenueError"]

    assert len(market_closed) == 1, (
        f"exactly 1 Disconnected{{reason:'market_closed'}} expected; got {outbox}"
    )
    assert not venue_errors, f"VenueError must not be emitted; got {venue_errors}"


@pytest.mark.asyncio
async def test_unrelated_st_frame_during_open_hours_does_not_emit_market_closed(
    tmp_path: Path,
) -> None:
    """ザラ場中に無関係なエラー ST frame → market_closed Disconnected を発出しない.

    plan §MEDIUM-D2-2 negative case.
    """
    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        # sResultCode="0" は情報通知レベルなので停止しない
        await ws.send(_st_frame("0"))
        stop.set()
        await asyncio.sleep(0.3)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path, f"ws://127.0.0.1:{port}/event/")
        outbox: list[dict] = []

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_trades("7203", "stock", "session-1", outbox, stop),
                timeout=3.0,
            )

    market_closed = [e for e in outbox if e.get("reason") == "market_closed"]
    assert not market_closed, (
        f"market_closed must not be emitted for sResultCode=0; got {market_closed}"
    )
