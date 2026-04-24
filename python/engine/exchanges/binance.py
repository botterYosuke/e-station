"""Binance exchange worker — REST and WebSocket for Phase 1."""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import logging
import zipfile
from collections import deque
from datetime import timezone
from pathlib import Path
from typing import Any

import httpx
import orjson
import websockets

from engine.exchanges.base import ExchangeWorker, OnSsidUpdate
from engine.limiter import BinanceLimiter

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint constants
# ---------------------------------------------------------------------------

_SPOT_REST = "https://api.binance.com"
_LINEAR_REST = "https://fapi.binance.com"
_INVERSE_REST = "https://dstream.binance.com"

_SPOT_WS = "wss://stream.binance.com:9443"
_LINEAR_WS = "wss://fstream.binance.com"
_INVERSE_WS = "wss://dstream.binance.com"

_TRADE_BATCH_INTERVAL = 0.033  # 33 ms


def _depth_levels(levels: list[list[str]]) -> list[dict[str, str]]:
    return [{"price": price, "qty": qty} for price, qty in levels]


def _rest_base(market: str) -> str:
    if market == "linear_perp":
        return _LINEAR_REST
    if market == "inverse_perp":
        return _INVERSE_REST
    return _SPOT_REST


def _ws_base(market: str) -> str:
    if market == "linear_perp":
        return _LINEAR_WS
    if market == "inverse_perp":
        return _INVERSE_WS
    return _SPOT_WS


def _is_perp(market: str) -> bool:
    return market in ("linear_perp", "inverse_perp")


# ---------------------------------------------------------------------------
# BinanceDepthSyncer
# ---------------------------------------------------------------------------


class BinanceDepthSyncer:
    """Implements the Binance depth consistency protocol for IPC.

    Maintains the gap-detection state machine for a single (ticker, market)
    and emits DepthSnapshot / DepthDiff / DepthGap dicts into outbox.
    """

    MAX_PENDING = 512

    def __init__(
        self,
        *,
        venue: str,
        ticker: str,
        market: str,
        stream_session_id: str,
        snapshot_fetcher: Any,  # async callable () -> snapshot dict
        outbox: Any,  # supports .append(dict)
    ) -> None:
        self._venue = venue
        self._ticker = ticker
        self._market = market
        self._ssid = stream_session_id
        self._snapshot_fetcher = snapshot_fetcher
        self._outbox = outbox
        self._applied_seq: int = 0
        self._pending: deque[dict] = deque()
        self._initialized = False
        self._needs_resync = False
        # True only for the diff immediately following a snapshot. Permits the
        # relaxed `U <= applied+1 <= u` match. Cleared after first valid diff.
        self._just_applied_snapshot = False

    @property
    def needs_resync(self) -> bool:
        return self._needs_resync

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def queue_diff(self, diff: dict) -> None:
        """Buffer a diff event received before initialize() is called.

        If the buffer overflows, we cannot drop silently (spec §4.4 — depth
        diffs must not be dropped). Emit a gap and force resync instead.
        """
        if len(self._pending) >= self.MAX_PENDING:
            self._pending.clear()
            self._emit_gap()
            self._needs_resync = True
            return
        self._pending.append(diff)

    async def initialize(self) -> None:
        """Fetch the snapshot and replay any buffered diffs."""
        await self._apply_snapshot()
        self._initialized = True

    async def apply_diff(self, diff: dict) -> None:
        """Apply a live diff event. Detects gaps and triggers resync."""
        if not self._initialized or self._needs_resync:
            self.queue_diff(diff)
            return
        self._process_diff(diff)

    async def resync(self) -> None:
        """Force a new snapshot fetch and replay buffered diffs."""
        self._needs_resync = False
        self._pending.clear()
        await self._apply_snapshot()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _apply_snapshot(self) -> None:
        snapshot = await self._snapshot_fetcher()
        self._applied_seq = snapshot["last_update_id"]
        self._just_applied_snapshot = True

        self._outbox.append(
            {
                "event": "DepthSnapshot",
                "venue": self._venue,
                "ticker": self._ticker,
                "market": self._market,
                "stream_session_id": self._ssid,
                "sequence_id": self._applied_seq,
                "bids": snapshot["bids"],
                "asks": snapshot["asks"],
            }
        )

        # Replay buffered diffs, dropping stale ones.
        pending = list(self._pending)
        self._pending.clear()
        for diff in pending:
            self._process_diff(diff, replaying=True)
            if self._needs_resync:
                # A replayed diff revealed a gap — stop replaying;
                # caller should trigger another resync.
                break

    def _process_diff(self, diff: dict, *, replaying: bool = False) -> None:
        final_id: int = diff["u"]

        # Drop stale events (already covered by snapshot)
        if final_id <= self._applied_seq:
            return

        if self._applied_seq == 0:
            # Not yet initialised (should not happen for live diffs — defensive)
            return

        if not self._is_first_valid(diff):
            # Gap detected — emit gap and require resync in both live and
            # replay paths. Previously replay-mode silently skipped.
            self._emit_gap()
            self._needs_resync = True
            if not replaying:
                self.queue_diff(diff)
            return

        # Valid diff — emit and advance cursor
        self._outbox.append(
            {
                "event": "DepthDiff",
                "venue": self._venue,
                "ticker": self._ticker,
                "market": self._market,
                "stream_session_id": self._ssid,
                "sequence_id": final_id,
                "prev_sequence_id": self._applied_seq,
                "bids": _depth_levels(diff.get("b", [])),
                "asks": _depth_levels(diff.get("a", [])),
            }
        )
        self._applied_seq = final_id
        self._just_applied_snapshot = False

    def _is_first_valid(self, diff: dict) -> bool:
        """Check whether this diff connects contiguously from applied_seq.

        Perp (futures) protocol: `pu` is the prev final update id and must
        equal `applied_seq` for strict continuity. Immediately after a
        snapshot the first diff may straddle the boundary, in which case
        `U <= applied_seq+1 <= u` is acceptable.

        Spot protocol: no `pu`; require `U == applied_seq + 1` strictly
        (straddle tolerated only immediately after a snapshot).
        """
        final_id: int = diff["u"]
        first_id: int = diff["U"]
        pu: int | None = diff.get("pu")
        next_expected = self._applied_seq + 1

        if pu is not None:
            if pu == self._applied_seq:
                return True
            if self._just_applied_snapshot:
                return first_id <= next_expected <= final_id
            return False

        # Spot
        if self._just_applied_snapshot:
            return first_id <= next_expected <= final_id
        return first_id == next_expected

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
# BinanceWorker
# ---------------------------------------------------------------------------


