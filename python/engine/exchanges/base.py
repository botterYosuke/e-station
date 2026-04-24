"""ExchangeWorker abstract base — Phase 1 boundary for future process isolation."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable

OnSsidUpdate = Callable[[str], None]


class WsNativeResyncTriggered(Exception):
    """Raised by fetch_depth_snapshot() when the venue cannot provide a REST snapshot.

    The worker has already signalled its live WS stream to reconnect; Rust will
    receive a fresh DepthSnapshot via the stream once the WS reconnects.
    """


class ExchangeWorker(ABC):
    """Abstract exchange worker.

    Phase 1 runs all workers in-process inside asyncio.
    The inbox/outbox queues form the boundary: if we later split into
    subprocesses, only this interface needs to change.
    """

    @abstractmethod
    async def set_proxy(self, url: str | None) -> None:
        """Apply a new proxy URL, closing any open HTTP client."""

    @abstractmethod
    async def list_tickers(self, market: str) -> list[dict]:
        """Return ticker metadata list for the given market type."""

    @abstractmethod
    async def fetch_klines(
        self, ticker: str, market: str, timeframe: str, *, limit: int = 400
    ) -> list[dict]:
        """Return kline data as list of dicts."""

    @abstractmethod
    async def fetch_open_interest(
        self, ticker: str, market: str, timeframe: str, *, limit: int = 400
    ) -> list[dict]:
        """Return open interest history as list of dicts."""

    @abstractmethod
    async def fetch_ticker_stats(self, ticker: str, market: str) -> dict:
        """Return 24h ticker statistics."""

    @abstractmethod
    async def fetch_depth_snapshot(self, ticker: str, market: str) -> dict:
        """Fetch a full order book snapshot."""

    @abstractmethod
    async def stream_trades(
        self,
        ticker: str,
        market: str,
        stream_session_id: str,
        outbox: list[dict],
        stop_event: asyncio.Event,
        *,
        on_ssid: OnSsidUpdate | None = None,
    ) -> None:
        """Push trade batch events into outbox until stop_event is set."""

    @abstractmethod
    async def stream_depth(
        self,
        ticker: str,
        market: str,
        stream_session_id: str,
        outbox: list[dict],
        stop_event: asyncio.Event,
        *,
        on_ssid: OnSsidUpdate | None = None,
    ) -> None:
        """Push depth diff/snapshot/gap events into outbox until stop_event is set."""

    @abstractmethod
    async def stream_kline(
        self,
        ticker: str,
        market: str,
        timeframe: str,
        stream_session_id: str,
        outbox: list[dict],
        stop_event: asyncio.Event,
        *,
        on_ssid: OnSsidUpdate | None = None,
    ) -> None:
        """Push kline update events into outbox until stop_event is set."""
