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
from collections import Counter
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

# Keys whose values must never be logged in cleartext: virtual URLs (sUrl*) and
# session tokens that grant full account access. Used by `_cb_depth` ST logging
# and by `scripts/diagnose_tachibana_ws.py` (imported as a shared frozenset to
# guarantee the two stay in sync).
# `sUrl*` keys come from the login response (see samples/e_api_login_tel).
_ST_SECRET_KEYS: frozenset[str] = frozenset({
    "sUrlRequest",
    "sUrlMaster",
    "sUrlPrice",
    "sUrlEvent",
    "sUrlEventWebSocket",
    "p_url",
    "p_event_url",
    "url_event_ws",
    "p_session_token",
})

# `p_errno` values that indicate "no error". The Rust client treats empty
# string as 正常 too (see SKILL.md R6: "p_errno は空文字列のことがある").
# `?` is NOT included here — callers historically used it as a sentinel for
# "key missing" but that masks real failures, so we now distinguish None.
_ST_OK_ERRNO_CODES: frozenset[str] = frozenset({"0", "00", ""})

# Default sizyou_c (市場コード) used when the master has not been loaded yet or
# the ticker is not found in CLMIssueSizyouMstKabu.
_SIZYOU_C_FALLBACK: str = "00"

# Whitelist for dynamically-inserted WebSocket URL parameter values. Tachibana
# tickers and sizyou_c (market codes) are numeric in practice; we allow ASCII
# alnum to be conservative for future expansions while still rejecting anything
# that could break URL structure.
_WS_PARAM_ALLOWED_RE: re.Pattern[str] = re.compile(r"^[0-9A-Za-z]+$")

# ST→VenueError rate limit window. ST frames can stream rapidly when the
# session is broken; without a limiter the outbox floods with duplicate
# VenueErrors. Keyed by `code`, last-emit timestamp tracked per stream_depth call.
_ST_VENUE_ERROR_RATE_LIMIT_S: float = 30.0