class BinanceWorker(ExchangeWorker):
    """Handles Binance REST and WebSocket data acquisition."""

    def __init__(self, proxy: str | None = None) -> None:
        self._limiter = BinanceLimiter()
        self._proxy = proxy
        self._client: httpx.AsyncClient | None = None

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
        base = _rest_base(market)
        if market == "linear_perp":
            url = f"{base}/fapi/v1/exchangeInfo"
            weight = 1
        elif market == "inverse_perp":
            url = f"{base}/dapi/v1/exchangeInfo"
            weight = 1
        else:
            url = f"{base}/api/v3/exchangeInfo"
            weight = 20

        data = await self._get_json(url, weight)
        symbols = data.get("symbols", [])

        result = []
        for sym in symbols:
            if sym.get("contractType") and sym["contractType"] != "PERPETUAL":
                continue
            quote = sym.get("quoteAsset", "")
            if quote not in ("USDT", "USD", ""):
                continue
            status = sym.get("status", "")
            if status and status != "TRADING":
                continue

            filters = sym.get("filters", [])
            price_filter = next(
                (f for f in filters if f.get("filterType") == "PRICE_FILTER"), None
            )
            lot_filter = next(
                (f for f in filters if f.get("filterType") == "LOT_SIZE"), None
            )
            if price_filter is None or lot_filter is None:
                continue

            result.append(
                {
                    "symbol": sym["symbol"],
                    "min_ticksize": float(price_filter["tickSize"]),
                    "min_qty": float(lot_filter["minQty"]),
                    "contract_size": sym.get("contractSize"),
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
        base = _rest_base(market)
        if market == "linear_perp":
            endpoint = "/fapi/v1/klines"
            weight = 2
        elif market == "inverse_perp":
            endpoint = "/dapi/v1/klines"
            weight = 2
        else:
            endpoint = "/api/v3/klines"
            weight = 2

        url = f"{base}{endpoint}?symbol={ticker}&interval={timeframe}&limit={limit}"
        if start_ms is not None:
            url += f"&startTime={start_ms}"
        if end_ms is not None:
            url += f"&endTime={end_ms}"

        rows = await self._get_json(url, weight)

        klines = []
        for row in rows:
            entry: dict = {
                "open_time_ms": row[0],
                "open": str(row[1]),
                "high": str(row[2]),
                "low": str(row[3]),
                "close": str(row[4]),
                "volume": str(row[5]),
                "is_closed": True,
            }
            if len(row) >= 10:
                entry["taker_buy_volume"] = str(row[9])
            klines.append(entry)

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
        if market not in ("linear_perp", "inverse_perp"):
            return []

        base = _rest_base(market)
        if market == "linear_perp":
            url = f"{base}/futures/data/openInterestHist?symbol={ticker}&period={timeframe}&limit={limit}"
            weight = 12
        else:
            url = f"{base}/futures/data/openInterestHist?symbol={ticker}&period={timeframe}&limit={limit}&contractType=PERPETUAL"
            weight = 1

        if start_ms is not None:
            url += f"&startTime={start_ms}"
        if end_ms is not None:
            url += f"&endTime={end_ms}"

        rows = await self._get_json(url, weight)

        return [
            {
                "ts_ms": int(row["timestamp"]),
                "open_interest": str(row["sumOpenInterest"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # REST: fetch_ticker_stats
    # ------------------------------------------------------------------

    async def fetch_ticker_stats(self, ticker: str, market: str) -> dict:
        base = _rest_base(market)
        if market == "linear_perp":
            url = f"{base}/fapi/v1/ticker/24hr"
            weight = 40
        elif market == "inverse_perp":
            url = f"{base}/dapi/v1/ticker/24hr"
            weight = 40
        else:
            url = f"{base}/api/v3/ticker/24hr"
            weight = 80

        rows = await self._get_json(url, weight)

        def _parse(item: dict) -> dict:
            return {
                "mark_price": str(item["lastPrice"]),
                "daily_price_chg": str(item["priceChangePercent"]),
                "daily_volume": str(item.get("quoteVolume", item.get("volume", "0"))),
            }

        if ticker == "__all__":
            return {item["symbol"]: _parse(item) for item in rows if "symbol" in item}

        for item in rows:
            if item.get("symbol") == ticker:
                return _parse(item)
        raise ValueError(f"Ticker {ticker} not found in stats response")

    # ------------------------------------------------------------------
    # REST: fetch_depth_snapshot
    # ------------------------------------------------------------------

    async def fetch_depth_snapshot(self, ticker: str, market: str) -> dict:
        base = _rest_base(market)
        if market == "linear_perp":
            url = f"{base}/fapi/v1/depth?symbol={ticker}&limit=1000"
            weight = 20
        elif market == "inverse_perp":
            url = f"{base}/dapi/v1/depth?symbol={ticker}&limit=1000"
            weight = 20
        else:
            url = f"{base}/api/v3/depth?symbol={ticker}&limit=1000"
            weight = 50

        data = await self._get_json(url, weight)

        return {
            "last_update_id": data["lastUpdateId"],
            "bids": _depth_levels(data["bids"]),
            "asks": _depth_levels(data["asks"]),
        }

    # ------------------------------------------------------------------
    # REST: fetch_trades (historical + intraday)
    # ------------------------------------------------------------------

    async def fetch_trades(
        self,
        ticker: str,
        market: str,
        start_ms: int,
        *,
        end_ms: int = 0,
        data_path: Path | None = None,
    ) -> list[dict]:
        """Fetch trades for exactly one calendar day starting from start_ms.

        For intraday dates (today), uses the aggTrades REST endpoint directly.
        For historical dates, downloads the daily aggTrades zip from
        data.binance.vision and caches it locally under data_path.
        Each call returns one day; callers that need multiple days should
        advance start_ms to the next day's midnight after each batch.
        """
        today_midnight_ms = int(
            dt.datetime.now(tz=timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )

        if start_ms >= today_midnight_ms:
            return await self._fetch_intraday_trades(ticker, market, start_ms, end_ms=end_ms)

        from_date = dt.datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).date()

        # Cap to_time at single-day boundary (contract: fetch_trades returns trades in [start_ms, effective_end_ms) only)
        _DAY_MS = 86_400_000
        from_day_index = start_ms // _DAY_MS
        next_day_start = (from_day_index + 1) * _DAY_MS
        effective_end_ms = end_ms if end_ms else next_day_start - 1
        if end_ms:
            effective_end_ms = min(end_ms, next_day_start - 1)

        try:
            trades = await self._fetch_historical_trades(ticker, market, from_date, data_path)
            result = [t for t in trades if t["ts_ms"] >= start_ms]
            if effective_end_ms:
                result = [t for t in result if t["ts_ms"] <= effective_end_ms]
            return result
        except (httpx.HTTPError, ValueError, zipfile.BadZipFile, OSError) as exc:
            log.warning(
                "Historical trade fetch failed for %s %s %s, falling back to intraday: %s",
                ticker, market, from_date, exc,
            )
            return await self._fetch_intraday_trades(ticker, market, start_ms, end_ms=effective_end_ms)

    async def _fetch_intraday_trades(
        self, ticker: str, market: str, start_ms: int, *, end_ms: int = 0
    ) -> list[dict]:
        _DAY_MS = 86_400_000
        if end_ms == 0:
            # Match the [start_ms, next_day_start - 1] inclusive contract used by
            # fetch_trades() and the Rust historical path so direct callers do not
            # pick up a trade at exactly the next day's 00:00:00.000.
            end_ms = (start_ms // _DAY_MS + 1) * _DAY_MS - 1
        base = _rest_base(market)
        if market == "linear_perp":
            endpoint = f"{base}/fapi/v1/aggTrades"
            weight = 20
        elif market == "inverse_perp":
            endpoint = f"{base}/dapi/v1/aggTrades"
            weight = 20
        else:
            endpoint = f"{base}/api/v3/aggTrades"
            weight = 4

        all_trades: list[dict] = []
        from_id: int | None = None

        while True:
            if from_id is not None:
                url = f"{endpoint}?symbol={ticker}&fromId={from_id}&limit=1000"
            else:
                url = f"{endpoint}?symbol={ticker}&limit=1000&startTime={start_ms}"

            data = await self._get_json(url, weight)
            if not data:
                break

            batch = [
                {
                    "ts_ms": int(row["T"]),
                    "price": str(row["p"]),
                    "qty": str(row["q"]),
                    "side": "sell" if row["m"] else "buy",
                    "is_liquidation": False,
                }
                for row in data
            ]

            if end_ms:
                batch = [t for t in batch if t["ts_ms"] <= end_ms]

            all_trades.extend(batch)

            if len(data) < 1000:
                break

            if end_ms and int(data[-1]["T"]) > end_ms:
                break

            from_id = int(data[-1]["a"]) + 1

        return all_trades

    async def _fetch_historical_trades(
        self,
        ticker: str,
        market: str,
        date: dt.date,
        data_path: Path | None,
    ) -> list[dict]:
        """Download or serve from cache the aggTrades zip for a single date."""
        if market == "linear_perp":
            subpath = f"data/futures/um/daily/aggTrades/{ticker}"
        elif market == "inverse_perp":
            subpath = f"data/futures/cm/daily/aggTrades/{ticker}"
        else:
            subpath = f"data/spot/daily/aggTrades/{ticker}"

        date_str = date.strftime("%Y-%m-%d")
        zip_filename = f"{ticker}-aggTrades-{date_str}.zip"

        # Cache path
        if data_path is not None:
            cache_dir = data_path / subpath
            cache_dir.mkdir(parents=True, exist_ok=True)
            zip_path = cache_dir / zip_filename
        else:
            zip_path = None

        if zip_path is not None and zip_path.exists():
            log.info("Using cached %s", zip_path)
            zip_bytes = zip_path.read_bytes()
        else:
            url = f"https://data.binance.vision/{subpath}/{zip_filename}"
            log.info("Downloading %s", url)
            client = await self._http()
            resp = await client.get(url)
            if not resp.is_success:
                raise ValueError(
                    f"data.binance.vision returned {resp.status_code} for {url}"
                )
            zip_bytes = resp.content
            if zip_path is not None:
                tmp_path = zip_path.with_suffix(".zip.tmp")
                tmp_path.write_bytes(zip_bytes)
                tmp_path.replace(zip_path)

        # Parse the zip → CSV
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # The zip may contain one or more CSV files
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                raise ValueError(f"No CSV in zip for {ticker} {date_str}")
            with zf.open(csv_names[0]) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
                trades = []
                skipped = 0
                for row in reader:
                    if len(row) < 7:
                        skipped += 1
                        continue
                    try:
                        time_ms = int(row[5])
                        is_sell = row[6].strip().lower() == "true"
                        trades.append(
                            {
                                "ts_ms": time_ms,
                                "price": row[1].strip(),
                                "qty": row[2].strip(),
                                "side": "sell" if is_sell else "buy",
                                "is_liquidation": False,
                            }
                        )
                    except (ValueError, IndexError):
                        skipped += 1
                        continue
                if skipped > 0:
                    log.warning(
                        "skipped %d malformed aggTrades rows for %s %s",
                        skipped, ticker, date_str,
                    )

        return trades

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
        symbol = ticker.lower()
        ws_base = _ws_base(market)
        url = f"{ws_base}/stream?streams={symbol}@aggTrade"
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
                        "venue": "binance",
                        "ticker": ticker,
                        "market": market,
                        "stream_session_id": _current_ssid,
                        "trades": batch,
                    }
                )
                batch = []

            try:
                async with websockets.connect(url) as ws:
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "binance",
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
                    try:
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            try:
                                msg = orjson.loads(raw)
                                data = msg.get("data", {})
                                if not data:
                                    continue

                                trade = {
                                    "price": data["p"],
                                    "qty": data["q"],
                                    "side": "sell" if data["m"] else "buy",
                                    "ts_ms": data["T"],
                                    "is_liquidation": False,
                                }
                                batch.append(trade)
                            except Exception as exc:
                                log.warning("trade parse error: %s", exc)
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
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "binance",
                        "ticker": ticker,
                        "stream": "trade",
                        "market": market,
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
        symbol = ticker.lower()
        ws_base = _ws_base(market)
        url = f"{ws_base}/stream?streams={symbol}@depth@100ms"

        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            async def _fetch_snapshot() -> dict:
                return await self.fetch_depth_snapshot(ticker, market)

            syncer = BinanceDepthSyncer(
                venue="binance",
                ticker=ticker,
                market=market,
                stream_session_id=ssid,
                snapshot_fetcher=_fetch_snapshot,
                outbox=outbox,
            )

            try:
                async with websockets.connect(url) as ws:
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "binance",
                            "ticker": ticker,
                            "stream": "depth",
                            "market": market,
                        }
                    )

                    init_task = asyncio.create_task(syncer.initialize())

                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        try:
                            msg = orjson.loads(raw)
                            data = msg.get("data", {})
                            if not data:
                                continue

                            diff = {
                                "type": "perp_diff" if _is_perp(market) else "spot_diff",
                                "U": data["U"],
                                "u": data["u"],
                                "pu": data.get("pu"),
                                "b": data.get("b", []),
                                "a": data.get("a", []),
                                "T": data.get("T", data.get("E", 0)),
                            }

                            if init_task.done():
                                await syncer.apply_diff(diff)
                                if syncer.needs_resync:
                                    await syncer.resync()
                            else:
                                syncer.queue_diff(diff)
                        except Exception as exc:
                            log.warning("depth parse error: %s", exc)

                    if not init_task.done():
                        init_task.cancel()

            except Exception as exc:
                if stop_event.is_set():
                    break
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "binance",
                        "ticker": ticker,
                        "stream": "depth",
                        "market": market,
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
        symbol = ticker.lower()
        ws_base = _ws_base(market)
        url = f"{ws_base}/stream?streams={symbol}@kline_{timeframe}"
        conn_counter = 0

        while not stop_event.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)

            try:
                async with websockets.connect(url) as ws:
                    outbox.append(
                        {
                            "event": "Connected",
                            "venue": "binance",
                            "ticker": ticker,
                            "stream": f"kline_{timeframe}",
                            "market": market,
                        }
                    )
                    async for raw in ws:
                        if stop_event.is_set():
                            break
                        try:
                            msg = orjson.loads(raw)
                            data = msg.get("data", {})
                            k = data.get("k", {})
                            if not k:
                                continue

                            outbox.append(
                                {
                                    "event": "KlineUpdate",
                                    "venue": "binance",
                                    "ticker": ticker,
                                    "market": market,
                                    "timeframe": timeframe,
                                    "stream_session_id": ssid,
                                    "kline": {
                                        "open_time_ms": k["t"],
                                        "open": k["o"],
                                        "high": k["h"],
                                        "low": k["l"],
                                        "close": k["c"],
                                        "volume": k["v"],
                                        "is_closed": k["x"],
                                    },
                                }
                            )
                        except Exception as exc:
                            log.warning("kline parse error: %s", exc)

            except Exception as exc:
                if stop_event.is_set():
                    break
                outbox.append(
                    {
                        "event": "Disconnected",
                        "venue": "binance",
                        "ticker": ticker,
                        "stream": f"kline_{timeframe}",
                        "market": market,
                        "reason": str(exc),
                    }
                )
                await asyncio.sleep(1.0)
