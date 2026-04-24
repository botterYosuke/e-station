"""MEXC exchange worker — REST and WebSocket for Phase 3."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

import httpx
import orjson
import websockets

from engine.exchanges.base import ExchangeWorker, OnSsidUpdate
from engine.limiter import TokenBucket

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint constants
# ---------------------------------------------------------------------------

_REST_V3 = "https://api.mexc.com/api/v3"
_REST_V1 = "https://api.mexc.com/api/v1/contract"
_WS_FUTURES = "wss://contract.mexc.com/edge"

_TRADE_BATCH_INTERVAL = 0.033  # 33 ms

# MEXC: 10 requests per 2 seconds
_MEXC_CAPACITY = 10
_MEXC_REFILL_RATE = _MEXC_CAPACITY / 2.0  # 5 req/sec

# Timeframe → MEXC interval string
_KLINE_SPOT: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "4h",
    "1d": "1d",
}

_KLINE_FUTURES: dict[str, str] = {
    "1m": "Min1",
    "5m": "Min5",
    "15m": "Min15",
    "30m": "Min30",
    "1h": "Min60",
    "4h": "Hour4",
    "1d": "Day1",
}

# Reverse map: interval string → timeframe (for kline stream)
_INTERVAL_TO_TIMEFRAME: dict[str, str] = {v: k for k, v in _KLINE_FUTURES.items()}


def _depth_levels(items: list[dict]) -> list[dict[str, str]]:
    """Convert MEXC depth array [{price, qty, ...}] to IPC format."""
    return [{"price": str(item["price"]), "qty": str(item["qty"])} for item in items]


def _is_linear(symbol: str) -> bool:
    return symbol.endswith("USDT")


def _is_inverse(symbol: str) -> bool:
    return symbol.endswith("USD") and not symbol.endswith("USDT")


# ---------------------------------------------------------------------------
# MexcLimiter
# ---------------------------------------------------------------------------


class MexcLimiter:
    """MEXC rate limiter: 10 requests per 2 seconds."""

    def __init__(self) -> None:
        self._bucket = TokenBucket(
            capacity=_MEXC_CAPACITY,
            refill_per_second=_MEXC_REFILL_RATE,
        )

    async def acquire_rest(self, weight: int = 1) -> None:
        await self._bucket.acquire(weight)


# ---------------------------------------------------------------------------
# MexcDepthSyncer
# ---------------------------------------------------------------------------


class MexcDepthSyncer:
    """MEXC depth protocol: REST snapshot + WS version-based diffs.

    Protocol:
    1. WS subscription confirmed → caller fetches REST snapshot
    2. apply_snapshot() sets the base version
    3. process_diff() applies WS diffs; checks version == applied + 1
    4. Gap detected → DepthGap emitted, needs_resync=True → WS reconnect
    """

    MAX_PENDING = 512

    def __init__(
        self,
        *,
        venue: str,
        ticker: str,
        market: str,
        stream_session_id: str,
        outbox: Any,
    ) -> None:
        self._venue = venue
        self._ticker = ticker
        self._market = market
        self._ssid = stream_session_id
        self._outbox = outbox
        self._applied_version: int = -1
        self._snapshot_ready = False
        self._needs_resync = False
        self._pending: deque[tuple[int, list, list]] = deque()

    @property
    def needs_resync(self) -> bool:
        return self._needs_resync

    def apply_snapshot(self, version: int, bids: list, asks: list) -> None:
        """Apply a REST depth snapshot and replay any buffered diffs."""
        self._applied_version = version
        self._snapshot_ready = True
        self._needs_resync = False

        self._outbox.append(
            {
                "event": "DepthSnapshot",
                "venue": self._venue,
                "ticker": self._ticker,
                "market": self._market,
                "stream_session_id": self._ssid,
                "sequence_id": version,
                "bids": bids,
                "asks": asks,
            }
        )

        # Replay buffered diffs
        pending = list(self._pending)
        self._pending.clear()
        for v, b, a in pending:
            self._apply_diff(v, b, a)
            if self._needs_resync:
                break

    def process_diff(self, version: int, bids: list, asks: list) -> None:
        """Process a WS depth diff."""
        if not self._snapshot_ready or self._needs_resync:
            self._buffer_diff(version, bids, asks)
            return
        self._apply_diff(version, bids, asks)

    def _buffer_diff(self, version: int, bids: list, asks: list) -> None:
        if len(self._pending) >= self.MAX_PENDING:
            self._pending.clear()
            self._emit_gap()
            self._needs_resync = True
            return
        self._pending.append((version, bids, asks))

    def _apply_diff(self, version: int, bids: list, asks: list) -> None:
        # Drop stale diffs
        if version <= self._applied_version:
            return

        if version != self._applied_version + 1:
            self._emit_gap()
            self._needs_resync = True
            return

        self._outbox.append(
            {
                "event": "DepthDiff",
                "venue": self._venue,
                "ticker": self._ticker,
                "market": self._market,
                "stream_session_id": self._ssid,
                "sequence_id": version,
                "prev_sequence_id": self._applied_version,
                "bids": bids,
                "asks": asks,
            }
        )
        self._applied_version = version

    def _emit_gap(self) -> None:
        self._outbox.append(
            {
                "event": "DepthGap",
                "venue": self._venue,
                "ticker": self._ticker,
                "market": self._market,
                "stream_session_id": self._ssid,
            }
        )


# ---------------------------------------------------------------------------
# MexcWorker
# ---------------------------------------------------------------------------


class MexcWorker(ExchangeWorker):
    """Handles MEXC REST and WebSocket data acquisition."""

    def __init__(self, proxy: str | None = None) -> None:
        self._limiter = MexcLimiter()
        self._proxy = proxy
        self._client: httpx.AsyncClient | None = None
        self._http_lock = asyncio.Lock()
        # Populated by _list_tickers_futures; keyed by futures symbol.
        self._contract_sizes: dict[str, float] = {}

    # ------------------------------------------------------------------
    # HTTP client lifecycle
    # ------------------------------------------------------------------

    async def set_proxy(self, url: str | None) -> None:
        self._proxy = url
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                log.warning("Error closing httpx client: %s", exc)
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            async with self._http_lock:
                if self._client is None or self._client.is_closed:
                    self._client = httpx.AsyncClient(
                        proxy=self._proxy,
                        timeout=15.0,
                        follow_redirects=True,
                    )
        return self._client

    async def _get_json(self, url: str, weight: int = 1) -> Any:
        await self._limiter.acquire_rest(weight)
        client = await self._http()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # REST: list_tickers
    # ------------------------------------------------------------------

    async def list_tickers(self, market: str) -> list[dict]:
        if market == "spot":
            return await self._list_tickers_spot()
        return await self._list_tickers_futures(market)

    async def _list_tickers_spot(self) -> list[dict]:
        data = await self._get_json(f"{_REST_V3}/exchangeInfo")
        result = []
        for item in data.get("symbols", []):
            status = item.get("status", "")
            if status not in ("1", "2"):
                continue
            if item.get("quoteAsset", "") not in ("USDT", "USD"):
                continue
            min_qty = float(item.get("baseSizePrecision", 0))
            precision = int(item.get("quoteAssetPrecision", 2))
            min_ticksize = 10.0 ** (-precision)
            result.append(
                {
                    "symbol": item["symbol"],
                    "min_ticksize": min_ticksize,
                    "min_qty": min_qty,
                    "contract_size": None,
                }
            )
        return result

    async def _populate_contract_sizes(self) -> None:
        """Fetch contract sizes from /detail if they have not been loaded yet."""
        data = await self._get_json(f"{_REST_V1}/detail")
        for item in data.get("data", []):
            symbol = item.get("symbol", "")
            if symbol:
                self._contract_sizes[symbol] = float(item.get("contractSize", 1))

    async def _list_tickers_futures(self, market: str) -> list[dict]:
        data = await self._get_json(f"{_REST_V1}/detail")
        result = []
        for item in data.get("data", []):
            if item.get("state", 1) != 0:
                continue
            quote_coin = item.get("quoteCoin", "")
            if quote_coin not in ("USDT", "USD"):
                continue
            settle_coin = item.get("settleCoin", "")
            base_coin = item.get("baseCoin", "")
            # Determine linear vs inverse by settlement currency
            if settle_coin == quote_coin:
                perp_market = "linear_perp"
            elif settle_coin == base_coin:
                perp_market = "inverse_perp"
            else:
                continue
            if perp_market != market:
                continue

            min_vol = float(item.get("minVol", 0))
            price_unit = float(item.get("priceUnit", 0))
            contract_size = float(item.get("contractSize", 1))
            min_qty = min_vol * contract_size

            symbol = item["symbol"]
            self._contract_sizes[symbol] = contract_size
            result.append(
                {
                    "symbol": symbol,
                    "min_ticksize": price_unit,
                    "min_qty": min_qty,
                    "contract_size": contract_size,
                }
            )
        return result

    # ------------------------------------------------------------------
    # REST: fetch_klines
    # ------------------------------------------------------------------

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
        if market == "spot":
            return await self._fetch_klines_spot(ticker, timeframe, limit=limit,
                                                  start_ms=start_ms, end_ms=end_ms)
        return await self._fetch_klines_futures(ticker, timeframe, limit=limit,
                                                start_ms=start_ms, end_ms=end_ms)

    async def _fetch_klines_spot(
        self,
        ticker: str,
        timeframe: str,
        *,
        limit: int,
        start_ms: int | None,
        end_ms: int | None,
    ) -> list[dict]:
        interval = _KLINE_SPOT.get(timeframe)
        if interval is None:
            raise ValueError(
                f"unsupported timeframe {timeframe!r} for MEXC spot; valid: {list(_KLINE_SPOT)}"
            )
        url = f"{_REST_V3}/klines?symbol={ticker}&interval={interval}&limit={limit}"
        if start_ms is not None:
            url += f"&startTime={start_ms}"
        if end_ms is not None:
            url += f"&endTime={end_ms}"

        rows = await self._get_json(url)
        # Spot klines: [open_ts_ms, open, high, low, close, vol, close_ts_ms, asset_vol]
        klines = [
            {
                "open_time_ms": int(row[0]),
                "open": str(row[1]),
                "high": str(row[2]),
                "low": str(row[3]),
                "close": str(row[4]),
                "volume": str(row[5]),
                "is_closed": True,  # REST klines are historical (closed)
            }
            for row in rows
        ]
        klines.sort(key=lambda k: k["open_time_ms"])
        return klines

    async def _fetch_klines_futures(
        self,
        ticker: str,
        timeframe: str,
        *,
        limit: int,
        start_ms: int | None,
        end_ms: int | None,
    ) -> list[dict]:
        interval = _KLINE_FUTURES.get(timeframe)
        if interval is None:
            raise ValueError(
                f"unsupported timeframe {timeframe!r} for MEXC futures; valid: {list(_KLINE_FUTURES)}"
            )
        url = f"{_REST_V1}/kline/{ticker}?interval={interval}&limit={limit}"
        if start_ms is not None:
            url += f"&start={start_ms // 1000}"
        if end_ms is not None:
            url += f"&end={end_ms // 1000}"

        data = await self._get_json(url)
        inner = data.get("data", {})
        times = inner.get("time", [])
        opens = inner.get("open", [])
        highs = inner.get("high", [])
        lows = inner.get("low", [])
        closes = inner.get("close", [])
        vols = inner.get("vol", [])

        klines = [
            {
                "open_time_ms": int(times[i]) * 1000,
                "open": str(opens[i]),
                "high": str(highs[i]),
                "low": str(lows[i]),
                "close": str(closes[i]),
                "volume": str(vols[i]),
                "is_closed": True,
            }
            for i in range(len(times))
        ]
        klines.sort(key=lambda k: k["open_time_ms"])
        return klines

    # ------------------------------------------------------------------
    # REST: fetch_open_interest
    # ------------------------------------------------------------------

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
        # MEXC does not provide historical OI API
        return []

    # ------------------------------------------------------------------
    # REST: fetch_ticker_stats
    # ------------------------------------------------------------------

    async def fetch_ticker_stats(self, ticker: str, market: str) -> dict:
        if market == "spot":
            return await self._fetch_ticker_stats_spot(ticker)
        return await self._fetch_ticker_stats_futures(ticker, market)

    async def _fetch_ticker_stats_spot(self, ticker: str) -> dict:
        items = await self._get_json(f"{_REST_V3}/ticker/24hr")

        def _parse(item: dict) -> dict:
            last_price = float(item.get("lastPrice", 0))
            price_change_percent = float(item.get("priceChangePercent", 0))
            daily_price_chg = price_change_percent * 100.0
            # Prefer quoteVolume (already in USD); fall back to volume * price
            quote_vol = item.get("quoteVolume")
            if quote_vol is not None:
                daily_volume = float(quote_vol)
            else:
                daily_volume = float(item.get("volume", 0)) * last_price
            return {
                "mark_price": item["lastPrice"],
                "daily_price_chg": str(daily_price_chg),
                "daily_volume": str(daily_volume),
            }

        if ticker == "__all__":
            return {
                item["symbol"]: _parse(item)
                for item in items
                if "symbol" in item
                and item.get("symbol", "").endswith("USDT")
            }

        for item in items:
            if item.get("symbol") == ticker:
                return _parse(item)

        raise ValueError(f"Ticker {ticker!r} not found in MEXC spot stats response")

    async def _fetch_ticker_stats_futures(self, ticker: str, market: str) -> dict:
        if not self._contract_sizes:
            await self._populate_contract_sizes()
        data = await self._get_json(f"{_REST_V1}/ticker")
        items = data.get("data", [])

        def _matches_market(symbol: str) -> bool:
            if market == "linear_perp":
                return _is_linear(symbol)
            if market == "inverse_perp":
                return _is_inverse(symbol)
            return True

        def _parse(item: dict) -> dict | None:
            symbol = item.get("symbol", "")
            cs = self._contract_sizes.get(symbol)
            if cs is None:
                # Contract size unknown — skip to avoid silently emitting daily_volume=0.
                log.debug("mexc ticker stats: skipping %s (contract size unknown)", symbol)
                return None
            last_price = float(item.get("lastPrice", 0))
            rise_fall_rate = float(item.get("riseFallRate", 0))
            daily_price_chg = rise_fall_rate * 100.0
            volume24 = float(item.get("volume24", 0))
            if _is_inverse(symbol):
                daily_volume_usd = volume24 * cs
            else:
                daily_volume_usd = volume24 * cs * last_price
            return {
                "mark_price": item["lastPrice"],
                "daily_price_chg": str(daily_price_chg),
                "daily_volume": str(daily_volume_usd),
            }

        if ticker == "__all__":
            result = {}
            for item in items:
                sym = item.get("symbol", "")
                if sym and _matches_market(sym):
                    parsed = _parse(item)
                    if parsed is not None:
                        result[sym] = parsed
            return result

        for item in items:
            if item.get("symbol") == ticker:
                result = _parse(item)
                if result is None:
                    await self._populate_contract_sizes()
                    result = _parse(item)
                    if result is None:
                        raise ValueError(f"Contract size unknown for {ticker!r} after retry")
                return result

        raise ValueError(f"Ticker {ticker!r} not found in MEXC futures stats response")

    # ------------------------------------------------------------------
    # REST: fetch_depth_snapshot
    # ------------------------------------------------------------------

    async def fetch_depth_snapshot(self, ticker: str, market: str) -> dict:
        if market == "spot":
            raise ValueError(
                "MEXC spot depth snapshot is not supported; only futures depth is available"
            )
        data = await self._get_json(f"{_REST_V1}/depth/{ticker}")
        inner = data["data"]
        return {
            "last_update_id": inner["version"],
            "bids": _depth_levels(inner["bids"]),
            "asks": _depth_levels(inner["asks"]),
        }

    # ------------------------------------------------------------------
    # Internal: fetch raw snapshot for syncer (returns (version, bids, asks))
    # ------------------------------------------------------------------

    async def _fetch_snapshot_raw(self, ticker: str) -> tuple[int, list, list]:
        data = await self._get_json(f"{_REST_V1}/depth/{ticker}")
        inner = data["data"]
        return (
            int(inner["version"]),
            _depth_levels(inner["bids"]),
            _depth_levels(inner["asks"]),
        )

    # ------------------------------------------------------------------
    # WebSocket: stream_trades (futures only)
    # ------------------------------------------------------------------

    async def stream_trades(
        self,
        ticker: str,
        market: str,
        stream_session_id: str,
        outbox: Any,
        stop_event: asyncio.Event,
        *,
        on_ssid: OnSsidUpdate | None = None,
    ) -> None:
        if market == "spot":
            outbox.append({"event": "Connected", "venue": "mexc", "ticker": ticker, "stream": "trade", "market": market})
            outbox.append(
                {
                    "event": "Disconnected",
                    "venue": "mexc",
                    "ticker": ticker,
                    "stream": "trade",
                    "market": market,
                    "reason": "MEXC spot trade WebSocket not supported",
                }
            )
            return

        subscribe_msg = orjson.dumps(
            {"method": "sub.deal", "param": {"symbol": ticker}}
        ).decode()
        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            batch: list[dict] = []
            _current_ssid = ssid  # freeze before closures so reconnect doesn't bleed

            def _flush_batch() -> None:
                nonlocal batch
                if not batch:
                    return
                outbox.append(
                    {
                        "event": "Trades",
                        "venue": "mexc",
                        "ticker": ticker,
                        "market": market,
                        "stream_session_id": _current_ssid,
                        "trades": batch,
                    }
                )
                batch = []

            try:
                async with websockets.connect(_WS_FUTURES) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "mexc",
                            "ticker": ticker,
                            "stream": "trade",
                            "market": market,
                        }
                    )

                    async def _flush_periodically() -> None:
                        while True:
                            await asyncio.sleep(_TRADE_BATCH_INTERVAL)
                            _flush_batch()

                    flush_task = asyncio.create_task(_flush_periodically())
                    ping_task = asyncio.create_task(
                        _send_pings(ws, stop_event)
                    )
                    try:
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            try:
                                msg = orjson.loads(raw)
                                channel = msg.get("channel", "")
                                if not channel.endswith(".deal"):
                                    continue
                                for t in msg.get("data", []):
                                    direction = t.get("T", 1)
                                    trade = {
                                        "price": str(t["p"]),
                                        "qty": str(t["v"]),
                                        "side": "sell" if direction == 2 else "buy",
                                        "ts_ms": int(t["t"]),
                                        "is_liquidation": False,
                                    }
                                    batch.append(trade)
                            except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                                log.debug("mexc trade parse error: %s", exc)
                    finally:
                        flush_task.cancel()
                        ping_task.cancel()
                        try:
                            await flush_task
                        except asyncio.CancelledError:
                            pass
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass
                        _flush_batch()

            except Exception as exc:
                _flush_batch()
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("mexc trade disconnected: %s", exc)
                else:
                    log.error("mexc trade unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "mexc",
                        "ticker": ticker,
                        "stream": "trade",
                        "market": market,
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # WebSocket: stream_depth (futures only)
    # ------------------------------------------------------------------

    async def stream_depth(
        self,
        ticker: str,
        market: str,
        stream_session_id: str,
        outbox: Any,
        stop_event: asyncio.Event,
        *,
        on_ssid: OnSsidUpdate | None = None,
    ) -> None:
        if market == "spot":
            outbox.append({"event": "Connected", "venue": "mexc", "ticker": ticker, "stream": "depth", "market": market})
            outbox.append(
                {
                    "event": "Disconnected",
                    "venue": "mexc",
                    "ticker": ticker,
                    "stream": "depth",
                    "market": market,
                    "reason": "MEXC spot depth WebSocket not supported",
                }
            )
            return

        ticker_upper = ticker.upper()
        subscribe_msg = orjson.dumps(
            {"method": "sub.depth", "param": {"symbol": ticker_upper}}
        ).decode()
        conn_counter = 0
        sub_confirm_channel = f"{ticker_upper}.sub.depth"
        depth_channel = f"{ticker_upper}.depth"

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            syncer = MexcDepthSyncer(
                venue="mexc",
                ticker=ticker,
                market=market,
                stream_session_id=ssid,
                outbox=outbox,
            )

            try:
                async with websockets.connect(_WS_FUTURES) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "mexc",
                            "ticker": ticker,
                            "stream": "depth",
                            "market": market,
                        }
                    )

                    ping_task = asyncio.create_task(_send_pings(ws, stop_event))
                    try:
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            try:
                                msg = orjson.loads(raw)
                                channel = msg.get("channel", "")

                                if channel == sub_confirm_channel:
                                    # Subscription confirmed — fetch REST snapshot
                                    try:
                                        version, bids, asks = await self._fetch_snapshot_raw(ticker)
                                        syncer.apply_snapshot(version, bids, asks)
                                    except Exception as exc:
                                        log.error("mexc depth snapshot fetch failed: %s", exc)
                                        outbox.append(
                                            {
                                                "event": "Disconnected",
                                                "venue": "mexc",
                                                "ticker": ticker,
                                                "stream": "depth",
                                                "market": market,
                                                "reason": f"snapshot fetch failed: {exc}",
                                            }
                                        )
                                        break

                                elif channel == depth_channel:
                                    depth_data = msg.get("data", {})
                                    version = int(depth_data.get("version", 0))
                                    bids = _depth_levels(depth_data.get("bids", []))
                                    asks = _depth_levels(depth_data.get("asks", []))
                                    syncer.process_diff(version, bids, asks)

                                    if syncer.needs_resync:
                                        break

                            except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                                log.debug("mexc depth parse error: %s", exc)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("mexc depth disconnected: %s", exc)
                else:
                    log.error("mexc depth unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "mexc",
                        "ticker": ticker,
                        "stream": "depth",
                        "market": market,
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # WebSocket: stream_kline (futures only)
    # ------------------------------------------------------------------

    async def stream_kline(
        self,
        ticker: str,
        market: str,
        timeframe: str,
        stream_session_id: str,
        outbox: Any,
        stop_event: asyncio.Event,
        *,
        on_ssid: OnSsidUpdate | None = None,
    ) -> None:
        if market == "spot":
            outbox.append({"event": "Connected", "venue": "mexc", "ticker": ticker, "stream": f"kline_{timeframe}", "market": market})
            outbox.append(
                {
                    "event": "Disconnected",
                    "venue": "mexc",
                    "ticker": ticker,
                    "stream": f"kline_{timeframe}",
                    "market": market,
                    "reason": "MEXC spot kline WebSocket not supported",
                }
            )
            return

        interval = _KLINE_FUTURES.get(timeframe, timeframe)
        subscribe_msg = orjson.dumps(
            {
                "method": "sub.kline",
                "param": {"symbol": ticker, "interval": interval},
                "gzip": False,
            }
        ).decode()
        kline_channel = f"{ticker}.kline"
        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            try:
                async with websockets.connect(_WS_FUTURES) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "mexc",
                            "ticker": ticker,
                            "stream": f"kline_{timeframe}",
                            "market": market,
                        }
                    )

                    ping_task = asyncio.create_task(_send_pings(ws, stop_event))
                    try:
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            try:
                                msg = orjson.loads(raw)
                                channel = msg.get("channel", "")
                                if channel != kline_channel:
                                    continue
                                kline_data = msg.get("data", {})
                                recv_interval = kline_data.get("interval", "")
                                recv_tf = _INTERVAL_TO_TIMEFRAME.get(recv_interval, timeframe)
                                # time is in seconds; convert to ms
                                open_time_ms = int(kline_data.get("t", 0)) * 1000
                                outbox.append(
                                    {
                                        "event": "KlineUpdate",
                                        "venue": "mexc",
                                        "ticker": ticker,
                                        "market": market,
                                        "timeframe": recv_tf,
                                        "stream_session_id": ssid,
                                        "kline": {
                                            "open_time_ms": open_time_ms,
                                            "open": str(kline_data.get("o", "0")),
                                            "high": str(kline_data.get("h", "0")),
                                            "low": str(kline_data.get("l", "0")),
                                            "close": str(kline_data.get("c", "0")),
                                            "volume": str(kline_data.get("q", "0")),
                                            "is_closed": False,
                                        },
                                    }
                                )
                            except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                                log.debug("mexc kline parse error: %s", exc)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("mexc kline disconnected: %s", exc)
                else:
                    log.error("mexc kline unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "mexc",
                        "ticker": ticker,
                        "stream": f"kline_{timeframe}",
                        "market": market,
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_pings(ws: Any, stop_event: asyncio.Event) -> None:
    """Send periodic ping messages to keep the MEXC WS connection alive."""
    ping_msg = orjson.dumps({"method": "ping"}).decode()
    while not stop_event.is_set():
        await asyncio.sleep(15.0)
        try:
            await ws.send(ping_msg)
        except Exception:
            break
