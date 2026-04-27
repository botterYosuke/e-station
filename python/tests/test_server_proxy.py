"""Tests for SetProxy behavior.

Fix 1: after SetProxy, all active streams are resubscribed through the new proxy.
Fix 2: in-flight fetch tasks cancelled by SetProxy emit an Error event instead of
       silently timing out.
Fix 3: SetProxy with the same URL (including None→None) is idempotent — active
       streams and in-flight fetches must NOT be cancelled when proxy is unchanged.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _connect_and_handshake(port: int, token: str) -> websockets.ClientConnection:
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    hello = {
        "op": "Hello",
        "schema_major": SCHEMA_MAJOR,
        "schema_minor": SCHEMA_MINOR,
        "client_version": "test",
        "token": token,
    }
    await ws.send(orjson.dumps(hello))
    raw = await ws.recv()
    msg = orjson.loads(raw)
    assert msg["event"] == "Ready", f"Expected Ready, got: {msg}"
    return ws


async def _never_ending_coro(*args, **kwargs) -> None:
    """Coroutine that blocks until cancelled — keeps stream tasks alive in _streams dict."""
    try:
        await asyncio.get_event_loop().create_future()
    except asyncio.CancelledError:
        raise


def _make_proxy_worker() -> MagicMock:
    """Mock worker with set_proxy support and never-ending stream coroutines.

    Streams must NOT complete immediately: if they finish before SetProxy arrives,
    the _on_done callback removes them from self._streams, leaving nothing to resubscribe.
    """
    worker = MagicMock()
    worker.set_proxy = AsyncMock()
    worker.stream_kline = MagicMock(side_effect=_never_ending_coro)
    worker.stream_trades = MagicMock(side_effect=_never_ending_coro)
    worker.stream_depth = MagicMock(side_effect=_never_ending_coro)
    worker.fetch_klines = AsyncMock(return_value=[])
    worker.fetch_trades = AsyncMock(return_value=[])
    worker.list_tickers = AsyncMock(return_value=[{"symbol": "BTCUSDT"}])
    return worker


_ALL_WORKER_CLASSES = [
    "BinanceWorker",
    "BybitWorker",
    "HyperliquidWorker",
    "MexcWorker",
    "OkexWorker",
    "TachibanaWorker",
]


@pytest.fixture
def unused_tcp_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def proxy_server(unused_tcp_port):
    """DataEngineServer on a random port with all venue workers replaced by a single mock."""
    from engine.server import DataEngineServer

    token = "proxy-test-token"
    worker = _make_proxy_worker()

    with ExitStack() as stack:
        for cls_name in _ALL_WORKER_CLASSES:
            stack.enter_context(
                patch(f"engine.server.{cls_name}", return_value=worker)
            )

        server = DataEngineServer(port=unused_tcp_port, token=token)
        serve_task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.05)

        yield unused_tcp_port, token, worker

        server.shutdown()
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):
            pass


# ── Fix 1: stream resubscription ──────────────────────────────────────────���──


@pytest.mark.asyncio
async def test_set_proxy_resubscribes_active_kline_stream(proxy_server):
    """SetProxy must reopen a live kline stream through the new proxy."""
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({
        "op": "Subscribe",
        "venue": "binance",
        "ticker": "BTCUSDT",
        "stream": "kline",
        "timeframe": "1m",
    }))
    await asyncio.sleep(0.05)
    assert worker.stream_kline.call_count == 1, "stream must start after Subscribe"

    await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy.example.com:8080"}))
    await asyncio.sleep(0.1)

    assert worker.stream_kline.call_count == 2, (
        "stream_kline must be called again after SetProxy to reopen through the new proxy"
    )
    await ws.close()


@pytest.mark.asyncio
async def test_set_proxy_resubscribes_trade_stream(proxy_server):
    """SetProxy must reopen a live trade stream."""
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({
        "op": "Subscribe",
        "venue": "binance",
        "ticker": "ETHUSDT",
        "stream": "trade",
    }))
    await asyncio.sleep(0.05)
    assert worker.stream_trades.call_count == 1

    await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy.example.com:8080"}))
    await asyncio.sleep(0.1)

    assert worker.stream_trades.call_count == 2
    await ws.close()


@pytest.mark.asyncio
async def test_set_proxy_resubscribes_multiple_streams(proxy_server):
    """All streams active at SetProxy time must be reopened — kline×2 + trade×1."""
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    for sub in [
        {"op": "Subscribe", "venue": "binance", "ticker": "BTCUSDT", "stream": "kline", "timeframe": "1m"},
        {"op": "Subscribe", "venue": "binance", "ticker": "BTCUSDT", "stream": "kline", "timeframe": "5m"},
        {"op": "Subscribe", "venue": "binance", "ticker": "ETHUSDT", "stream": "trade"},
    ]:
        await ws.send(orjson.dumps(sub))
    await asyncio.sleep(0.1)

    assert worker.stream_kline.call_count == 2
    assert worker.stream_trades.call_count == 1

    await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://new-proxy:3128"}))
    await asyncio.sleep(0.15)

    assert worker.stream_kline.call_count == 4, "both kline streams must restart"
    assert worker.stream_trades.call_count == 2, "trade stream must restart"
    await ws.close()


@pytest.mark.asyncio
async def test_set_proxy_with_no_streams_does_not_crash(proxy_server):
    """SetProxy when no streams are active must complete without error."""
    port, token, _ = proxy_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({"op": "SetProxy", "url": None}))
    await asyncio.sleep(0.1)

    # Connection must still be alive
    await ws.send(orjson.dumps({"op": "Ping", "request_id": "after-proxy"}))
    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Pong", f"Expected Pong, got: {msg}"
    await ws.close()


@pytest.mark.asyncio
async def test_set_proxy_calls_set_proxy_on_all_workers(proxy_server):
    """set_proxy must be forwarded to every venue worker, not just the subscribed one."""
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    new_url = "socks5://proxy.local:1080"
    await ws.send(orjson.dumps({"op": "SetProxy", "url": new_url}))
    await asyncio.sleep(0.1)

    # All workers share one mock object — one set_proxy call per venue.
    expected = len(_ALL_WORKER_CLASSES)
    assert worker.set_proxy.call_count == expected, (
        f"Expected {expected} set_proxy calls (one per venue), "
        f"got {worker.set_proxy.call_count}"
    )
    worker.set_proxy.assert_called_with(new_url)
    await ws.close()


@pytest.mark.asyncio
async def test_set_proxy_resubscribes_with_correct_ticker_and_timeframe(proxy_server):
    """Resubscription must preserve the original ticker and timeframe."""
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({
        "op": "Subscribe",
        "venue": "binance",
        "ticker": "SOLUSDT",
        "stream": "kline",
        "timeframe": "15m",
    }))
    await asyncio.sleep(0.05)

    first_args = worker.stream_kline.call_args_list[0].args

    await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy.example.com:8080"}))
    await asyncio.sleep(0.1)

    second_args = worker.stream_kline.call_args_list[1].args

    # args: (ticker, market, timeframe, base_ssid, outbox, stop, ...)
    assert first_args[0] == second_args[0], "ticker must be preserved across resubscription"
    assert first_args[2] == second_args[2], "timeframe must be preserved across resubscription"
    await ws.close()


@pytest.mark.asyncio
async def test_set_proxy_clears_then_reopens_stream_not_duplicating(proxy_server):
    """After SetProxy, the stream count must be exactly 2 (cancel + reopen), not 3+."""
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({
        "op": "Subscribe",
        "venue": "binance",
        "ticker": "BTCUSDT",
        "stream": "kline",
        "timeframe": "1m",
    }))
    await asyncio.sleep(0.05)

    # Two rapid SetProxy calls — each should cancel-and-reopen once
    await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy1:8080"}))
    await asyncio.sleep(0.1)
    await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy2:8080"}))
    await asyncio.sleep(0.1)

    # 1 (initial) + 1 (first proxy) + 1 (second proxy) = 3
    assert worker.stream_kline.call_count == 3, (
        f"Expected 3 stream_kline calls (initial + 2 resubscriptions), "
        f"got {worker.stream_kline.call_count}"
    )
    await ws.close()


# ── Fix 3: idempotent SetProxy does not cancel streams ────────────────────────


@pytest.mark.asyncio
async def test_set_proxy_none_when_already_none_does_not_cancel_streams(proxy_server):
    """SetProxy(None) when proxy is already None must NOT restart active streams.

    Regression test for: _handle_set_proxy always calling _cancel_all_streams(),
    even when the proxy URL had not changed (None → None on every EngineConnected).

    Fix: server tracks _proxy_url; if url == _proxy_url the handler returns early.
    Without this fix, every engine reconnect would cancel all in-flight startup
    fetches via the unconditional _cancel_all_streams() call.
    """
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({
        "op": "Subscribe",
        "venue": "binance",
        "ticker": "BTCUSDT",
        "stream": "kline",
        "timeframe": "1m",
    }))
    await asyncio.sleep(0.05)
    assert worker.stream_kline.call_count == 1, "stream must start after Subscribe"

    # Send SetProxy(None) — proxy was already None (default state, no change)
    await ws.send(orjson.dumps({"op": "SetProxy", "url": None}))
    await asyncio.sleep(0.1)

    assert worker.stream_kline.call_count == 1, (
        "SetProxy(None) when proxy is already None must NOT restart streams.\n"
        "Fix: add 'if proxy_url == self._proxy_url: return' guard in "
        "_handle_set_proxy() in python/engine/server.py."
    )
    await ws.close()


@pytest.mark.asyncio
async def test_set_proxy_same_url_twice_does_not_double_restart(proxy_server):
    """Sending the same non-None proxy URL twice must only restart streams once.

    Regression guard: idempotency check applies to non-None URLs too.
    """
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({
        "op": "Subscribe",
        "venue": "binance",
        "ticker": "BTCUSDT",
        "stream": "kline",
        "timeframe": "1m",
    }))
    await asyncio.sleep(0.05)
    assert worker.stream_kline.call_count == 1

    proxy_url = "http://proxy.example.com:8080"
    # First SetProxy — URL changes (None → proxy_url): stream must restart
    await ws.send(orjson.dumps({"op": "SetProxy", "url": proxy_url}))
    await asyncio.sleep(0.1)
    assert worker.stream_kline.call_count == 2, "first SetProxy must restart stream"

    # Second SetProxy with same URL — no change: stream must NOT restart again
    await ws.send(orjson.dumps({"op": "SetProxy", "url": proxy_url}))
    await asyncio.sleep(0.1)
    assert worker.stream_kline.call_count == 2, (
        "SetProxy with the same URL must be idempotent — stream must not restart.\n"
        "Fix: 'if proxy_url == self._proxy_url: return' in _handle_set_proxy()."
    )
    await ws.close()


# ── Fix 2: cancelled fetch emits Error event ─────────────────────────────────


@pytest.mark.asyncio
async def test_set_proxy_cancels_inflight_fetch_klines_emits_error(unused_tcp_port):
    """In-flight FetchKlines cancelled by SetProxy must emit Error(code='cancelled').

    Before Fix 2, the client would time out (10–60 s) with no response.
    Now it must receive an immediate Error event.
    """
    from engine.server import DataEngineServer

    token = "cancel-klines-token"
    worker = _make_proxy_worker()

    # Block indefinitely so the task is still in-flight when SetProxy arrives.
    # Use MagicMock + async side_effect so the mock returns an awaitable coroutine.
    async def _blocking_fetch(*args, **kwargs):
        await asyncio.get_event_loop().create_future()  # never resolves until cancelled

    worker.fetch_klines = MagicMock(side_effect=_blocking_fetch)

    with ExitStack() as stack:
        for cls_name in _ALL_WORKER_CLASSES:
            stack.enter_context(
                patch(f"engine.server.{cls_name}", return_value=worker)
            )

        server = DataEngineServer(port=unused_tcp_port, token=token)
        serve_task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.05)

        try:
            ws = await _connect_and_handshake(unused_tcp_port, token)

            await ws.send(orjson.dumps({
                "op": "FetchKlines",
                "request_id": "fetch-klines-001",
                "venue": "binance",
                "ticker": "BTCUSDT",
                "timeframe": "1h",
                "limit": 100,
            }))
            await asyncio.sleep(0.05)  # let the fetch task start and block

            await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy:8080"}))

            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = orjson.loads(raw)

            assert msg.get("event") == "Error", f"Expected Error event, got: {msg}"
            assert msg.get("request_id") == "fetch-klines-001", "Error must carry the original request_id"
            assert msg.get("code") == "cancelled", f"Expected code='cancelled', got: {msg.get('code')}"

            await ws.close()
        finally:
            server.shutdown()
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_set_proxy_cancels_inflight_fetch_trades_emits_error(unused_tcp_port):
    """In-flight FetchTrades cancelled by SetProxy must emit Error(code='cancelled')."""
    from engine.server import DataEngineServer

    token = "cancel-trades-token"
    worker = _make_proxy_worker()

    async def _blocking_fetch(*args, **kwargs):
        await asyncio.get_event_loop().create_future()

    worker.fetch_trades = MagicMock(side_effect=_blocking_fetch)

    with ExitStack() as stack:
        for cls_name in _ALL_WORKER_CLASSES:
            stack.enter_context(
                patch(f"engine.server.{cls_name}", return_value=worker)
            )

        server = DataEngineServer(port=unused_tcp_port, token=token)
        serve_task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.05)

        try:
            ws = await _connect_and_handshake(unused_tcp_port, token)

            await ws.send(orjson.dumps({
                "op": "FetchTrades",
                "request_id": "fetch-trades-002",
                "venue": "binance",
                "ticker": "ETHUSDT",
                "start_ms": 1_700_000_000_000,
                "end_ms": 1_700_086_400_000,
            }))
            await asyncio.sleep(0.05)

            await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy:8080"}))

            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = orjson.loads(raw)

            assert msg.get("event") == "Error"
            assert msg.get("request_id") == "fetch-trades-002"
            assert msg.get("code") == "cancelled"

            await ws.close()
        finally:
            server.shutdown()
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_error_event_contains_human_readable_message(unused_tcp_port):
    """Cancelled Error event must include a non-empty human-readable message."""
    from engine.server import DataEngineServer

    token = "cancel-msg-token"
    worker = _make_proxy_worker()

    async def _blocking_fetch(*args, **kwargs):
        await asyncio.get_event_loop().create_future()

    worker.fetch_klines = MagicMock(side_effect=_blocking_fetch)

    with ExitStack() as stack:
        for cls_name in _ALL_WORKER_CLASSES:
            stack.enter_context(
                patch(f"engine.server.{cls_name}", return_value=worker)
            )

        server = DataEngineServer(port=unused_tcp_port, token=token)
        serve_task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.05)

        try:
            ws = await _connect_and_handshake(unused_tcp_port, token)

            await ws.send(orjson.dumps({
                "op": "FetchKlines",
                "request_id": "req-msg",
                "venue": "binance",
                "ticker": "BTCUSDT",
                "timeframe": "5m",
                "limit": 50,
            }))
            await asyncio.sleep(0.05)

            await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy:8080"}))

            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = orjson.loads(raw)

            assert msg.get("message"), "cancelled Error must include a non-empty message string"

            await ws.close()
        finally:
            server.shutdown()
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_fetch_started_after_proxy_change_succeeds(proxy_server):
    """A fetch issued after SetProxy completes must still return results normally."""
    port, token, worker = proxy_server
    ws = await _connect_and_handshake(port, token)

    # Change proxy with no in-flight fetches
    await ws.send(orjson.dumps({"op": "SetProxy", "url": "http://proxy:3128"}))
    await asyncio.sleep(0.1)

    await ws.send(orjson.dumps({
        "op": "ListTickers",
        "request_id": "tickers-post-proxy",
        "venue": "binance",
    }))
    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "TickerInfo", f"Expected TickerInfo, got: {msg}"
    assert msg.get("request_id") == "tickers-post-proxy"
    await ws.close()
