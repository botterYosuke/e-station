"""Hyperliquid exchange worker — REST and WebSocket for Phase 3."""

from __future__ import annotations

import asyncio
import logging
import math
import time as time_module
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

_API_INFO = "https://api.hyperliquid.xyz/info"
_WS_URL = "wss://api.hyperliquid.xyz/ws"

_TRADE_BATCH_INTERVAL = 0.033  # 33 ms

# Rate limits: 1200 requests per 60 seconds
_HL_CAPACITY = 1200
_HL_REFILL_RATE = _HL_CAPACITY / 60.0  # 20 req/sec

# Tick size computation parameters (matches Rust fetch.rs)
_MAX_DECIMALS_PERP = 6
_SIG_FIG_LIMIT = 5

# Kline interval → milliseconds for start_ms calculation when limit is given
_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _compute_tick_size(price: float, sz_decimals: int) -> float:
    """Compute min tick size from price and size decimals.

    Mirrors the Rust fetch.rs compute_tick_size logic:
    - Up to SIG_FIG_LIMIT (5) significant figures in price
    - Capped by MAX_DECIMALS_PERP - sz_decimals decimal places
    """
    if price <= 0.0:
        return 0.001

    decimal_cap = max(_MAX_DECIMALS_PERP - sz_decimals, 0)

    if price >= 1.0:
        int_digits = max(int(math.floor(math.log10(price))) + 1, 1)
        if int_digits > _SIG_FIG_LIMIT:
            return 1.0
        remaining_sig = _SIG_FIG_LIMIT - int_digits
        if remaining_sig == 0 or decimal_cap == 0:
            return 1.0
        return 10.0 ** (-min(remaining_sig, decimal_cap))
    else:
        lg = math.floor(math.log10(price))
        leading_zeros = max(-lg - 1, 0)
        total_decimals = min(leading_zeros + _SIG_FIG_LIMIT, decimal_cap)
        if total_decimals <= 0:
            return 1.0
        return 10.0 ** (-total_decimals)


def _asset_price(ctx: dict) -> float:
    """Return mid price if > 0, otherwise mark price. Matches Rust ctx.price()."""
    try:
        mid = float(ctx.get("midPx") or 0)
    except (TypeError, ValueError):
        mid = 0.0
    if mid > 0.0:
        return mid
    try:
        return float(ctx.get("markPx") or 0)
    except (TypeError, ValueError):
        return 0.0


def _daily_price_chg_pct(price: float, prev_day_price: float) -> float:
    """Compute 24h price change percentage."""
    if prev_day_price > 0.0:
        return ((price - prev_day_price) / prev_day_price) * 100.0
    return 0.0


def _create_display_symbol(pair_name: str, tokens: list[dict], token_indices: list[int]) -> str:
    """Create human-readable symbol from pair metadata. Mirrors Rust create_display_symbol."""
    if pair_name.startswith("@"):
        base_token = next((t for t in tokens if t.get("index") == token_indices[0]), None)
        quote_token = next((t for t in tokens if t.get("index") == token_indices[1]), None)
        if base_token and quote_token:
            return f"{base_token['name']}{quote_token['name']}"
        return pair_name
    return pair_name.replace("/", "")


# ---------------------------------------------------------------------------
# HyperliquidLimiter
# ---------------------------------------------------------------------------


class HyperliquidLimiter:
    """Hyperliquid rate limiter: 1200 requests per 60 seconds."""

    def __init__(self) -> None:
        self._bucket = TokenBucket(
            capacity=_HL_CAPACITY,
            refill_per_second=_HL_REFILL_RATE,
        )

    async def acquire_rest(self, weight: int = 1) -> None:
        await self._bucket.acquire(weight)


# ---------------------------------------------------------------------------
# HyperliquidDepthSyncer
# ---------------------------------------------------------------------------


