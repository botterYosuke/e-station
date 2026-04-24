"""Binance exchange worker — skeleton for Phase 1."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class BinanceWorker:
    """Handles Binance REST and WebSocket data acquisition.

    Phase 1 skeleton — methods will be implemented incrementally.
    """

    BASE_REST = "https://fapi.binance.com"
    BASE_WS = "wss://fstream.binance.com"

    def __init__(self) -> None:
        pass

    async def list_tickers(self) -> list[dict]:
        raise NotImplementedError

    async def get_ticker_metadata(self, ticker: str) -> dict:
        raise NotImplementedError

    async def fetch_klines(self, ticker: str, timeframe: str, limit: int) -> list[dict]:
        raise NotImplementedError

    async def fetch_open_interest(self, ticker: str, timeframe: str, limit: int) -> list[dict]:
        raise NotImplementedError

    async def fetch_ticker_stats(self, ticker: str) -> dict:
        raise NotImplementedError
