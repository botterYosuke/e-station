"""ExchangeWorker abstract base — Phase 1 boundary for future process isolation."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

OnSsidUpdate = Callable[[str], None]

_log = logging.getLogger(__name__)


def is_valid_ticker_entry(entry: dict[str, Any], *, venue: str) -> bool:
    """Validate Phase F invariants on a `TickerEntry` before it enters the IPC.

    Phase F made `min_ticksize` and `min_qty` required + `exclusiveMinimum: 0`
    in the JSON schema. Rust `StockTickerEntry` / `CryptoTickerEntry` deserialize
    them as plain `f32` with no `> 0` guard, so a single bad numeric value (e.g.
    a zero default from a venue API field omission) silently survives parse and
    later breaks downstream consumers (price-step bucketing, qty stepping).
    Worse, since Phase F also made `min_ticksize` required, an entry that lacks
    it altogether causes the entire `Vec<TickerEntry>` deserialization to fail
    on the Rust side, dropping every ticker for that venue with only a single
    `log::warn!` line.

    Returns True if `entry` may be emitted; False if it must be skipped.
    Logs a WARNING on rejection so the silent-loss mode is observable.
    """
    symbol = entry.get("symbol", "<missing>")
    min_ticksize = entry.get("min_ticksize")
    if min_ticksize is None or not (isinstance(min_ticksize, (int, float))) or min_ticksize <= 0:
        _log.warning(
            "%s list_tickers: skipping %s — invalid min_ticksize=%r",
            venue,
            symbol,
            min_ticksize,
        )
        return False
    min_qty = entry.get("min_qty")
    if min_qty is not None and (
        not isinstance(min_qty, (int, float)) or min_qty <= 0
    ):
        _log.warning(
            "%s list_tickers: skipping %s — invalid min_qty=%r",
            venue,
            symbol,
            min_qty,
        )
        return False
    return True


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

    async def prepare(self) -> None:
        """Eagerly initialize HTTP client + perform any cold-start work.

        Called once during the handshake `_handshake` before the engine emits
        `Ready` to the client. Workers should ensure that `list_tickers`,
        `fetch_ticker_stats`, `fetch_klines`, etc. can be served immediately
        on the next event-loop tick. Default is a no-op for workers that have
        nothing to warm up (e.g. test stubs).
        """
        return None

    def venue_caps(self) -> dict:
        """Per-venue capability flags for depth display and normalization.

        Subclasses should override to return accurate capabilities.
        Default: client_aggr_depth=True, supports_spread_display=True, qty_norm_kind="none"
        """
        return {
            "client_aggr_depth": True,
            "supports_spread_display": True,
            "qty_norm_kind": "none",
        }

    def capabilities(self) -> dict:
        """Per-venue capability advertisement (B3 / plan §T4 L508-549).

        The returned dict is merged into `Ready.capabilities.venue_capabilities[<venue>]`
        verbatim. Empty dict means "no venue-specific capability constraints" — the
        Rust UI then assumes every default-supported feature is available
        (capabilities-not-received => fail-open per B3 design note).

        Workers that *do* constrain features (e.g. Tachibana — only `"1d"`) must
        override and return the constraint set so the UI can pre-disable
        unsupported timeframes / features before the user clicks them.
        """
        return {}

    @abstractmethod
    async def set_proxy(self, url: str | None) -> None:
        """Apply a new proxy URL, closing any open HTTP client."""

    @abstractmethod
    async def list_tickers(self, market: str) -> list[dict]:
        """Return ticker metadata list for the given market type."""

    @abstractmethod
    async def fetch_klines(
        self,
        ticker: str,
        market: str,
        timeframe: str,
        *,
        limit: int = 400,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict]:
        """Return kline data as list of dicts."""

    @abstractmethod
    async def fetch_open_interest(
        self,
        ticker: str,
        market: str,
        timeframe: str,
        *,
        limit: int = 400,
        start_ms: int | None = None,
        end_ms: int | None = None,
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

    async def fetch_trades(
        self,
        ticker: str,
        market: str,
        start_ms: int,
        *,
        end_ms: int = 0,
        data_path: Path | None = None,
    ) -> list[dict]:
        """Fetch trades for one calendar day starting from start_ms."""
        raise NotImplementedError(f"{type(self).__name__} does not support fetch_trades")

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