class HyperliquidDepthSyncer:
    """Depth syncer for Hyperliquid's full-snapshot WS protocol.

    Unlike Binance/Bybit, Hyperliquid sends the complete l2Book on every WS
    message. There are no partial diffs. Each message replaces the entire book.
    We emit DepthSnapshot for every message with a monotonically increasing
    sequence_id derived from the message's time field.
    """

    def __init__(
        self,
        *,
        venue: str,
        ticker: str,
        stream_session_id: str,
        outbox: Any,
    ) -> None:
        self._venue = venue
        self._ticker = ticker
        self._ssid = stream_session_id
        self._outbox = outbox
        self._last_seq: int = 0

    def process_message(
        self,
        *,
        time: int,
        bids: list[dict],
        asks: list[dict],
    ) -> None:
        """Process an incoming WS l2Book message (always a full snapshot)."""
        # Ensure monotonically increasing sequence_id even if time repeats
        seq = max(time, self._last_seq + 1)
        self._last_seq = seq

        self._outbox.append(
            {
                "event": "DepthSnapshot",
                "venue": self._venue,
                "ticker": self._ticker,
                "stream_session_id": self._ssid,
                "sequence_id": seq,
                "bids": [{"price": str(level["px"]), "qty": str(level["sz"])} for level in bids],
                "asks": [{"price": str(level["px"]), "qty": str(level["sz"])} for level in asks],
            }
        )


# ---------------------------------------------------------------------------
# HyperliquidWorker
# ---------------------------------------------------------------------------


