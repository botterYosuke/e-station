"""Bybit exchange worker — REST and WebSocket for Phase 3."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

import httpx
import orjson
import websockets

from engine.exchanges.base import ExchangeWorker, OnSsidUpdate, WsNativeResyncTriggered
from engine.limiter import TokenBucket

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint constants
# ---------------------------------------------------------------------------

_REST = "https://api.bybit.com"
_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"
_WS_INVERSE = "wss://stream.bybit.com/v5/public/inverse"
_WS_SPOT = "wss://stream.bybit.com/v5/public/spot"

_TRADE_BATCH_INTERVAL = 0.033  # 33 ms

# Bybit: 600 requests per 5 seconds
_BYBIT_CAPACITY = 600
_BYBIT_REFILL_RATE = _BYBIT_CAPACITY / 5.0  # 120 req/sec

# Timeframe mapping from common notation to Bybit interval strings
_KLINE_INTERVAL: dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "4h": "240",
    "6h": "360",
    "12h": "720",
    "1d": "D",
}

# OI period mapping
_OI_PERIOD: dict[str, str] = {
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


def _market_category(market: str) -> str:
    if market == "linear_perp":
        return "linear"
    if market == "inverse_perp":
        return "inverse"
    return "spot"


def _ws_url(market: str) -> str:
    if market == "linear_perp":
        return _WS_LINEAR
    if market == "inverse_perp":
        return _WS_INVERSE
    return _WS_SPOT


def _depth_levels(levels: list[list[str]]) -> list[dict[str, str]]:
    return [{"price": price, "qty": qty} for price, qty in levels]


# ---------------------------------------------------------------------------
# BybitLimiter
# ---------------------------------------------------------------------------


class BybitLimiter:
    """Bybit rate limiter: 600 requests per 5 seconds."""

    def __init__(self) -> None:
        self._bucket = TokenBucket(
            capacity=_BYBIT_CAPACITY,
            refill_per_second=_BYBIT_REFILL_RATE,
        )

    async def acquire_rest(self, weight: int = 1) -> None:
        await self._bucket.acquire(weight)


# ---------------------------------------------------------------------------
# BybitDepthSyncer
# ---------------------------------------------------------------------------


class BybitDepthSyncer:
    """Implements Bybit's WebSocket snapshot+delta depth protocol for IPC.

    Unlike Binance, Bybit sends the full orderbook snapshot as the first
    WebSocket message (type="snapshot"), so no REST prefetch is needed.
    Subsequent messages are type="delta" with a monotonic update_id (`u`).
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
        self._applied_seq: int = 0
        self._initialized = False
        self._needs_resync = False
        self._pending: deque[tuple[int, list, list]] = deque()

    @property
    def needs_resync(self) -> bool:
        return self._needs_resync

    def process_message(
        self,
        *,
        msg_type: str,
        update_id: int,
        bids: list[list[str]],
        asks: list[list[str]],
    ) -> None:
        """Process an incoming WS depth message (snapshot or delta)."""
        if msg_type == "snapshot":
            self._apply_snapshot(update_id, bids, asks)
        elif msg_type == "delta":
            if not self._initialized or self._needs_resync:
                self._buffer_delta(update_id, bids, asks)
            else:
                self._apply_delta(update_id, bids, asks)

    def _buffer_delta(self, update_id: int, bids: list, asks: list) -> None:
        if len(self._pending) >= self.MAX_PENDING:
            self._pending.clear()
            self._emit_gap()
            self._needs_resync = True
            return
        self._pending.append((update_id, bids, asks))

    def _apply_snapshot(self, update_id: int, bids: list, asks: list) -> None:
        self._applied_seq = update_id
        self._initialized = True

        self._outbox.append(
            {
                "event": "DepthSnapshot",
                "venue": self._venue,
                "ticker": self._ticker,
                "market": self._market,
                "stream_session_id": self._ssid,
                "sequence_id": update_id,
                "bids": _depth_levels(bids),
                "asks": _depth_levels(asks),
            }
        )

        # Replay buffered deltas
        pending = list(self._pending)
        self._pending.clear()
        for uid, b, a in pending:
            self._apply_delta(uid, b, a)
            if self._needs_resync:
                break

    def _apply_delta(self, update_id: int, bids: list, asks: list) -> None:
        # Drop stale events
        if update_id <= self._applied_seq:
            return

        if update_id != self._applied_seq + 1:
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
                "sequence_id": update_id,
                "prev_sequence_id": self._applied_seq,
                "bids": _depth_levels(bids),
                "asks": _depth_levels(asks),
            }
        )
        self._applied_seq = update_id

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
# BybitWorker
# ---------------------------------------------------------------------------