def build_ws_url(url_event_ws: str, ticker: str, sizyou_c: str) -> str:
    """Build the EVENT WebSocket subscription URL — pure function.

    Parameter values are NOT percent-encoded: the official sample
    (samples/e_api_websocket_receive_tel.py:573-585) appends raw strings.
    Applying func_replace_urlecnode would turn 'ST,KP,FD' into
    'ST%2CKP%2CFD' which the server does not recognise.

    Dynamic values (ticker, sizyou_c) are validated against
    `_WS_PARAM_ALLOWED_RE` (ASCII alnum only) before embedding to prevent
    URL structure breakage and control-char injection.
    """
    for val, name in ((ticker, "ticker"), (sizyou_c, "sizyou_c")):
        if not _WS_PARAM_ALLOWED_RE.match(val):
            raise ValueError(
                f"build_ws_url: {name}={val!r} contains characters outside [0-9A-Za-z]"
                " — control chars and URL structure chars (&?=) are not allowed"
            )
    ws_base = url_event_ws.rstrip("?&")
    params = "&".join([
        "p_rid=22",
        "p_board_no=1000",
        "p_gyou_no=1",
        f"p_mkt_code={sizyou_c}",
        "p_eno=0",
        "p_evt_cmd=ST,KP,FD",
        f"p_issue_code={ticker}",
    ])
    return f"{ws_base}?{params}"

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
    resolve_min_ticksize_for_issue,
)
from .tachibana_url import (
    PriceUrl,
    build_request_url,
)
from . import tachibana_ws as _tachibana_ws
from .tachibana_ws import FdFrameProcessor, TachibanaEventWs

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
        # C2/C4: per-ticker resolved min_ticksize cache (populated in list_tickers,
        # updated from first snapshot price in stream_depth).
        self._ticker_min_ticksize: dict[str, Decimal] = {}

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
        """Inject the post-login `TachibanaSession`.

        Called by ``server.py`` via ``_apply_tachibana_session`` after
        ``startup_login`` / ``RequestVenueLogin`` login success.
        """
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

    def venue_caps(self) -> dict:
        return {"client_aggr_depth": True, "supports_spread_display": True, "qty_norm_kind": "lot"}

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
        self._ticker_min_ticksize = {}

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
            str(r.get("sIssueCode", "")).strip(): r for r in kabu_rows
        }

        out: list[dict] = []
        for sizyou in sizyou_rows:
            code = str(sizyou.get("sIssueCode", "")).strip()
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
                "kind": "stock",
                "symbol": code,
                "display_name_ja": display_name_ja,
                "display_symbol": display_symbol,
                "lot_size": lot_size,
                "min_qty": lot_size,  # cross-venue alias used by Rust TickerInfo
                "quote_currency": _DEFAULT_QUOTE_CURRENCY,
                "yobine_code": yobine_code,
                "sizyou_c": str(sizyou.get("sSizyouC", "")),
                "venue_caps": self.venue_caps(),
            }
            # B5: resolve min_ticksize from CLMYobine table using the
            # conservative no-snapshot-price fallback (finest tick band).
            # KeyError means CLMYobine data is missing for this yobine_code;
            # Rust will then fall back to TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32.
            if self._yobine_table:
                try:
                    tick = resolve_min_ticksize_for_issue(sizyou, self._yobine_table, None)
                    entry["min_ticksize"] = float(tick)
                    self._ticker_min_ticksize[code] = tick  # C2: cache for stream_depth
                except (KeyError, ValueError):
                    pass
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
            # CLMMfdsGetMarketPriceHistory uses sIssueCode/sSizyouC (not sTargetIssueCode/sTargetSizyouC).
            # See sample: e_api_get_histrical_price_daily_tel.py L482-483.
            "sIssueCode": ticker,
            "sSizyouC": sizyou_c,
        }
        url = build_request_url(self._session.url_price, payload, sJsonOfmt="5")
        body = await self._http_get(url)
        from engine.schemas import MarketPriceHistoryResponse  # local import (cycle-safe)

        data = json.loads(decode_response_body(body))
        if not isinstance(data, dict):
            raise TachibanaError(
                code="parse_error",
                message=f"CLMMfdsGetMarketPriceHistory: expected dict response, got {type(data).__name__}",
            )
        err = check_response(data)
        if err is not None:
            raise err
        parsed = MarketPriceHistoryResponse.model_validate(data)

        rows: list[dict] = []
        skipped = 0
        for raw in parsed.aCLMMfdsMarketPriceHistory:
            row = self._row_to_kline(raw)
            if row is None:
                log.debug(
                    "[tachibana] fetch_klines: skipped invalid row sDate=%r",
                    raw.get("sDate"),
                )
                skipped += 1
            else:
                rows.append(row)
        if skipped > 0:
            log.warning(
                "[tachibana] fetch_klines: skipped %d rows with empty/invalid OHLCV for %s",
                skipped,
                ticker,
            )
        # Tachibana returns oldest-first; honour the caller's `limit`
        # by trimming to the most recent N entries.
        if limit and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    @staticmethod
    def _row_to_kline(row: dict) -> dict | None:
        """Reshape one CLMMfdsGetMarketPriceHistory row into the standard
        kline dict (matches `binance.py::fetch_klines` shape)."""
        # Field mapping per sample e_api_get_histrical_price_daily_tel.py L490-495:
        # sDate=YYYYMMDD, pDOP=open, pDHP=high, pDLP=low, pDPP=close, pDV=volume.
        date_str = str(row.get("sDate", "")).strip()
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
        open_p = str(row.get("pDOP", "")).strip()
        high_p = str(row.get("pDHP", "")).strip()
        low_p = str(row.get("pDLP", "")).strip()
        close_p = str(row.get("pDPP", "")).strip()
        volume_v = str(row.get("pDV", "")).strip()
        if not (open_p and high_p and low_p and close_p and volume_v):
            return None
        return {
            "open_time_ms": open_time_ms,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": volume_v,
            "is_closed": True,
        }

    def _lookup_sizyou_c(self, ticker: str) -> str:
        for row in self._master_records.get("CLMIssueSizyouMstKabu", []):
            if str(row.get("sIssueCode", "")) == ticker:
                sc = str(row.get("sSizyouC", "")).strip()
                return sc or _SIZYOU_C_FALLBACK
        return _SIZYOU_C_FALLBACK

    def _lookup_sizyou_record(self, ticker: str) -> dict | None:
        """Return the CLMIssueSizyouMstKabu row for ticker, or None if not found."""
        for row in self._master_records.get("CLMIssueSizyouMstKabu", []):
            if str(row.get("sIssueCode", "")).strip() == ticker:
                return row
        return None

    def _update_min_ticksize_from_price(self, ticker: str, snapshot_price: Decimal) -> None:
        """C4: Re-resolve min_ticksize using a real market price and update cache.

        Called whenever a real exchange price is available (first depth FD frame,
        first trade, REST snapshot) so that the correct yobine band replaces the
        finest-tick startup fallback.
        """
        sizyou = self._lookup_sizyou_record(ticker)
        if sizyou is None or not self._yobine_table:
            return
        try:
            tick = resolve_min_ticksize_for_issue(sizyou, self._yobine_table, snapshot_price)
            self._ticker_min_ticksize[ticker] = tick
        except (KeyError, ValueError):
            pass

    def _try_update_min_ticksize_from_levels(
        self,
        ticker: str,
        bids: list[dict],
        asks: list[dict],
    ) -> None:
        """C4: Update min_ticksize using the first available price from bids or asks.

        Checks bids first, then asks as fallback so that ask-only snapshots (e.g.
        during pre-market or thin books) still resolve the correct yobine band.
        """
        price_str: str | None = None
        if bids:
            price_str = bids[0]["price"]
        elif asks:
            price_str = asks[0]["price"]
        if price_str is None:
            return
        try:
            self._update_min_ticksize_from_price(ticker, Decimal(price_str))
        except Exception:
            pass

    def _normalize_depth_levels(
        self,
        ticker: str,
        bids: list[dict],
        asks: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """C2: Apply price normalization to depth levels using cached min_ticksize."""
        min_ticksize = self._ticker_min_ticksize.get(ticker)
        if min_ticksize is None:
            return bids, asks
        from .normalize import normalize_depth_levels
        return normalize_depth_levels(bids, min_ticksize), normalize_depth_levels(asks, min_ticksize)

    def _normalize_trade_price(self, ticker: str, trade: dict) -> dict:
        """C2: Apply price normalization to a trade dict using cached min_ticksize."""
        min_ticksize = self._ticker_min_ticksize.get(ticker)
        if min_ticksize is None or "price" not in trade:
            return trade
        from .normalize import normalize_price
        result = dict(trade)
        result["price"] = normalize_price(trade["price"], min_ticksize)
        return result

    def _build_ws_url(self, ticker: str) -> str:
        """Instance-level wrapper around the module-level pure function.

        Looks up sizyou_c via the loaded master and delegates URL formatting
        to `build_ws_url`.
        """
        if self._session is None:
            raise RuntimeError("_build_ws_url called without an active session")
        sizyou_c = self._lookup_sizyou_c(ticker)
        return build_ws_url(self._session.url_event_ws, ticker, sizyou_c)

    # ------------------------------------------------------------------
    # fetch_ticker_stats (CLMMfdsGetMarketPrice)
    # ------------------------------------------------------------------

    async def fetch_ticker_stats(self, ticker: str, market: str = "stock") -> dict[str, Any]:
        # Returns dict[str, dict[str, Any]] (bulk placeholder map) when ticker == "__all__",
        # or dict[str, Any] (single ticker stats) otherwise.
        await self._ensure_master_loaded()

        # No active session is required for the __all__ path: master data is already
        # loaded from the on-disk cache and does not need a live HTTP session.
        # The caller (Rust) may issue __all__ before the session is re-established
        # after a reconnect.
        # Bulk case: Rust requests "__all__" to populate the sidebar ticker list.
        # Return placeholder zero-stats for every ticker in the master so that
        # ticker_rows can be created even before any real prices are received.
        if ticker == "__all__":
            sizyou_rows = self._master_records.get("CLMIssueSizyouMstKabu", [])
            if not sizyou_rows:
                log.warning(
                    "[tachibana] fetch_ticker_stats(__all__): CLMIssueSizyouMstKabu is empty"
                    " — master_loaded=%s session=%s",
                    self._master_loaded.is_set(),
                    self._session is not None,
                )
            bulk: dict[str, Any] = {}
            for row in sizyou_rows:
                # .strip() keeps keys consistent with list_tickers(), which also
                # strips sIssueCode before registering symbols with Rust.
                code = str(row.get("sIssueCode", "")).strip()
                if code:
                    bulk[code] = {"mark_price": 0, "daily_price_chg": 0, "daily_volume": 0}
            return bulk

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
            # sTargetColumn is required by the API (error -1 when absent).
            # Field names in the response match the FD codes specified here.
            "sTargetColumn": "pDPP,pDOP,pDHP,pDLP,pDV,tDPP:T",
        }
        url = build_request_url(self._session.url_price, payload, sJsonOfmt="5")
        body = await self._http_get(url)
        from engine.schemas import MarketPriceResponse  # local import

        data = json.loads(decode_response_body(body))
        if not isinstance(data, dict):
            raise TachibanaError(
                code="parse_error",
                message=f"CLMMfdsGetMarketPrice: expected dict, got {type(data).__name__}",
            )
        err = check_response(data)
        if err is not None:
            raise err
        parsed = MarketPriceResponse.model_validate(data)
        if not parsed.aCLMMfdsMarketPrice:
            return {"symbol": ticker}
        first = parsed.aCLMMfdsMarketPrice[0]
        return {
            "symbol": ticker,
            "last_price": str(first.get("pDPP", "")),
            "open": str(first.get("pDOP", "")),
            "high": str(first.get("pDHP", "")),
            "low": str(first.get("pDLP", "")),
            "volume": str(first.get("pDV", "")),
            "ts": str(first.get("tDPP:T", "")),
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
        """Fetch a shallow depth snapshot via CLMMfdsGetMarketPrice (F-M12 / F-M1b).

        Returns bids/asks extracted from the REST response.  The endpoint
        carries 10-level bid/ask (GBP1..GBP10 / GAP1..GAP10).
        """
        if self._session is None:
            raise TachibanaError(
                code="no_session",
                message="tachibana fetch_depth_snapshot requires a logged-in session",
            )
        sizyou_c = self._lookup_sizyou_c(ticker)
        # sTargetColumn is required by the API (error -1 when absent).
        # FD codes for bid (GBP/GBV) and ask (GAP/GAV) 10 levels each.
        depth_cols = ",".join(
            f"pGBP{i},pGBV{i}" for i in range(1, 11)
        ) + "," + ",".join(
            f"pGAP{i},pGAV{i}" for i in range(1, 11)
        )
        payload: dict[str, Any] = {
            "p_no": str(self._p_no_counter.next()),
            "p_sd_date": current_p_sd_date(),
            "sCLMID": "CLMMfdsGetMarketPrice",
            "sTargetIssueCode": ticker,
            "sTargetSizyouC": sizyou_c,
            "sTargetColumn": depth_cols,
        }
        url = build_request_url(self._session.url_price, payload, sJsonOfmt="5")
        body = await self._http_get(url)
        from engine.schemas import MarketPriceResponse  # local import

        data = json.loads(decode_response_body(body))
        if not isinstance(data, dict):
            raise TachibanaError(
                code="parse_error",
                message=f"CLMMfdsGetMarketPrice: expected dict, got {type(data).__name__}",
            )
        err = check_response(data)
        if err is not None:
            raise err
        parsed = MarketPriceResponse.model_validate(data)
        if not parsed.aCLMMfdsMarketPrice:
            recv_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            log.warning("tachibana: fetch_depth_snapshot: empty aCLMMfdsMarketPrice for %s", ticker)
            return {"last_update_id": recv_ts_ms, "bids": [], "asks": [], "recv_ts_ms": recv_ts_ms}
        first = parsed.aCLMMfdsMarketPrice[0]

        recv_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        bids: list[dict[str, str]] = []
        asks: list[dict[str, str]] = []
        for i in range(1, 11):
            bp = str(first.get(f"pGBP{i}", ""))
            bv = str(first.get(f"pGBV{i}", ""))
            ap = str(first.get(f"pGAP{i}", ""))
            av = str(first.get(f"pGAV{i}", ""))
            if bp:
                bids.append({"price": bp, "qty": bv})
            if ap:
                asks.append({"price": ap, "qty": av})

        return {"last_update_id": recv_ts_ms, "bids": bids, "asks": asks, "recv_ts_ms": recv_ts_ms}

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
        if not _tachibana_ws.is_market_open(datetime.now(timezone.utc)):
            outbox.append({
                "event": "Disconnected",
                "venue": "tachibana",
                "ticker": ticker,
                "stream": "trade",
                "market": market,
                "reason": "market_closed",
            })
            return

        if self._session is None:
            log.warning("tachibana: stream_trades: session is None — not streaming %s", ticker)
            outbox.append({
                "event": "Disconnected",
                "venue": "tachibana",
                "ticker": ticker,
                "stream": "trade",
                "market": market,
                "reason": "no_session",
            })
            return

        ws_url = self._build_ws_url(ticker)
        processor = FdFrameProcessor(row="1")
        conn_counter = 0
        _st_stopped: list[bool] = [False]
        # C4: track first trade so we can resolve the correct yobine band before normalizing.
        _first_trade_received: list[bool] = [False]

        while not stop_event.is_set() and not _st_stopped[0]:
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)
            processor.reset()

            async def _cb(frame_type: str, fields: dict, recv_ts_ms: int) -> None:
                if frame_type == "FD":
                    trade, _ = processor.process(fields, recv_ts_ms)
                    if trade:
                        # C4: first trade — resolve correct yobine band from actual price
                        if not _first_trade_received[0] and "price" in trade:
                            try:
                                self._update_min_ticksize_from_price(
                                    ticker, Decimal(trade["price"])
                                )
                            except Exception:
                                pass
                            _first_trade_received[0] = True
                        outbox.append({
                            "event": "Trades",
                            "venue": "tachibana",
                            "ticker": ticker,
                            "market": market,
                            "stream_session_id": ssid,
                            "trades": [self._normalize_trade_price(ticker, trade)],  # C2
                        })
                elif frame_type == "ST":
                    result_code = fields.get("sResultCode", "0")
                    if result_code != "0":
                        _st_stopped[0] = True
                        outbox.append({
                            "event": "Disconnected",
                            "venue": "tachibana",
                            "ticker": ticker,
                            "stream": "trade",
                            "market": market,
                            "reason": "market_closed",
                        })
                        stop_event.set()

            ws_client = TachibanaEventWs(ws_url, stop_event, ticker=ticker, proxy=self._proxy)
            await ws_client.run(_cb)

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
        if not _tachibana_ws.is_market_open(datetime.now(timezone.utc)):
            # spec §3.3 (a): ザラ場前後の初回 snapshot 1 発 — 市場時間外でも REST から
            # 最後の気配を取得して DepthSnapshot を 1 件返してから終了する。
            if self._session is not None:
                try:
                    snapshot = await self.fetch_depth_snapshot(ticker, market)
                    if snapshot.get("bids") or snapshot.get("asks"):
                        log.info(
                            "tachibana: stream_depth market_closed initial snapshot: "
                            "ticker=%s bids=%d asks=%d",
                            ticker, len(snapshot.get("bids", [])), len(snapshot.get("asks", [])),
                        )
                        # C4: update min_ticksize from first available price (bids or asks)
                        snap_bids = snapshot.get("bids", [])
                        snap_asks = snapshot.get("asks", [])
                        self._try_update_min_ticksize_from_levels(ticker, snap_bids, snap_asks)
                        # C2: normalize prices before sending to Rust
                        norm_bids, norm_asks = self._normalize_depth_levels(
                            ticker, snap_bids, snap_asks
                        )
                        outbox.append({
                            "event": "DepthSnapshot",
                            "venue": "tachibana",
                            "ticker": ticker,
                            "market": market,
                            "stream_session_id": f"{stream_session_id}:initial",
                            "bids": norm_bids,
                            "asks": norm_asks,
                            "sequence_id": 0,
                            "recv_ts_ms": snapshot.get("recv_ts_ms", 0),
                        })
                except Exception as exc:
                    log.warning("tachibana: stream_depth market_closed initial snapshot failed for %s: %r", ticker, exc, exc_info=True)
            outbox.append({
                "event": "VenueError",
                "venue": "tachibana",
                "code": "market_closed",
                "message": (
                    "東証は現在市場時間外です（前場 9:00–11:30、後場 12:30–15:30）。\n"
                    "Candlesチャートは引き続き使用できます。"
                ),
            })
            outbox.append({
                "event": "Disconnected",
                "venue": "tachibana",
                "ticker": ticker,
                "stream": "depth",
                "market": market,
                "reason": "market_closed",
            })
            return

        if self._session is None:
            log.warning("tachibana: stream_depth: session is None — not streaming %s", ticker)
            outbox.append({
                "event": "Disconnected",
                "venue": "tachibana",
                "ticker": ticker,
                "stream": "depth",
                "market": market,
                "reason": "no_session",
            })
            return

        try:
            ws_url = self._build_ws_url(ticker)
        except ValueError as exc:
            log.warning("tachibana: stream_depth: invalid ticker=%r: %s", ticker, exc)
            outbox.append({
                "event": "VenueError",
                "venue": "tachibana",
                "ticker": ticker,
                "market": market,
                "code": "invalid_ticker",
                "message": str(exc),
            })
            return
        processor = FdFrameProcessor(row="1")
        # Renamed from depth_keys_seen for clarity (M-H): the flag tracks
        # whether at least one FD frame *with bid/ask keys* has been processed.
        _first_fd_received: list[bool] = [False]
        # Frame-type counts shared across reconnects so the WARN log on
        # depth_unavailable can include them (M-C).
        frame_counts_seen: Counter[str] = Counter()
        # Per-code last-emit time for ST→VenueError rate limiting (H-C).
        # Scoped to stream_depth lifetime; cleared on each WS reconnect so that
        # at least one VenueError is emitted per reconnect attempt (design intent).
        loop = asyncio.get_event_loop()
        st_last_emit: dict[str, float] = {}

        # Inner stop: set by outer stop_event OR by depth safety watchdog.
        _inner_stop = asyncio.Event()

        async def _sync_outer() -> None:
            await stop_event.wait()
            _inner_stop.set()

        async def _safety_watchdog() -> None:
            await asyncio.sleep(_tachibana_ws._DEPTH_SAFETY_TIMEOUT_S)
            if not _first_fd_received[0]:
                log.warning(
                    "tachibana: stream_depth depth_unavailable ticker=%s — "
                    "%.0f s 経過しても FD フレーム（気配付き）が届きません。"
                    " polling fallback に切替えます。"
                    " frame_counts: FD=%d KP=%d ST=%d other=%d",
                    ticker, _tachibana_ws._DEPTH_SAFETY_TIMEOUT_S,
                    frame_counts_seen["FD"], frame_counts_seen["KP"],
                    frame_counts_seen["ST"], frame_counts_seen["other"],
                )
                outbox.append({
                    "event": "VenueError",
                    "venue": "tachibana",
                    "ticker": ticker,
                    "market": market,
                    "code": "depth_unavailable",
                    "message": (
                        "立花の板情報が取得できません"
                        "（FD frame に気配が含まれていません）。"
                        "設定を確認してください"
                    ),
                })
                _inner_stop.set()

        sync_task = asyncio.create_task(_sync_outer())
        safety_task = asyncio.create_task(_safety_watchdog())

        conn_counter = 0
        while not _inner_stop.is_set():
            conn_counter += 1
            ssid = f"{stream_session_id}:{conn_counter}"
            if on_ssid is not None:
                on_ssid(ssid)
            processor.reset()

            async def _cb_depth(frame_type: str, fields: dict, recv_ts_ms: int) -> None:
                # Update shared counts (M-C). Counter handles missing keys as 0.
                if frame_type in ("FD", "KP", "ST"):
                    frame_counts_seen[frame_type] += 1
                else:
                    frame_counts_seen["other"] += 1

                if frame_type == "FD":
                    _, depth = processor.process(fields, recv_ts_ms)
                    if depth:
                        # C4: first FD — update min_ticksize from bids or asks
                        if not _first_fd_received[0]:
                            self._try_update_min_ticksize_from_levels(
                                ticker, depth.get("bids", []), depth.get("asks", [])
                            )
                        _first_fd_received[0] = True
                        # C2: normalize prices before sending to Rust
                        norm_bids, norm_asks = self._normalize_depth_levels(
                            ticker, depth["bids"], depth["asks"]
                        )
                        outbox.append({
                            "event": "DepthSnapshot",
                            "venue": "tachibana",
                            "ticker": ticker,
                            "market": market,
                            "stream_session_id": ssid,
                            "bids": norm_bids,
                            "asks": norm_asks,
                            "sequence_id": depth["sequence_id"],
                            "recv_ts_ms": depth["recv_ts_ms"],
                        })
                elif frame_type == "ST":
                    # ST = server-side status frame. May carry an error.
                    # Use None default to distinguish "key missing" from "key=''" (H-B).
                    p_errno = fields.get("p_errno")
                    # Mask sUrl* / session-token-bearing keys (H-A); other fields
                    # (p_errno, p_status, etc.) are safe diagnostic data.
                    safe_fields = {
                        k: ("***" if k in _ST_SECRET_KEYS else v) for k, v in fields.items()
                    }
                    log.warning(
                        "tachibana: stream_depth ST frame ticker=%s p_errno=%r — "
                        "first_fd_received=%s fields=%r",
                        ticker, p_errno, _first_fd_received[0], safe_fields,
                    )

                    # Decide error code (H-B).
                    code: str | None
                    message: str
                    if p_errno is None:
                        code = "st_no_errno"
                        message = "立花 ST フレームに p_errno キーがありません"
                    elif p_errno in _ST_OK_ERRNO_CODES:
                        code = None
                        message = ""
                    elif p_errno == "2":
                        # Virtual URL invalidated → polling fallback.
                        code = "st_session_expired"
                        message = "立花仮想 URL が失効しました（p_errno=2）。再ログインが必要です"
                    else:
                        code = f"st_errno_{p_errno}"
                        message = f"立花 ST フレームエラー: p_errno={p_errno}"

                    if code is not None:
                        # Rate-limit (H-C): one VenueError per code per
                        # _ST_VENUE_ERROR_RATE_LIMIT_S window.
                        now = loop.time()
                        last = st_last_emit.get(code, 0.0)
                        if now - last >= _ST_VENUE_ERROR_RATE_LIMIT_S:
                            st_last_emit[code] = now
                            outbox.append({
                                "event": "VenueError",
                                "venue": "tachibana",
                                "ticker": ticker,
                                "market": market,
                                "code": code,
                                "message": message,
                            })
                        # Session expired → drop to polling fallback regardless
                        # of rate limiting (M-G).
                        if code == "st_session_expired":
                            _inner_stop.set()

            ws_client = TachibanaEventWs(ws_url, _inner_stop, ticker=ticker, proxy=self._proxy)
            await ws_client.run(_cb_depth, on_connect=st_last_emit.clear)

        for t in (safety_task, sync_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("tachibana: stream_depth: unexpected error cancelling task")

        # depth_unavailable fired → polling fallback (F-M12)
        if not _first_fd_received[0] and not stop_event.is_set():
            await self._depth_polling_fallback(
                ticker, market, stream_session_id, outbox, stop_event
            )

    async def _depth_polling_fallback(
        self,
        ticker: str,
        market: str,
        stream_session_id: str,
        outbox: list[dict],
        stop_event: asyncio.Event,
    ) -> None:
        """CLMMfdsGetMarketPrice polling when depth_unavailable fires (plan §F-M12)."""
        if self._session is None:
            log.warning(
                "tachibana: _depth_polling_fallback: session is None — skipping for %s", ticker
            )
            outbox.append({
                "event": "Disconnected",
                "venue": "tachibana",
                "ticker": ticker,
                "stream": "depth",
                "market": market,
                "reason": "no_session",
            })
            return
        elapsed = 0.0
        poll_counter = 0
        while not stop_event.is_set() and elapsed < _tachibana_ws._DEPTH_POLL_MAX_S:
            if self._session is None:
                log.warning(
                    "tachibana: _depth_polling_fallback: session expired mid-poll for %s"
                    " — stopping fallback", ticker
                )
                outbox.append({
                    "event": "VenueError",
                    "venue": "tachibana",
                    "ticker": ticker,
                    "market": market,
                    "code": "session_expired_during_poll",
                    "message": "立花セッションがポーリング中に失効しました。再ログインしてください",
                })
                return
            try:
                snapshot = await self.fetch_depth_snapshot(ticker, market)
                if snapshot.get("bids") or snapshot.get("asks"):
                    poll_counter += 1
                    poll_bids = snapshot.get("bids", [])
                    poll_asks = snapshot.get("asks", [])
                    # C4: update min_ticksize from REST snapshot price (fix for degraded sessions)
                    self._try_update_min_ticksize_from_levels(ticker, poll_bids, poll_asks)
                    # C2: normalize prices before sending to Rust
                    norm_bids, norm_asks = self._normalize_depth_levels(
                        ticker, poll_bids, poll_asks
                    )
                    outbox.append({
                        "event": "DepthSnapshot",
                        "venue": "tachibana",
                        "ticker": ticker,
                        "market": market,
                        "stream_session_id": f"{stream_session_id}:poll:{poll_counter}",
                        "bids": norm_bids,
                        "asks": norm_asks,
                        "sequence_id": poll_counter,
                        "recv_ts_ms": snapshot.get("recv_ts_ms", 0),
                    })
            except Exception as exc:
                log.warning("tachibana: depth poll error for %s: %s", ticker, exc)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=_tachibana_ws._DEPTH_POLL_INTERVAL_S
                )
                return
            except asyncio.TimeoutError:
                elapsed += _tachibana_ws._DEPTH_POLL_INTERVAL_S
        # ポーリング上限到達（stop_event 未セット）— Rust 側にストリーム終了を通知する
        if not stop_event.is_set():
            outbox.append({
                "event": "Disconnected",
                "venue": "tachibana",
                "ticker": ticker,
                "stream": "depth",
                "market": market,
                "reason": "poll_timeout",
            })

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
