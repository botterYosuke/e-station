"""Tachibana e-shiten exchange worker (B2 — master + list_tickers + fetch_klines + ticker_stats).

Phase 1 scope:

* `_ensure_master_loaded` is the single entry point that lazily downloads
  the per-day master via ``CLMEventDownload`` (sCLMID = ``CLMIssueMstKabu``
  / ``CLMIssueSizyouMstKabu`` / ``CLMYobine``). The double-checked
  ``asyncio.Lock`` + ``asyncio.Event`` pattern (plan §T4 L513-524) keeps
  the download single-flight even when ``list_tickers`` and
  ``fetch_ticker_stats`` race at startup.
* `list_tickers` joins ``CLMIssueMstKabu`` (display names) with
  ``CLMIssueSizyouMstKabu`` (per-market lot size + ``sYobineTaniNumber``)
  and emits one dict per (issue, market) pair. ``min_ticksize`` is left
  unresolved here in Phase 1 — see the design-decision note below.
* `fetch_klines` rejects any ``timeframe`` other than the wire literal
  ``"1d"`` (HIGH-U-11) and routes to ``CLMMfdsGetMarketPriceHistory`` via
  the ``sUrlPrice`` virtual URL.
* `fetch_ticker_stats` calls ``CLMMfdsGetMarketPrice`` (also via
  ``sUrlPrice``) and reshapes the response into a flat 24h-stats dict.
* trade / depth / kline streams and ``fetch_open_interest`` are deferred
  to T5 and raise ``NotImplementedError`` so the ABC contract still holds.

`min_ticksize` resolution (B2 design decision — plan §T4 L537):

  data-mapping.md §5 (A) requires a single fixed tick value at
  TickerInfo construction time. Since the master endpoints used here
  (``CLMIssueSizyouMstKabu``) do not carry a snapshot price, this
  implementation defers the lookup to Rust by always emitting
  ``yobine_code`` on the ticker dict (the resolved-tick map is built
  Rust-side from the same ``CLMYobine`` payload — wired by B3
  HIGH-U-9). When a snapshot price *is* in hand we cap at
  ``sKizunPrice_1`` of the relevant ``CLMYobine`` row as a conservative
  fallback. This keeps Phase 1 minimal and avoids a double tick-table
  copy at the IPC boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from .base import ExchangeWorker, OnSsidUpdate
from .tachibana_auth import TachibanaSession
from .tachibana_codec import decode_response_body
from .tachibana_helpers import (
    JST,
    PNoCounter,
    TachibanaError,
    check_response,
    current_p_sd_date,
)
from .tachibana_master import (
    YobineBand,
    decode_clm_yobine_record,
)
from .tachibana_url import (
    PriceUrl,
    build_request_url,
)

log = logging.getLogger(__name__)

# Fields routed to ticker dicts. The keys must match the Rust-side
# `TickerMetadataMap` consumer (B3 HIGH-U-9 wiring) — `display_name_ja`
# (Japanese long name) and `display_symbol` (ASCII short name) are pinned
# by plan T0.2 and tests.

_DEFAULT_QUOTE_CURRENCY: str = "JPY"


# ---------------------------------------------------------------------------
# VenueCapabilityError — raised when the worker is asked for something
# Phase 1 explicitly does not support (HIGH-U-11). The server-side IPC
# router maps this to a `VenueError{code: ...}` event.
# ---------------------------------------------------------------------------


class VenueCapabilityError(TachibanaError):
    """Phase 1 capability gate (e.g. timeframes other than ``"1d"``)."""


def current_jst_yyyymmdd(now: datetime | None = None) -> str:
    """Return ``YYYYMMDD`` in JST. Used for both cache filename and the
    in-memory invalidation comparison (HIGH-U-10, plan §T4 L550).
    """
    # Tachibana operating calendar is JST-defined; UTC `today()` would
    # roll the cache 9 hours early.
    if now is None:
        now = datetime.now(JST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(JST)
    else:
        now = now.astimezone(JST)
    return now.strftime("%Y%m%d")


def master_cache_path(cache_dir: Path, *, env: str) -> Path:
    """Compose the per-day master cache path (plan §T4 L531-532)."""
    if env not in ("demo", "prod"):
        raise ValueError(f"master_cache_path: env must be 'demo' or 'prod', got {env!r}")
    return cache_dir / "tachibana" / f"master_{env}_{current_jst_yyyymmdd()}.jsonl"


# ---------------------------------------------------------------------------
# TachibanaWorker
# ---------------------------------------------------------------------------


class TachibanaWorker(ExchangeWorker):
    """Tachibana e-shiten exchange worker.

    Phase 1 supports:
      * `list_tickers("stock")`  — derived from the daily master DL
      * `fetch_klines(..., "1d")` — `CLMMfdsGetMarketPriceHistory`
      * `fetch_ticker_stats(...)` — `CLMMfdsGetMarketPrice`

    Streams and intraday klines are out of scope for B2 / Phase-1 T4.
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        is_demo: bool,
        session: TachibanaSession | None = None,
        p_no_counter: PNoCounter | None = None,
    ) -> None:
        # Master DL coordination (plan §T4 L513-524).
        self._master_lock = asyncio.Lock()
        self._master_loaded = asyncio.Event()
        self._master_records: dict[str, list[dict]] = {}
        self._yobine_table: dict[str, list[YobineBand]] = {}
        # JST date the in-memory master was loaded for (HIGH-U-10).
        self._master_loaded_jst_date: str | None = None

        self._cache_dir = Path(cache_dir)
        self._is_demo = bool(is_demo)
        self._env = "demo" if self._is_demo else "prod"
        self._session = session
        self._p_no_counter = p_no_counter or PNoCounter()

        # HTTP lifecycle mirrors `BinanceWorker` — a single shared client,
        # rebuilt on `set_proxy`.
        self._proxy: str | None = None
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle / HTTP
    # ------------------------------------------------------------------

    def set_session(self, session: TachibanaSession) -> None:
        """Inject the post-login `TachibanaSession` (called by server.py
        after `SetVenueCredentials`). Wired further in B3."""
        self._session = session

    def set_credentials_demo_flag(self, is_demo: bool) -> None:
        """Apply a demo/prod flip; flushes any in-memory master so the next
        `_ensure_master_loaded` reloads from the right environment
        (HIGH-U-10)."""
        if bool(is_demo) != self._is_demo:
            self._is_demo = bool(is_demo)
            self._env = "demo" if self._is_demo else "prod"
            self.invalidate_master()

    async def set_proxy(self, url: str | None) -> None:
        self._proxy = url
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("tachibana: closing httpx client: %s", exc)
            self._client = None

    def capabilities(self) -> dict:
        """Phase 1 capability ad — daily klines only (HIGH-U-11, plan §T4 L548).

        The Rust UI consumes ``supported_timeframes`` to pre-disable non-``"1d"``
        choices in the timeframe dropdown so the user never sends a request the
        worker would reject with ``not_implemented``.
        """
        return {"supported_timeframes": ["1d"]}

    async def prepare(self) -> None:
        # Construct the HTTP client eagerly so the first list_tickers call
        # does not race ClientSession construction. Master DL is left to
        # `_ensure_master_loaded` (kicked by `VenueReady` handling in
        # server.py — see plan §T4 L512). Triggering it here would block
        # `Ready` for several seconds and is not what spec.md §3.3 wants.
        await self._http()

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                proxy=self._proxy,
                timeout=15.0,
                follow_redirects=True,
            )
        return self._client

    async def _http_get(self, url: str) -> bytes:
        client = await self._http()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # Master DL coordination
    # ------------------------------------------------------------------

    def invalidate_master(self) -> None:
        """Flush in-memory master state. Subsequent `_ensure_master_loaded`
        callers will re-download (or re-read the on-disk cache)."""
        self._master_loaded.clear()
        self._master_records = {}
        self._yobine_table = {}
        self._master_loaded_jst_date = None

    def _check_jst_rollover(self) -> None:
        """If the JST date has rolled since the in-memory master was loaded,
        invalidate so callers re-download. Cheap O(1) call site, run from
        the entry of `_ensure_master_loaded`."""
        if self._master_loaded.is_set():
            today = current_jst_yyyymmdd()
            if self._master_loaded_jst_date != today:
                log.info(
                    "tachibana: JST date rolled (%s -> %s) — invalidating in-memory master",
                    self._master_loaded_jst_date,
                    today,
                )
                self.invalidate_master()

    async def _ensure_master_loaded(self) -> None:
        """Lazy single-flight master DL.

        ``asyncio.Event`` alone permits two concurrent callers to both
        observe ``is_set() == False`` and start two downloads. The
        ``Lock`` + ``Event`` double-checked pattern below collapses every
        concurrent race to exactly one download (plan §T4 L513-524).
        """
        self._check_jst_rollover()
        if self._master_loaded.is_set():
            return
        async with self._master_lock:
            if self._master_loaded.is_set():
                return
            # Try the on-disk cache first — when present we skip the
            # network DL entirely.
            if not self._try_load_cached_master():
                await self._download_master()
            self._master_loaded_jst_date = current_jst_yyyymmdd()
            self._master_loaded.set()

    def _try_load_cached_master(self) -> bool:
        """Load today's master cache from disk if it exists. Returns True
        on success, False otherwise (caller falls back to network DL)."""
        path = master_cache_path(self._cache_dir, env=self._env)
        if not path.exists():
            return False
        records: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("tachibana: master cache read failed (%s): %s", path, exc)
            return False
        self._ingest_master_records(records)
        log.info("tachibana: loaded %d master records from cache %s", len(records), path)
        return True

    async def _download_master(self) -> None:
        """Fetch CLMEventDownload, parse, populate `_master_records` /
        `_yobine_table`, and persist to the per-day cache file."""
        if self._session is None:
            raise TachibanaError(
                code="no_session",
                message="tachibana master DL requires a logged-in session",
            )

        from .tachibana_master import MasterStreamParser  # local import (cycle-safe)

        target_clmids = ",".join(
            ["CLMIssueMstKabu", "CLMIssueSizyouMstKabu", "CLMYobine"]
        )
        payload: dict[str, Any] = {
            "p_no": str(self._p_no_counter.next()),
            "p_sd_date": current_p_sd_date(),
            "sCLMID": "CLMEventDownload",
            "sTargetCLMID": target_clmids,
        }
        url = build_request_url(self._session.url_master, payload, sJsonOfmt="4")
        client = await self._http()
        parser = MasterStreamParser()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                parser.feed(chunk)
                if parser.is_complete:
                    break
        records = parser.records()
        self._ingest_master_records(records)
        self._persist_master_cache(records)

    def _ingest_master_records(self, records: list[dict]) -> None:
        """Group raw master records by sCLMID and decode CLMYobine."""
        grouped: dict[str, list[dict]] = {}
        yobine_table: dict[str, list[YobineBand]] = {}
        for rec in records:
            sclmid = str(rec.get("sCLMID", ""))
            if not sclmid:
                continue
            if sclmid == "CLMYobine":
                try:
                    decoded = decode_clm_yobine_record(rec)
                except (ValueError, InvalidOperation, KeyError) as exc:
                    log.warning("tachibana: CLMYobine decode failed: %s", exc)
                    continue
                yobine_table[decoded.sYobineTaniNumber] = list(decoded.bands)
            else:
                grouped.setdefault(sclmid, []).append(rec)
        self._master_records = grouped
        self._yobine_table = yobine_table

    def _persist_master_cache(self, records: list[dict]) -> None:
        path = master_cache_path(self._cache_dir, env=self._env)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fp:
                for rec in records:
                    fp.write(json.dumps(rec, ensure_ascii=False))
                    fp.write("\n")
        except OSError as exc:
            log.warning("tachibana: master cache write failed (%s): %s", path, exc)

    # ------------------------------------------------------------------
    # list_tickers
    # ------------------------------------------------------------------

    async def list_tickers(self, market: str = "stock") -> list[dict]:
        await self._ensure_master_loaded()
        if market != "stock":
            return []
        kabu_rows = self._master_records.get("CLMIssueMstKabu", [])
        sizyou_rows = self._master_records.get("CLMIssueSizyouMstKabu", [])

        # Per-issue display name (CLMIssueMstKabu has the canonical names).
        names_by_code: dict[str, dict] = {
            str(r.get("sIssueCode", "")): r for r in kabu_rows
        }

        out: list[dict] = []
        for sizyou in sizyou_rows:
            code = str(sizyou.get("sIssueCode", ""))
            if not code:
                continue
            kabu = names_by_code.get(code, {})
            display_name_ja = str(kabu.get("sIssueName", ""))
            display_symbol = str(kabu.get("sIssueNameEizi", "")) or code
            yobine_code = str(sizyou.get("sYobineTaniNumber", ""))
            try:
                lot_size: int | None = int(sizyou.get("sBaibaiTaniNumber", "0") or 0) or None
            except (TypeError, ValueError):
                lot_size = None

            entry: dict[str, Any] = {
                "symbol": code,
                "display_name_ja": display_name_ja,
                "display_symbol": display_symbol,
                "lot_size": lot_size,
                "min_qty": lot_size,  # cross-venue alias used by Rust TickerInfo
                "quote_currency": _DEFAULT_QUOTE_CURRENCY,
                "yobine_code": yobine_code,
                "sizyou_c": str(sizyou.get("sSizyouC", "")),
            }
            # B2 design decision: omit min_ticksize when no snapshot price
            # is available; Rust resolves it from yobine_code at TickerInfo
            # construction time (B3 HIGH-U-9). If a fallback floor price
            # ever becomes available here we can populate the key directly.
            out.append(entry)
        return out

    # ------------------------------------------------------------------
    # fetch_klines (CLMMfdsGetMarketPriceHistory)
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
        # HIGH-U-11: wire `"1d"` is the only supported timeframe in Phase 1.
        # Compare against the wire literal — Rust enum internal name is
        # `D1`, but it crosses the IPC as `"1d"` (T0.2 L67).
        if timeframe != "1d":
            raise VenueCapabilityError(
                code="not_implemented",
                message="tachibana supports 1d only in Phase 1",
            )
        await self._ensure_master_loaded()
        if self._session is None:
            raise TachibanaError(
                code="no_session",
                message="tachibana fetch_klines requires a logged-in session",
            )

        sizyou_c = self._lookup_sizyou_c(ticker)
        payload: dict[str, Any] = {
            "p_no": str(self._p_no_counter.next()),
            "p_sd_date": current_p_sd_date(),
            "sCLMID": "CLMMfdsGetMarketPriceHistory",
            "sTargetIssueCode": ticker,
            "sTargetSizyouC": sizyou_c,
        }
        url = build_request_url(self._session.url_price, payload, sJsonOfmt="5")
        body = await self._http_get(url)
        from engine.schemas import MarketPriceHistoryResponse  # local import (cycle-safe)

        data = json.loads(decode_response_body(body))
        err = check_response(data) if isinstance(data, dict) else None
        if err is not None:
            raise err
        parsed = MarketPriceHistoryResponse.model_validate(data)

        rows: list[dict] = []
        for raw in parsed.aCLMMfdsMarketPriceHistoryData:
            row = self._row_to_kline(raw)
            if row is not None:
                rows.append(row)
        # Tachibana returns oldest-first; honour the caller's `limit`
        # by trimming to the most recent N entries.
        if limit and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    @staticmethod
    def _row_to_kline(row: dict) -> dict | None:
        """Reshape one CLMMfdsGetMarketPriceHistory row into the standard
        kline dict (matches `binance.py::fetch_klines` shape)."""
        # Field mapping per data-mapping.md §6.
        # sHFutureBA=open, BB=high, BC=low, BD=close, BE=volume,
        # BF=YYYYMMDD trade date.
        date_str = str(row.get("sHFutureBF", "")).strip()
        if len(date_str) != 8 or not date_str.isdigit():
            return None
        try:
            jst_midnight = datetime(
                int(date_str[0:4]),
                int(date_str[4:6]),
                int(date_str[6:8]),
                tzinfo=JST,
            )
        except ValueError:
            return None
        open_time_ms = int(jst_midnight.timestamp() * 1000)
        return {
            "open_time_ms": open_time_ms,
            "open": str(row.get("sHFutureBA", "")),
            "high": str(row.get("sHFutureBB", "")),
            "low": str(row.get("sHFutureBC", "")),
            "close": str(row.get("sHFutureBD", "")),
            "volume": str(row.get("sHFutureBE", "")),
            "is_closed": True,
        }

    def _lookup_sizyou_c(self, ticker: str, *, default: str = "00") -> str:
        for row in self._master_records.get("CLMIssueSizyouMstKabu", []):
            if str(row.get("sIssueCode", "")) == ticker:
                sc = str(row.get("sSizyouC", "")).strip()
                return sc or default
        return default

    # ------------------------------------------------------------------
    # fetch_ticker_stats (CLMMfdsGetMarketPrice)
    # ------------------------------------------------------------------

    async def fetch_ticker_stats(self, ticker: str, market: str = "stock") -> dict:
        await self._ensure_master_loaded()
        if self._session is None:
            raise TachibanaError(
                code="no_session",
                message="tachibana fetch_ticker_stats requires a logged-in session",
            )
        sizyou_c = self._lookup_sizyou_c(ticker)
        payload: dict[str, Any] = {
            "p_no": str(self._p_no_counter.next()),
            "p_sd_date": current_p_sd_date(),
            "sCLMID": "CLMMfdsGetMarketPrice",
            "sTargetIssueCode": ticker,
            "sTargetSizyouC": sizyou_c,
        }
        url = build_request_url(self._session.url_price, payload, sJsonOfmt="5")
        body = await self._http_get(url)
        from engine.schemas import MarketPriceResponse  # local import

        data = json.loads(decode_response_body(body))
        err = check_response(data) if isinstance(data, dict) else None
        if err is not None:
            raise err
        parsed = MarketPriceResponse.model_validate(data)
        if not parsed.aCLMMfdsMarketPriceData:
            return {"symbol": ticker}
        first = parsed.aCLMMfdsMarketPriceData[0]
        return {
            "symbol": ticker,
            "last_price": str(first.get("sCurrentPrice", "")),
            "open": str(first.get("sOpenPrice", "")),
            "high": str(first.get("sHighPrice", "")),
            "low": str(first.get("sLowPrice", "")),
            "volume": str(first.get("sVolume", "")),
            "ts": str(first.get("sCurrentPriceTime", "")),
        }

    # ------------------------------------------------------------------
    # ABC residuals — T5 implements these for real
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
        raise NotImplementedError("tachibana fetch_open_interest is implemented in T5")

    async def fetch_depth_snapshot(self, ticker: str, market: str) -> dict:
        raise NotImplementedError("tachibana fetch_depth_snapshot is implemented in T5")

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
        raise NotImplementedError("tachibana stream_trades is implemented in T5")

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
        raise NotImplementedError("tachibana stream_depth is implemented in T5")

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
        raise NotImplementedError("tachibana stream_kline is implemented in T5")


__all__ = [
    "TachibanaWorker",
    "VenueCapabilityError",
    "current_jst_yyyymmdd",
    "master_cache_path",
]
