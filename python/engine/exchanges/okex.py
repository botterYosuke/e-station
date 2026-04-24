"""OKX exchange worker — REST and WebSocket for Phase 3."""

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

_REST = "https://www.okx.com/api/v5"
_WS_PUBLIC = "wss://ws.okx.com/ws/v5/public"
_WS_BUSINESS = "wss://ws.okx.com/ws/v5/business"

_TRADE_BATCH_INTERVAL = 0.033  # 33 ms

# OKX: 20 requests per 2 seconds
_OKX_CAPACITY = 20
_OKX_REFILL_RATE = _OKX_CAPACITY / 2.0  # 10 req/sec

# Timeframe → OKX bar notation
_KLINE_BAR: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "12h": "12Hutc",
    "1d": "1Dutc",
}

# OI period mapping (same bars as klines, filtered to supported values)
_OI_PERIOD: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "12h": "12Hutc",
    "1d": "1Dutc",
}


def _inst_type(market: str) -> str:
    if market == "spot":
        return "SPOT"
    return "SWAP"


def _depth_levels(levels: list[list[str]]) -> list[dict[str, str]]:
    """Convert OKX depth array [price, qty, liquidated_orders, orders] to IPC format."""
    return [{"price": row[0], "qty": row[1]} for row in levels]


# ---------------------------------------------------------------------------
# OkexLimiter
# ---------------------------------------------------------------------------


class OkexLimiter:
    """OKX rate limiter: 20 requests per 2 seconds."""

    def __init__(self) -> None:
        self._bucket = TokenBucket(
            capacity=_OKX_CAPACITY,
            refill_per_second=_OKX_REFILL_RATE,
        )

    async def acquire_rest(self, weight: int = 1) -> None:
        await self._bucket.acquire(weight)


# ---------------------------------------------------------------------------
# OkexDepthSyncer
# ---------------------------------------------------------------------------