class BybitWorker(ExchangeWorker):
    """Handles Bybit REST and WebSocket data acquisition."""

    def __init__(self, proxy: str | None = None) -> None:
        self._limiter = BybitLimiter()
        self._proxy = proxy
        self._client: httpx.AsyncClient | None = None
        self._http_lock = asyncio.Lock()
        # Keyed by (ticker, market); set to trigger WS reconnect from outside the stream task.
        self._reconnect_triggers: dict[tuple[str, str], asyncio.Event] = {}

    def _reconnect_trigger(self, ticker: str, market: str) -> asyncio.Event:
        key = (ticker, market)
        if key not in self._reconnect_triggers:
            self._reconnect_triggers[key] = asyncio.Event()
        return self._reconnect_triggers[key]

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
        category = _market_category(market)
        url = f"{_REST}/v5/market/instruments-info?category={category}&limit=1000"
        data = await self._get_json(url, weight=1)

        items = data.get("result", {}).get("list", [])
        result = []
        for item in items:
            contract_type = item.get("contractType")
            # For derivatives: filter to only perpetuals
            if contract_type is not None:
                if market == "linear_perp" and contract_type != "LinearPerpetual":
                    continue
                if market == "inverse_perp" and contract_type != "InversePerpetual":
                    continue

            status = item.get("status", "")
            if status and status != "Trading":
                continue

            quote = item.get("quoteCoin", "")
            if quote not in ("USDT", "USD", ""):
                continue

            lot_filter = item.get("lotSizeFilter", {})
            price_filter = item.get("priceFilter", {})

            min_qty_str = lot_filter.get("minOrderQty")
            tick_str = price_filter.get("tickSize")
            if min_qty_str is None or tick_str is None:
                continue

            result.append(
                {
                    "symbol": item["symbol"],
                    "min_ticksize": float(tick_str),
                    "min_qty": float(min_qty_str),
                    "contract_size": None,
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
        category = _market_category(market)
        interval = _KLINE_INTERVAL.get(timeframe, timeframe)
        url = f"{_REST}/v5/market/kline?category={category}&symbol={ticker}&interval={interval}&limit={limit}"
        if start_ms is not None:
            url += f"&start={start_ms}"
        if end_ms is not None:
            url += f"&end={end_ms}"

        data = await self._get_json(url, weight=1)
        rows = data.get("result", {}).get("list", [])

        return [
            {
                "open_time_ms": int(row[0]),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
                "is_closed": True,
            }
            for row in rows
        ]

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
        if market not in ("linear_perp", "inverse_perp"):
            return []

        limit = min(limit, 200)
        category = _market_category(market)
        period = _OI_PERIOD.get(timeframe)
        if period is None:
            raise ValueError(
                f"unsupported OI timeframe {timeframe!r}; valid: {list(_OI_PERIOD)}"
            )
        url = f"{_REST}/v5/market/open-interest?category={category}&symbol={ticker}&intervalTime={period}&limit={limit}"
        if start_ms is not None:
            url += f"&startTime={start_ms}"
        if end_ms is not None:
            url += f"&endTime={end_ms}"

        data = await self._get_json(url, weight=1)
        rows = data.get("result", {}).get("list", [])

        return [
            {
                "ts_ms": int(row["timestamp"]),
                "open_interest": row["openInterest"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # REST: fetch_ticker_stats
    # ------------------------------------------------------------------

    async def fetch_ticker_stats(self, ticker: str, market: str) -> dict:
        category = _market_category(market)
        url = f"{_REST}/v5/market/tickers?category={category}"
        data = await self._get_json(url, weight=1)

        items = data.get("result", {}).get("list", [])

        def _parse(item: dict) -> dict:
            mark_price = float(item.get("lastPrice", 0))
            volume24h = float(item.get("volume24h", 0))
            # Match Rust fetch.rs: inverse uses volume24h as-is (USD-denominated),
            # linear/spot multiplies by mark_price to get USD value.
            if category == "inverse":
                daily_volume = volume24h
            else:
                daily_volume = volume24h * mark_price
            # price24hPcnt is a decimal fraction e.g. 0.025 = 2.5%
            pct = float(item.get("price24hPcnt", "0")) * 100.0
            return {
                "mark_price": item["lastPrice"],
                "daily_price_chg": str(pct),
                "daily_volume": str(daily_volume),
            }

        if ticker == "__all__":
            return {
                item["symbol"]: _parse(item)
                for item in items
                if "symbol" in item and "lastPrice" in item
            }

        for item in items:
            if item.get("symbol") == ticker:
                return _parse(item)

        raise ValueError(f"Ticker {ticker} not found in Bybit stats response")

    # ------------------------------------------------------------------
    # REST: fetch_depth_snapshot
    # ------------------------------------------------------------------

    async def fetch_depth_snapshot(self, ticker: str, market: str) -> dict:
        # Bybit's REST orderbook uses a different sequence namespace than orderbook.200,
        # so REST snapshots cannot be spliced into the live WS feed. Resync is WS-native:
        # signal the active stream to reconnect; a fresh type="snapshot" arrives via WS.
        # Only set the trigger if stream_depth is already running for this ticker/market;
        # otherwise we would create an orphaned entry that is never cleaned up.
        key = (ticker, market)
        if key in self._reconnect_triggers:
            self._reconnect_triggers[key].set()
        raise WsNativeResyncTriggered(
            "Bybit orderbook.200 depth resync is WS-native — reconnect triggered."
        )

    # ------------------------------------------------------------------
    # WebSocket: stream_trades
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
        ws_url = _ws_url(market)
        subscribe_msg = orjson.dumps(
            {"op": "subscribe", "args": [f"publicTrade.{ticker}"]}
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
                        "venue": "bybit",
                        "ticker": ticker,
                        "market": market,
                        "stream_session_id": _current_ssid,
                        "trades": batch,
                    }
                )
                batch = []

            try:
                async with websockets.connect(ws_url) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "bybit",
                            "ticker": ticker,
                            "stream": "trade",
                        }
                    )

                    async def _flush_periodically() -> None:
                        while True:
                            await asyncio.sleep(_TRADE_BATCH_INTERVAL)
                            _flush_batch()

                    flush_task = asyncio.create_task(_flush_periodically())
                    try:
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            try:
                                msg = orjson.loads(raw)
                                topic = msg.get("topic", "")
                                if not topic.startswith("publicTrade."):
                                    continue

                                trades_data = msg.get("data", [])
                                for t in trades_data:
                                    try:
                                        trade = {
                                            "price": t["p"],
                                            "qty": t["v"],
                                            "side": "sell" if t["S"] == "Sell" else "buy",
                                            "ts_ms": t["T"],
                                            "is_liquidation": False,
                                        }
                                        batch.append(trade)
                                    except (KeyError, ValueError, TypeError) as exc:
                                        log.debug("bybit trade parse error: %s", exc)
                            except (orjson.JSONDecodeError, ValueError, TypeError) as exc:
                                log.debug("bybit trade parse error: %s", exc)
                    finally:
                        flush_task.cancel()
                        try:
                            await flush_task
                        except asyncio.CancelledError:
                            pass
                        _flush_batch()

            except Exception as exc:
                _flush_batch()
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("bybit trade disconnected: %s", exc)
                else:
                    log.error("bybit trade unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "bybit",
                        "ticker": ticker,
                        "stream": "trade",
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # WebSocket: stream_depth
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
        ws_url = _ws_url(market)
        # Use 200-level depth for best balance of granularity and speed
        depth_topic = f"orderbook.200.{ticker}"
        subscribe_msg = orjson.dumps(
            {"op": "subscribe", "args": [depth_topic]}
        ).decode()
        conn_counter = 0
        reconnect_trigger = self._reconnect_trigger(ticker, market)

        try:
            while not stop_event.is_set():
                conn_counter += 1
                ssid = f"{stream_session_id}:{conn_counter}"
                if on_ssid is not None:
                    on_ssid(ssid)

                syncer = BybitDepthSyncer(
                    venue="bybit",
                    ticker=ticker,
                    market=market,
                    stream_session_id=ssid,
                    outbox=outbox,
                )

                try:
                    async with websockets.connect(ws_url) as ws:
                        await ws.send(subscribe_msg)
                        outbox.append(
                            {
                                "event": "Connected",
                                "venue": "bybit",
                                "ticker": ticker,
                                "stream": "depth",
                            }
                        )

                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            if reconnect_trigger.is_set():
                                reconnect_trigger.clear()
                                break
                            try:
                                msg = orjson.loads(raw)
                                topic = msg.get("topic", "")
                                if not topic.startswith("orderbook."):
                                    continue

                                msg_type = msg.get("type", "")
                                if msg_type not in ("snapshot", "delta"):
                                    continue

                                data = msg.get("data", {})
                                update_id = data.get("u", 0)
                                bids = data.get("b", [])
                                asks = data.get("a", [])

                                syncer.process_message(
                                    msg_type=msg_type,
                                    update_id=update_id,
                                    bids=bids,
                                    asks=asks,
                                )

                                if syncer.needs_resync:
                                    # Reconnect to get a fresh WS snapshot
                                    break
                            except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                                log.debug("bybit depth parse error: %s", exc)

                except Exception as exc:
                    if stop_event.is_set():
                        break
                    if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                        log.warning("bybit depth disconnected: %s", exc)
                    else:
                        log.error("bybit depth unexpected error: %s", exc)
                    outbox.append(
                        {
                            "event": "Disconnected",
                            "venue": "bybit",
                            "ticker": ticker,
                            "stream": "depth",
                            "reason": str(exc),
                        }
                    )
                    await asyncio.sleep(1.0)
        finally:
            self._reconnect_triggers.pop((ticker, market), None)

    # ------------------------------------------------------------------
    # WebSocket: stream_kline
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
        ws_url = _ws_url(market)
        interval = _KLINE_INTERVAL.get(timeframe, timeframe)
        kline_topic = f"kline.{interval}.{ticker}"
        subscribe_msg = orjson.dumps(
            {"op": "subscribe", "args": [kline_topic]}
        ).decode()
        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            try:
                async with websockets.connect(ws_url) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "bybit",
                            "ticker": ticker,
                            "stream": f"kline_{timeframe}",
                        }
                    )
                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        try:
                            msg = orjson.loads(raw)
                            topic = msg.get("topic", "")
                            if not topic.startswith("kline."):
                                continue

                            kline_list = msg.get("data", [])
                            for k in kline_list:
                                outbox.append(
                                    {
                                        "event": "KlineUpdate",
                                        "venue": "bybit",
                                        "ticker": ticker,
                                        "market": market,
                                        "timeframe": timeframe,
                                        "stream_session_id": ssid,
                                        "kline": {
                                            "open_time_ms": k["start"],
                                            "open": k["open"],
                                            "high": k["high"],
                                            "low": k["low"],
                                            "close": k["close"],
                                            "volume": k["volume"],
                                            "is_closed": k.get("confirm", False),
                                        },
                                    }
                                )
                        except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                            log.debug("bybit kline parse error: %s", exc)

            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("bybit kline disconnected: %s", exc)
                else:
                    log.error("bybit kline unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "bybit",
                        "ticker": ticker,
                        "stream": f"kline_{timeframe}",
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)
