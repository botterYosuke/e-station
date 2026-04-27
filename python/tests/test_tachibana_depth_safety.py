"""TDD: depth_unavailable safety (T5, plan §T5 MEDIUM-6 / F-M12 / HIGH-D4).

depth_unavailable fires when stream_depth receives FD frames with no bid/ask
keys for _DEPTH_SAFETY_TIMEOUT_S seconds.  When bid/ask keys arrive in time,
the safety must NOT fire.

Tests use a real websockets.serve mock server and patch _DEPTH_SAFETY_TIMEOUT_S
to a small value for speed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import websockets
import websockets.server  # type: ignore[import-untyped]

import engine.exchanges.tachibana_ws as _ws_mod
from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_session(ws_port: int) -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws=f"ws://127.0.0.1:{ws_port}/event/",
        zyoutoeki_kazei_c="",
    )


def _make_worker(tmp_path: Path) -> TachibanaWorker:
    return TachibanaWorker(cache_dir=tmp_path, is_demo=True)


def _fd_no_depth() -> bytes:
    """FD frame WITHOUT bid/ask keys (no GAP/GBP)."""
    text = (
        "\x01p_cmd\x02FD"
        "\x01p_1_DPP\x022500"
        "\x01p_1_DV\x02100"
        "\x01p_date\x022024.01.01-09:30:00.000"
    )
    return text.encode("shift_jis")


def _fd_with_depth() -> bytes:
    """FD frame WITH bid/ask keys."""
    text = (
        "\x01p_cmd\x02FD"
        "\x01p_1_DPP\x022500"
        "\x01p_1_DV\x02100"
        "\x01p_1_GAP1\x022501"
        "\x01p_1_GBP1\x022499"
        "\x01p_1_GAV1\x02100"
        "\x01p_1_GBV1\x02100"
        "\x01p_date\x022024.01.01-09:30:00.000"
    )
    return text.encode("shift_jis")


# ---------------------------------------------------------------------------
# HIGH-D4: negative test — safety does NOT fire when keys arrive in time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_safety_does_not_fire_when_keys_arrive_within_30s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bid/ask keys arriving before the timeout must NOT emit depth_unavailable.

    plan §HIGH-D4: VenueError{code:"depth_unavailable"} not emitted,
    fetch_depth_snapshot call count == 0.
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.3)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        # Send FD WITHOUT depth at t=0 (initialises DV state)
        await ws.send(_fd_no_depth())
        # Wait a bit, then send FD WITH depth — before 0.3 s timeout
        await asyncio.sleep(0.1)
        await ws.send(_fd_with_depth())
        # Let the test finish
        stop.set()
        await asyncio.sleep(0.5)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []

        with (
            patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True),
            patch.object(
                worker, "fetch_depth_snapshot", new_callable=lambda: lambda *a, **kw: AsyncMock(return_value={})()
            ) as mock_snap,
        ):
            mock_snap = AsyncMock(return_value={})
            worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-1", outbox, stop),
                timeout=3.0,
            )

    depth_errors = [
        e for e in outbox
        if e.get("event") == "VenueError" and e.get("code") == "depth_unavailable"
    ]
    assert not depth_errors, f"depth_unavailable should not fire; got: {depth_errors}"
    assert mock_snap.call_count == 0, (
        f"fetch_depth_snapshot must not be called; called {mock_snap.call_count} times"
    )


# ---------------------------------------------------------------------------
# MEDIUM-6 / F-M12: positive test — safety fires when no keys within timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_safety_fires_when_no_keys_within_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bid/ask keys within timeout → VenueError{code:'depth_unavailable'} emitted.

    plan §MEDIUM-6 / F-M12.  Poll constants are also patched so stream_depth
    returns quickly after the safety fires.
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 0.15)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(_ws_mod, "_DEPTH_POLL_MAX_S", 0.1)  # exits after ~2 polls

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        # Keep sending FD frames WITHOUT depth keys well past the timeout
        for _ in range(10):
            await ws.send(_fd_no_depth())
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)

        outbox: list[dict] = []
        mock_snap = AsyncMock(return_value={})
        worker.fetch_depth_snapshot = mock_snap  # type: ignore[method-assign]

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-1", outbox, stop),
                timeout=5.0,
            )

    depth_errors = [
        e for e in outbox
        if e.get("event") == "VenueError" and e.get("code") == "depth_unavailable"
    ]
    assert len(depth_errors) == 1, (
        f"exactly 1 depth_unavailable expected; got {len(depth_errors)}: {depth_errors}"
    )
    assert depth_errors[0].get("venue") == "tachibana"
    assert "message" in depth_errors[0]
