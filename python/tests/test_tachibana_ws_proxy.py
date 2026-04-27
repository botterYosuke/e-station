"""TDD: SetProxy + WS integration (T5, plan §F-M3a / MEDIUM-D5).

Verifies that TachibanaEventWs passes proxy=<url> to websockets.connect when
a proxy is configured.  Uses a pure-asyncio CONNECT proxy so no external
dependencies are required.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import websockets
import websockets.server  # type: ignore[import-untyped]

from engine.exchanges.tachibana_ws import TachibanaEventWs


# ---------------------------------------------------------------------------
# Minimal asyncio HTTP CONNECT proxy
# ---------------------------------------------------------------------------


async def _proxy_pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _proxy_handler(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    connect_count: list[int],
) -> None:
    """Handle one HTTP CONNECT request, then transparently proxy data."""
    try:
        # Read the CONNECT request line
        line = (await client_reader.readline()).decode(errors="replace").strip()
        m = re.match(r"CONNECT\s+([^:]+):(\d+)\s+HTTP/", line)
        if not m:
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
            return

        host, port = m.group(1), int(m.group(2))

        # Drain remaining headers
        while True:
            hdr = await client_reader.readline()
            if hdr in (b"\r\n", b"\n", b""):
                break

        # Connect to the real target
        target_reader, target_writer = await asyncio.open_connection(host, port)
        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        connect_count[0] += 1

        # Bidirectional pipe
        await asyncio.gather(
            _proxy_pipe(client_reader, target_writer),
            _proxy_pipe(target_reader, client_writer),
            return_exceptions=True,
        )
    except Exception:
        pass
    finally:
        try:
            client_writer.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MEDIUM-D5: positive path — WS connects through local CONNECT proxy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_connects_through_local_connect_proxy() -> None:
    """TachibanaEventWs connects through a CONNECT proxy and receives a frame.

    plan §MEDIUM-D5 / F-M3a positive path.
    """
    stop = asyncio.Event()
    connect_count: list[int] = [0]
    received: list[str] = []

    # Start the WS target server
    async def _ws_handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        text = "\x01p_cmd\x02KP"
        await ws.send(text.encode("shift_jis"))
        stop.set()
        await asyncio.sleep(0.5)
        await ws.close()

    async with websockets.serve(_ws_handler, "127.0.0.1", 0) as ws_server:
        ws_port = ws_server.sockets[0].getsockname()[1]

        # Start the CONNECT proxy
        async def _make_handler(r, w):  # noqa: ANN001
            await _proxy_handler(r, w, connect_count=connect_count)

        proxy_server = await asyncio.start_server(_make_handler, "127.0.0.1", 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]

        try:
            proxy_url = f"http://127.0.0.1:{proxy_port}"
            ws_url = f"ws://127.0.0.1:{ws_port}"

            async def _cb(frame_type: str, fields: dict, ts: int) -> None:
                received.append(frame_type)

            client = TachibanaEventWs(ws_url, stop, ticker="7203", proxy=proxy_url)
            await asyncio.wait_for(client.run(_cb), timeout=4.0)
        finally:
            proxy_server.close()
            await proxy_server.wait_closed()

    assert connect_count[0] >= 1, "CONNECT proxy should have been used"
    assert "KP" in received, f"KP frame should have been received; got {received}"


# ---------------------------------------------------------------------------
# F-M3a: proxy kwarg is forwarded to websockets.connect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_kwarg_forwarded_to_websockets_connect() -> None:
    """When proxy is set, websockets.connect receives proxy= kwarg.

    plan §F-M3a — unit-level check that the plumbing is correct.
    websockets.connect() is NOT a coroutine; it returns a CM synchronously.
    So side_effect must be a plain (sync) function.
    """
    stop = asyncio.Event()
    captured_kwargs: list[dict] = []

    def _fake_connect(url, **kwargs):  # noqa: ANN001  sync!
        captured_kwargs.append(kwargs)
        stop.set()
        raise ConnectionRefusedError("mock: no server")

    proxy_url = "http://proxy.example.test:8080"

    with patch("websockets.connect", side_effect=_fake_connect):
        client = TachibanaEventWs(
            "ws://127.0.0.1:19999",
            stop,
            ticker="7203",
            proxy=proxy_url,
        )
        try:
            await asyncio.wait_for(client.run(AsyncMock()), timeout=2.0)
        except Exception:
            pass

    assert any(
        kw.get("proxy") == proxy_url for kw in captured_kwargs
    ), f"proxy kwarg not forwarded; captured: {captured_kwargs}"


@pytest.mark.asyncio
async def test_no_proxy_does_not_pass_proxy_kwarg() -> None:
    """When proxy is None, websockets.connect does NOT receive proxy= kwarg.

    plan §F-M3a — absence test so we don't accidentally override auto-detect.
    """
    stop = asyncio.Event()
    captured_kwargs: list[dict] = []

    def _fake_connect(url, **kwargs):  # noqa: ANN001  sync!
        captured_kwargs.append(kwargs)
        stop.set()
        raise ConnectionRefusedError("mock: no server")

    with patch("websockets.connect", side_effect=_fake_connect):
        client = TachibanaEventWs(
            "ws://127.0.0.1:19999",
            stop,
            ticker="7203",
            proxy=None,
        )
        try:
            await asyncio.wait_for(client.run(AsyncMock()), timeout=2.0)
        except Exception:
            pass

    assert len(captured_kwargs) >= 1, "websockets.connect should have been called"
    assert all(
        "proxy" not in kw for kw in captured_kwargs
    ), f"proxy kwarg must not be passed when proxy=None; captured: {captured_kwargs}"
