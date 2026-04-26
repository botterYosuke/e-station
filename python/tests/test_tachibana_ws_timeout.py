"""TDD: TachibanaEventWs dead-frame timeout (T5, plan §T5 M2 修正).

The implementation patches _DEAD_FRAME_TIMEOUT_S to a small value for speed.
"""

from __future__ import annotations

import asyncio

import pytest
import websockets.server  # type: ignore[import-untyped]
import websockets  # type: ignore[import-untyped]

import engine.exchanges.tachibana_ws as _ws_mod
from engine.exchanges.tachibana_ws import TachibanaEventWs


def _encode_fd(dv: str = "100") -> bytes:
    text = (
        "\x01p_evt_cmd\x02FD"
        "\x01p_1_DPP\x022500"
        f"\x01p_1_DV\x02{dv}"
        "\x01p_1_GAP1\x022501"
        "\x01p_1_GBP1\x022499"
        "\x01p_date\x022024.01.01-09:30:00.000"
    )
    return text.encode("shift_jis")


@pytest.mark.asyncio
async def test_no_frame_within_timeout_triggers_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dead-frame timeout causes reconnect (plan §T5 M2: 13s → disconnect)."""
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 0.2)
    monkeypatch.setattr(
        _ws_mod, "_BACKOFF_CAPS", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    )

    conn_count = 0
    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        nonlocal conn_count
        conn_count += 1
        if conn_count == 1:
            # First connection: send nothing for > timeout → dead
            await asyncio.sleep(1.0)
        else:
            # Second connection: send a frame and let test finish
            await ws.send(_encode_fd())
            stop.set()
            await asyncio.sleep(0.5)
            await ws.close()

    collected: list[str] = []

    async def _cb(frame_type: str, fields: dict, ts: int) -> None:
        collected.append(frame_type)

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ws_client = TachibanaEventWs(
            f"ws://127.0.0.1:{port}", stop, ticker="7203"
        )
        await asyncio.wait_for(ws_client.run(_cb), timeout=4.0)

    assert conn_count == 2, f"expected 2 connections (reconnect), got {conn_count}"
    assert "FD" in collected


@pytest.mark.asyncio
async def test_frame_before_timeout_keeps_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame arriving before the timeout resets the timer (plan §T5: 11s stays)."""
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 0.3)

    stop = asyncio.Event()
    conn_count = 0

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        nonlocal conn_count
        conn_count += 1
        # Send a frame just before timeout, then another, then close.
        await asyncio.sleep(0.1)   # < 0.3 s timeout
        await ws.send(_encode_fd())
        await asyncio.sleep(0.1)   # still < timeout
        await ws.send(_encode_fd(dv="110"))
        stop.set()
        await ws.close()

    received: list[str] = []

    async def _cb(frame_type: str, fields: dict, ts: int) -> None:
        received.append(frame_type)

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ws_client = TachibanaEventWs(
            f"ws://127.0.0.1:{port}", stop, ticker="7203"
        )
        await asyncio.wait_for(ws_client.run(_cb), timeout=3.0)

    # Only 1 connection: the early frame keeps it alive.
    assert conn_count == 1
    assert received.count("FD") == 2