class OkexDepthSyncer:
    """Implements OKX WebSocket snapshot+delta depth protocol for IPC.

    OKX books channel: first message has action="snapshot", subsequent have
    action="update". seqId increments by exactly 1 per update; gaps indicate
    missed messages and require reconnection.
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
        action: str,
        update_id: int,
        bids: list[list[str]],
        asks: list[list[str]],
    ) -> None:
        if action == "snapshot":
            self._apply_snapshot(update_id, bids, asks)
        elif action == "update":
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
        self._needs_resync = False

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
# OkexWorker
# ---------------------------------------------------------------------------


class OkexWorker(ExchangeWorker):
    """Handles OKX REST and WebSocket data acquisition."""

    def __init__(self, proxy: str | None = None) -> None:
        self._limiter = OkexLimiter()
        self._proxy = proxy
        self._client: httpx.AsyncClient | None = None
        self._http_lock = asyncio.Lock()

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
        inst_type = _inst_type(market)
        url = f"{_REST}/public/instruments?instType={inst_type}"
        data = await self._get_json(url, weight=1)

        items = data.get("data", [])
        result = []

        for item in items:
            if item.get("state", "") != "live":
                continue

            if market == "spot":
                if item.get("quoteCcy", "") != "USDT":
                    continue
                result.append(
                    {
                        "symbol": item["instId"],
                        "min_ticksize": float(item["tickSz"]),
                        "min_qty": float(item["lotSz"]),
                        "contract_size": None,
                    }
                )

            elif market == "linear_perp":
                if item.get("ctType") != "linear":
                    continue
                if item.get("settleCcy") != "USDT":
                    continue
                ct_val = item.get("ctVal")
                result.append(
                    {
                        "symbol": item["instId"],
                        "min_ticksize": float(item["tickSz"]),
                        "min_qty": float(item["lotSz"]),
                        "contract_size": float(ct_val) if ct_val is not None else None,
                    }
                )

            elif market == "inverse_perp":
                if item.get("ctType") != "inverse":
                    continue
                ct_val = item.get("ctVal")
                result.append(
                    {
                        "symbol": item["instId"],
                        "min_ticksize": float(item["tickSz"]),
                        "min_qty": float(item["lotSz"]),
                        "contract_size": float(ct_val) if ct_val is not None else None,
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
        limit: int = 300,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict]:
        bar = _KLINE_BAR.get(timeframe)
        if bar is None:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; valid: {list(_KLINE_BAR)}"
            )

        url = f"{_REST}/market/history-candles?instId={ticker}&bar={bar}&limit={min(limit, 300)}"
        if start_ms is not None:
            url += f"&before={start_ms}"
        if end_ms is not None:
            url += f"&after={end_ms}"

        data = await self._get_json(url, weight=1)
        rows = data.get("data", [])

        klines = [
            {
                "open_time_ms": int(row[0]),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
                # index 8: "1" = closed, "0" = open (may not exist for older API)
                "is_closed": len(row) > 8 and row[8] == "1",
            }
            for row in rows
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
        limit: int = 300,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict]:
        if market not in ("linear_perp", "inverse_perp"):
            return []

        bar = _OI_PERIOD.get(timeframe)
        if bar is None:
            raise ValueError(
                f"unsupported timeframe {timeframe!r} for OI; valid: {list(_OI_PERIOD)}"
            )

        url = f"{_REST}/rubik/stat/contracts/open-interest-history?instId={ticker}&period={bar}"
        if start_ms is not None:
            url += f"&begin={start_ms}"
        if end_ms is not None:
            url += f"&end={end_ms}"

        data = await self._get_json(url, weight=1)
        rows = data.get("data", [])

        return [
            {
                "ts_ms": int(row[0]),
                # index 2: OI in currency (BTC/USD), index 1: OI in contracts
                "open_interest": row[2],
            }
            for row in rows
            if len(row) >= 3
        ]

    # ------------------------------------------------------------------
    # REST: fetch_ticker_stats
    # ------------------------------------------------------------------

    async def fetch_ticker_stats(self, ticker: str, market: str) -> dict:
        inst_type = _inst_type(market)
        url = f"{_REST}/market/tickers?instType={inst_type}"
        data = await self._get_json(url, weight=1)
        items = data.get("data", [])

        is_perp = market in ("linear_perp", "inverse_perp")

        def _parse(item: dict) -> dict:
            last_price = float(item.get("last", 0))
            open24h = float(item.get("open24h", 0))
            vol_ccy24h = float(item.get("volCcy24h", 0))

            daily_price_chg = (
                (last_price - open24h) / open24h * 100.0 if open24h > 0 else 0.0
            )
            # Spot: volCcy24h is already in quote currency (USD).
            # Perps: volCcy24h is in base currency (BTC/ETH) → multiply by price.
            if is_perp:
                daily_volume = vol_ccy24h * last_price
            else:
                daily_volume = vol_ccy24h

            return {
                "mark_price": item["last"],
                "daily_price_chg": str(daily_price_chg),
                "daily_volume": str(daily_volume),
            }

        if ticker == "__all__":
            def _matches_market(inst_id: str) -> bool:
                if market == "linear_perp":
                    return inst_id.endswith("-USDT-SWAP")
                if market == "inverse_perp":
                    return inst_id.endswith("-USD-SWAP")
                return True  # spot: no additional filter needed

            return {
                item["instId"]: _parse(item)
                for item in items
                if "instId" in item and "last" in item and _matches_market(item["instId"])
            }

        for item in items:
            if item.get("instId") == ticker:
                return _parse(item)

        raise ValueError(f"Ticker {ticker!r} not found in OKX stats response")

    # ------------------------------------------------------------------
    # REST: fetch_depth_snapshot
    # ------------------------------------------------------------------

    async def fetch_depth_snapshot(self, ticker: str, market: str) -> dict:
        url = f"{_REST}/market/books?instId={ticker}&sz=400"
        data = await self._get_json(url, weight=1)
        if not data.get("data"):
            raise ValueError(f"Empty depth snapshot for {ticker!r}: {data}")
        entry = data["data"][0]
        return {
            "last_update_id": entry["seqId"],
            "bids": _depth_levels(entry["bids"]),
            "asks": _depth_levels(entry["asks"]),
        }

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
        subscribe_msg = orjson.dumps(
            {"op": "subscribe", "args": [{"channel": "trades", "instId": ticker}]}
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
                        "venue": "okex",
                        "ticker": ticker,
                        "market": market,
                        "stream_session_id": _current_ssid,
                        "trades": batch,
                    }
                )
                batch = []

            try:
                async with websockets.connect(_WS_PUBLIC) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "okex",
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
                                # Skip subscription confirmations and pings
                                if "event" in msg or "data" not in msg:
                                    continue
                                channel = msg.get("arg", {}).get("channel", "")
                                if channel != "trades":
                                    continue

                                for t in msg["data"]:
                                    try:
                                        trade = {
                                            "price": t["px"],
                                            "qty": t["sz"],
                                            "side": t["side"],
                                            "ts_ms": int(t["ts"]),
                                            "is_liquidation": False,
                                        }
                                        batch.append(trade)
                                    except (KeyError, ValueError, TypeError) as exc:
                                        log.debug("okex trade parse error: %s", exc)
                            except (orjson.JSONDecodeError, ValueError, TypeError) as exc:
                                log.debug("okex trade parse error: %s", exc)
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
                    log.warning("okex trade disconnected: %s", exc)
                else:
                    log.error("okex trade unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "okex",
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
        subscribe_msg = orjson.dumps(
            {"op": "subscribe", "args": [{"channel": "books", "instId": ticker}]}
        ).decode()
        conn_counter = 0
        resync_streak = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            syncer = OkexDepthSyncer(
                venue="okex",
                ticker=ticker,
                market=market,
                stream_session_id=ssid,
                outbox=outbox,
            )

            broke_for_resync = False
            try:
                async with websockets.connect(_WS_PUBLIC) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "okex",
                            "ticker": ticker,
                            "stream": "depth",
                        }
                    )

                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        try:
                            msg = orjson.loads(raw)
                            if "event" in msg or "data" not in msg:
                                continue

                            action = msg.get("action", "")
                            if action not in ("snapshot", "update"):
                                continue

                            data_list = msg.get("data", [])
                            if not data_list:
                                continue
                            first = data_list[0]
                            update_id = first.get("seqId", 0)
                            bids = first.get("bids", [])
                            asks = first.get("asks", [])

                            syncer.process_message(
                                action=action,
                                update_id=update_id,
                                bids=bids,
                                asks=asks,
                            )

                            if syncer.needs_resync:
                                broke_for_resync = True
                                break

                        except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                            log.debug("okex depth parse error: %s", exc)

            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("okex depth disconnected: %s", exc)
                else:
                    log.error("okex depth unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "okex",
                        "ticker": ticker,
                        "stream": "depth",
                        "reason": str(exc),
                    }
                )
                resync_streak = 0
                await asyncio.sleep(1.0)
                continue

            if broke_for_resync and not stop_event.is_set():
                resync_streak += 1
                backoff = min(1.0 * (2 ** (resync_streak - 1)), 30.0)
                if resync_streak > 1:
                    log.warning(
                        "okex depth %s: resync streak %d, backing off %.1fs",
                        ticker,
                        resync_streak,
                        backoff,
                    )
                await asyncio.sleep(backoff)
            else:
                resync_streak = 0

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
        bar = _KLINE_BAR.get(timeframe, timeframe)
        channel = f"candle{bar}"
        subscribe_msg = orjson.dumps(
            {"op": "subscribe", "args": [{"channel": channel, "instId": ticker}]}
        ).decode()
        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            try:
                # Klines use the "business" WS endpoint
                async with websockets.connect(_WS_BUSINESS) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "okex",
                            "ticker": ticker,
                            "stream": f"kline_{timeframe}",
                        }
                    )

                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        try:
                            msg = orjson.loads(raw)
                            if "event" in msg or "data" not in msg:
                                continue

                            ch = msg.get("arg", {}).get("channel", "")
                            if not ch.startswith("candle"):
                                continue

                            for row in msg["data"]:
                                # row: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
                                is_closed = len(row) > 8 and row[8] == "1"
                                outbox.append(
                                    {
                                        "event": "KlineUpdate",
                                        "venue": "okex",
                                        "ticker": ticker,
                                        "market": market,
                                        "timeframe": timeframe,
                                        "stream_session_id": ssid,
                                        "kline": {
                                            "open_time_ms": int(row[0]),
                                            "open": row[1],
                                            "high": row[2],
                                            "low": row[3],
                                            "close": row[4],
                                            "volume": row[5],
                                            "is_closed": is_closed,
                                        },
                                    }
                                )
                        except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                            log.debug("okex kline parse error: %s", exc)

            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("okex kline disconnected: %s", exc)
                else:
                    log.error("okex kline unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "okex",
                        "ticker": ticker,
                        "stream": f"kline_{timeframe}",
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)