class HyperliquidWorker(ExchangeWorker):
    """Handles Hyperliquid REST and WebSocket data acquisition."""

    def __init__(self, proxy: str | None = None) -> None:
        self._limiter = HyperliquidLimiter()
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

    async def _post_json(self, body: dict, weight: int = 1) -> Any:
        await self._limiter.acquire_rest(weight)
        client = await self._http()
        resp = await client.post(_API_INFO, json=body)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Internal: fetch perp/spot metadata
    # ------------------------------------------------------------------

    async def _fetch_perp_metadata(self) -> tuple[list[dict], list[dict]]:
        """Fetch all perp asset metadata across all DEXs.

        Returns (assets, ctxs) where assets[i] has {name, szDecimals, index}
        and ctxs[i] has {dayNtlVlm, markPx, midPx, prevDayPx}.
        """
        dex_list: list = await self._post_json({"type": "perpDexs"})

        combined_assets: list[dict] = []
        combined_ctxs: list[dict] = []

        for dex_entry in dex_list:
            dex_name = None if dex_entry is None else (
                dex_entry.get("name") if isinstance(dex_entry, dict) else None
            )

            body: dict[str, Any] = {"type": "metaAndAssetCtxs"}
            if dex_name is not None:
                body["dex"] = dex_name

            try:
                response = await self._post_json(body)
                meta = response[0]
                ctxs = response[1] if len(response) > 1 else []

                universe = meta.get("universe", [])
                combined_assets.extend(universe)
                combined_ctxs.extend(ctxs)
            except Exception as exc:
                log.warning("Failed to fetch perp metadata for DEX %s: %s", dex_name, exc)

        return combined_assets, combined_ctxs

    async def _fetch_spot_metadata(self) -> tuple[list[dict], list[dict], list[dict]]:
        """Fetch spot metadata. Returns (tokens, pairs, ctxs)."""
        response = await self._post_json({"type": "spotMetaAndAssetCtxs"})
        meta = response[0]
        ctxs = response[1] if len(response) > 1 else []
        tokens = meta.get("tokens", [])
        pairs = meta.get("universe", [])
        return tokens, pairs, ctxs

    # ------------------------------------------------------------------
    # REST: list_tickers
    # ------------------------------------------------------------------

    async def list_tickers(self, market: str) -> list[dict]:
        if market == "linear_perp":
            return await self._list_tickers_perp()
        if market == "spot":
            return await self._list_tickers_spot()
        return []

    async def _list_tickers_perp(self) -> list[dict]:
        assets, ctxs = await self._fetch_perp_metadata()
        result = []
        for i, asset in enumerate(assets):
            ctx = ctxs[i] if i < len(ctxs) else {}
            price = _asset_price(ctx)
            if price <= 0.0:
                continue

            sz_decimals = int(asset.get("szDecimals", 0))
            tick_size = _compute_tick_size(price, sz_decimals)
            min_qty = 10.0 ** (-sz_decimals) if sz_decimals > 0 else 1.0

            result.append(
                {
                    "symbol": asset["name"],
                    "min_ticksize": tick_size,
                    "min_qty": min_qty,
                    "contract_size": None,
                }
            )
        return result

    async def _list_tickers_spot(self) -> list[dict]:
        tokens, pairs, ctxs = await self._fetch_spot_metadata()
        result = []
        for i, pair in enumerate(pairs):
            ctx = ctxs[i] if i < len(ctxs) else {}
            price = _asset_price(ctx)
            if price <= 0.0:
                continue

            token_indices = pair.get("tokens", [])

            base_index = token_indices[0] if token_indices else None
            base_token = (
                next((t for t in tokens if isinstance(t, dict) and t.get("index") == base_index), None)
                if base_index is not None else None
            )
            sz_decimals = int(base_token.get("szDecimals", 0)) if base_token is not None else 0

            tick_size = _compute_tick_size(price, sz_decimals)
            min_qty = 10.0 ** (-sz_decimals) if sz_decimals > 0 else 1.0

            display_sym = _create_display_symbol(pair["name"], tokens, token_indices)
            result.append(
                {
                    "symbol": pair["name"],
                    "display_symbol": display_sym,
                    "min_ticksize": tick_size,
                    "min_qty": min_qty,
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
        interval_ms = _INTERVAL_MS.get(timeframe, 60_000)
        now_ms = int(time_module.time() * 1000)

        end = end_ms if end_ms is not None else now_ms
        start = start_ms if start_ms is not None else (end - limit * interval_ms)

        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": ticker,
                "interval": timeframe,
                "startTime": start,
                "endTime": end,
            },
        }

        data = await self._post_json(body)

        return [
            {
                "open_time_ms": int(k["t"]),
                "open": str(k["o"]),
                "high": str(k["h"]),
                "low": str(k["l"]),
                "close": str(k["c"]),
                "volume": str(k["v"]),
                "is_closed": True,
            }
            for k in data
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
        # Hyperliquid does not provide historical open interest time series
        return []

    # ------------------------------------------------------------------
    # REST: fetch_ticker_stats
    # ------------------------------------------------------------------

    async def fetch_ticker_stats(self, ticker: str, market: str) -> dict:
        stats_map = await self._build_stats_map(market)

        if ticker == "__all__":
            return stats_map

        if ticker not in stats_map:
            raise ValueError(f"Ticker {ticker} not found in Hyperliquid stats for market={market}")

        return stats_map[ticker]

    async def _build_stats_map(self, market: str) -> dict[str, dict]:
        if market == "linear_perp":
            assets, ctxs = await self._fetch_perp_metadata()
            result: dict[str, dict] = {}
            for i, asset in enumerate(assets):
                ctx = ctxs[i] if i < len(ctxs) else {}
                price = _asset_price(ctx)
                if price <= 0.0:
                    continue
                try:
                    prev = float(ctx.get("prevDayPx") or 0)
                    mark = float(ctx.get("markPx") or 0)
                    volume = float(ctx.get("dayNtlVlm") or 0)
                except (TypeError, ValueError):
                    continue
                result[asset["name"]] = {
                    "mark_price": str(mark),
                    "daily_price_chg": str(_daily_price_chg_pct(price, prev)),
                    "daily_volume": str(volume),
                }
            return result

        if market == "spot":
            tokens, pairs, ctxs = await self._fetch_spot_metadata()
            result = {}
            for i, pair in enumerate(pairs):
                ctx = ctxs[i] if i < len(ctxs) else {}
                price = _asset_price(ctx)
                if price <= 0.0:
                    continue
                try:
                    prev = float(ctx.get("prevDayPx") or 0)
                    mark = float(ctx.get("markPx") or 0)
                    volume = float(ctx.get("dayNtlVlm") or 0)
                except (TypeError, ValueError):
                    continue
                # Key by raw pair name to match list_tickers symbol convention.
                result[pair["name"]] = {
                    "mark_price": str(mark),
                    "daily_price_chg": str(_daily_price_chg_pct(price, prev)),
                    "daily_volume": str(volume),
                }
            return result

        return {}

    # ------------------------------------------------------------------
    # REST: fetch_depth_snapshot
    # ------------------------------------------------------------------

    async def fetch_depth_snapshot(self, ticker: str, market: str) -> dict:
        body = {"type": "l2Book", "coin": ticker}
        depth = await self._post_json(body)

        levels = depth.get("levels", [[], []])
        bids_raw = levels[0] if len(levels) > 0 else []
        asks_raw = levels[1] if len(levels) > 1 else []

        return {
            "last_update_id": int(depth.get("time", 0)),
            "bids": [{"price": str(level["px"]), "qty": str(level["sz"])} for level in bids_raw],
            "asks": [{"price": str(level["px"]), "qty": str(level["sz"])} for level in asks_raw],
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
            {
                "method": "subscribe",
                "subscription": {"type": "trades", "coin": ticker},
            }
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
                        "venue": "hyperliquid",
                        "ticker": ticker,
                        "stream_session_id": _current_ssid,
                        "trades": batch,
                    }
                )
                batch = []

            try:
                async with websockets.connect(_WS_URL) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "hyperliquid",
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
                                if msg.get("channel") != "trades":
                                    continue
                                for t in msg.get("data", []):
                                    try:
                                        # "A" = aggressor is seller → is_sell
                                        trade = {
                                            "price": str(t["px"]),
                                            "qty": str(t["sz"]),
                                            "side": "sell" if t["side"] == "A" else "buy",
                                            "ts_ms": int(t["time"]),
                                            "is_liquidation": False,
                                        }
                                        batch.append(trade)
                                    except (KeyError, ValueError, TypeError) as exc:
                                        log.debug("hyperliquid trade parse error: %s", exc)
                            except (orjson.JSONDecodeError, ValueError, TypeError) as exc:
                                log.debug("hyperliquid trade parse error: %s", exc)
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
                    log.warning("hyperliquid trade disconnected: %s", exc)
                else:
                    log.error("hyperliquid trade unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "hyperliquid",
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
            {
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": ticker},
            }
        ).decode()
        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            syncer = HyperliquidDepthSyncer(
                venue="hyperliquid",
                ticker=ticker,
                stream_session_id=ssid,
                outbox=outbox,
            )

            try:
                async with websockets.connect(_WS_URL) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "hyperliquid",
                            "ticker": ticker,
                            "stream": "depth",
                        }
                    )

                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        try:
                            msg = orjson.loads(raw)
                            if msg.get("channel") != "l2Book":
                                continue
                            data = msg.get("data", {})
                            levels = data.get("levels", [[], []])
                            syncer.process_message(
                                time=int(data.get("time", 0)),
                                bids=levels[0] if len(levels) > 0 else [],
                                asks=levels[1] if len(levels) > 1 else [],
                            )
                        except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                            log.debug("hyperliquid depth parse error: %s", exc)

            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("hyperliquid depth disconnected: %s", exc)
                else:
                    log.error("hyperliquid depth unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "hyperliquid",
                        "ticker": ticker,
                        "stream": "depth",
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)

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
        subscribe_msg = orjson.dumps(
            {
                "method": "subscribe",
                "subscription": {
                    "type": "candle",
                    "coin": ticker,
                    "interval": timeframe,
                },
            }
        ).decode()
        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            try:
                async with websockets.connect(_WS_URL) as ws:
                    await ws.send(subscribe_msg)
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "hyperliquid",
                            "ticker": ticker,
                            "stream": f"kline_{timeframe}",
                        }
                    )

                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        try:
                            msg = orjson.loads(raw)
                            if msg.get("channel") != "candle":
                                continue
                            k = msg.get("data", {})
                            outbox.append(
                                {
                                    "event": "KlineUpdate",
                                    "venue": "hyperliquid",
                                    "ticker": ticker,
                                    "timeframe": timeframe,
                                    "stream_session_id": ssid,
                                    "kline": {
                                        "open_time_ms": int(k["t"]),
                                        "open": str(k["o"]),
                                        "high": str(k["h"]),
                                        "low": str(k["l"]),
                                        "close": str(k["c"]),
                                        "volume": str(k["v"]),
                                        "is_closed": False,
                                    },
                                }
                            )
                        except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as exc:
                            log.debug("hyperliquid kline parse error: %s", exc)

            except Exception as exc:
                if stop_event.is_set():
                    break
                if isinstance(exc, (websockets.exceptions.ConnectionClosed, OSError, TimeoutError)):
                    log.warning("hyperliquid kline disconnected: %s", exc)
                else:
                    log.error("hyperliquid kline unexpected error: %s", exc)
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "hyperliquid",
                        "ticker": ticker,
                        "stream": f"kline_{timeframe}",
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)
