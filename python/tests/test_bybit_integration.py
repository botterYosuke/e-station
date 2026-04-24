"""Integration tests for Bybit depth snapshot and trade stream correctness.

Covers two bugs identified during phase-3 review:
  1. REST fetch_depth_snapshot is incompatible with orderbook.200 namespace.
     Must raise WsNativeResyncTriggered and set the reconnect trigger.
  2. publicTrade BT field means "block trade", not liquidation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from engine.exchanges.base import WsNativeResyncTriggered
from engine.exchanges.bybit import BybitWorker


@pytest.fixture
def worker() -> BybitWorker:
    return BybitWorker()


# ---------------------------------------------------------------------------
# Fix 1 – RequestDepthSnapshot / fetch_depth_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_raises_ws_native_resync(worker: BybitWorker) -> None:
    """REST orderbook u is 1000-level namespace; orderbook.200 resync is WS-native."""
    with pytest.raises(WsNativeResyncTriggered, match="orderbook.200"):
        await worker.fetch_depth_snapshot("BTCUSDT", "linear_perp")

    # No active stream → trigger should NOT be created (avoids orphaned dict entries).
    assert ("BTCUSDT", "linear_perp") not in worker._reconnect_triggers


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_sets_trigger_when_stream_active(worker: BybitWorker) -> None:
    """Trigger is set only when stream_depth has already registered the key."""
    # Simulate an active stream_depth by pre-registering the trigger.
    trigger = worker._reconnect_trigger("BTCUSDT", "linear_perp")
    assert not trigger.is_set()

    with pytest.raises(WsNativeResyncTriggered):
        await worker.fetch_depth_snapshot("BTCUSDT", "linear_perp")

    assert trigger.is_set()


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_raises_for_inverse(worker: BybitWorker) -> None:
    with pytest.raises(WsNativeResyncTriggered):
        await worker.fetch_depth_snapshot("BTCUSD", "inverse_perp")

    # No active stream → no orphaned entry created.
    assert ("BTCUSD", "inverse_perp") not in worker._reconnect_triggers


# ---------------------------------------------------------------------------
# Fix 2 – publicTrade BT field is "block trade", not liquidation
# ---------------------------------------------------------------------------


class _FakeWS:
    """Async context manager / async iterator that yields preset raw messages."""

    def __init__(self, messages: list[bytes], stop: asyncio.Event) -> None:
        self._messages = messages
        self._stop = stop
        self._index = 0

    async def __aenter__(self) -> "_FakeWS":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def send(self, _: str) -> None:
        pass

    def __aiter__(self) -> "_FakeWS":
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._messages):
            # Signal caller to stop before suspending forever
            self._stop.set()
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg


def _trade_ws_msg(*, bt: bool, side: str = "Buy") -> bytes:
    return orjson.dumps(
        {
            "topic": "publicTrade.BTCUSDT",
            "data": [{"p": "50000.0", "v": "1.0", "S": side, "T": 1_700_000_000_000, "BT": bt}],
        }
    )


async def _run_stream_trades(
    worker: BybitWorker, messages: list[bytes]
) -> list[dict]:
    outbox: list[dict] = []
    stop = asyncio.Event()
    fake_ws = _FakeWS(messages, stop)

    with patch("engine.exchanges.bybit.websockets.connect", return_value=fake_ws):
        try:
            await asyncio.wait_for(
                worker.stream_trades("BTCUSDT", "linear_perp", "sess:1", outbox, stop),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            pass

    return [t for ev in outbox if ev["event"] == "Trades" for t in ev["trades"]]


@pytest.mark.asyncio
async def test_block_trade_bt_true_not_liquidation(worker: BybitWorker) -> None:
    """BT=True is a block trade marker, not a liquidation flag."""
    trades = await _run_stream_trades(worker, [_trade_ws_msg(bt=True)])
    assert trades, "expected at least one trade in outbox"
    assert all(not t["is_liquidation"] for t in trades)


@pytest.mark.asyncio
async def test_normal_trade_bt_false_not_liquidation(worker: BybitWorker) -> None:
    """Ordinary trades (BT absent / False) should also have is_liquidation=False."""
    msg = orjson.dumps(
        {
            "topic": "publicTrade.BTCUSDT",
            "data": [{"p": "49000.0", "v": "0.5", "S": "Sell", "T": 1_700_000_000_001}],
        }
    )
    trades = await _run_stream_trades(worker, [msg])
    assert trades
    assert all(not t["is_liquidation"] for t in trades)


@pytest.mark.asyncio
async def test_multiple_trades_none_liquidation(worker: BybitWorker) -> None:
    """A batch containing both BT and non-BT trades must all have is_liquidation=False."""
    msg = orjson.dumps(
        {
            "topic": "publicTrade.BTCUSDT",
            "data": [
                {"p": "50000.0", "v": "2.0", "S": "Buy", "T": 1_700_000_000_002, "BT": True},
                {"p": "50001.0", "v": "0.3", "S": "Sell", "T": 1_700_000_000_003, "BT": False},
                {"p": "49999.0", "v": "1.1", "S": "Buy", "T": 1_700_000_000_004},
            ],
        }
    )
    trades = await _run_stream_trades(worker, [msg])
    assert len(trades) == 3
    assert all(not t["is_liquidation"] for t in trades)
