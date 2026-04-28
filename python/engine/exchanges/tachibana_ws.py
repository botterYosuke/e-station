"""Tachibana EVENT WebSocket client (T5).

Responsibilities:
* `is_market_open`      — pure JST market-hours check (東証 前場/後場/クロージング)
* `FdFrameProcessor`    — stateful per-row FD frame → trade + depth synthesis
* `TachibanaEventWs`    — async WS connection manager (reconnect, timeout, ping/pong)

The three concrete stream methods in `tachibana.py`
(`stream_trades` / `stream_depth` / stream_kline stub) delegate here.

Architecture notes:
  * Each WS subscription to a ticker uses p_gyou_no=1 (one row per connection).
  * `stream_trades` and `stream_depth` open separate WS connections for the
    same ticker and only consume the relevant output of `FdFrameProcessor`.
    This mirrors the Binance pattern (two logical streams per ticker) and
    keeps the ABC interface simple.
  * Manual ping/pong (`ping_interval=None`): the server sends a websockets-level
    Ping that the library echoes as Pong automatically when ping_interval is
    disabled — confirmed with the official sample (samples/e_api_websocket_receive_tel.py).
  * 12-second dead-frame timeout: if no frame (including KP keepalive frames)
    arrives within 12 s, the connection is treated as dead and reconnected with
    exponential back-off.  12 s = 5 s (KP interval) * 2 + 2 s jitter.
  * Shift-JIS decode is applied on every received bytes payload before
    `parse_event_frame`; string payloads are passed through decode_response_body
    to handle any mixed-encoding edge cases.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

JST = timezone(timedelta(hours=9))
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market hours (Tokyo Stock Exchange, effective 2024-11-05)
# ---------------------------------------------------------------------------

# 前場  09:00–11:30
# 昼休  11:30–12:30
# 後場  12:30–15:25  (regular)
# クロージング・オークション  15:25–15:30
# → WS connection kept alive until 15:30; closed only from 15:30 onward.
_SESSION_WINDOWS: tuple[tuple[dtime, dtime], ...] = (
    (dtime(9, 0), dtime(11, 30)),
    (dtime(12, 30), dtime(15, 30)),  # 後場 + クロージング合算
)


def is_market_open(now_jst: datetime) -> bool:
    """Return True if `now_jst` falls within any Tokyo trading session.

    Naive datetimes are treated as UTC, matching the convention in
    ``tachibana.py::current_jst_yyyymmdd``.  This function is intentionally
    *not* aware of holiday calendars (Phase 1 design decision — plan §T5
    §holiday-failsafe).
    """
    if now_jst.tzinfo is None:
        now_jst = now_jst.replace(tzinfo=timezone.utc)
    t = now_jst.astimezone(JST).time().replace(tzinfo=None)
    return any(start <= t < end for start, end in _SESSION_WINDOWS)


# ---------------------------------------------------------------------------
# FD frame processor — stateful, per-row
# ---------------------------------------------------------------------------


@dataclass
class FdFrameProcessor:
    """Convert FD (time-and-sales) event frames into trade + depth dicts.

    Designed for use with one ``p_gyou_no`` row per instance. The caller
    must call ``reset()`` when the underlying WebSocket reconnects or the
    subscribed ticker changes, to avoid carrying stale DV/quote state
    across session boundaries (F4).

    ``process()`` returns ``(trade_dict | None, depth_dict | None)``.
    ``trade_dict`` is omitted on the first frame and when DV does not
    increase. ``depth_dict`` is omitted when no bid/ask keys are present.
    """

    row: str

    _prev_dv: Decimal | None = field(default=None, init=False, repr=False)
    _prev_bid: Decimal | None = field(default=None, init=False, repr=False)
    _prev_ask: Decimal | None = field(default=None, init=False, repr=False)
    _prev_trade_price: Decimal | None = field(default=None, init=False, repr=False)
    _sequence_id: int = field(default=0, init=False, repr=False)

    def reset(self) -> None:
        """Reset DV/quote/sequence state (call on reconnect or ticker change)."""
        self._prev_dv = None
        self._prev_bid = None
        self._prev_ask = None
        self._prev_trade_price = None
        self._sequence_id = 0

    def process(
        self, fields: dict[str, str], recv_ts_ms: int
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Process one FD frame.

        Args:
            fields:      ``(key, value)`` pairs from ``parse_event_frame``,
                         converted to a flat dict by the caller.
            recv_ts_ms:  Unix-millisecond receive timestamp (fallback for ts_ms).

        Returns:
            ``(trade | None, depth | None)``
        """
        row = self.row
        dpp_str = fields.get(f"p_{row}_DPP", "")
        dv_str = fields.get(f"p_{row}_DV", "")

        if not dpp_str or not dv_str:
            return None, None

        try:
            dpp = Decimal(dpp_str)
            dv = Decimal(dv_str)
        except InvalidOperation:
            log.warning(
                "tachibana: FdFrameProcessor.process: InvalidOperation for row=%s fields_keys=%s",
                self.row, list(fields.keys())[:5],
            )
            return None, None

        depth = self._extract_depth(fields, recv_ts_ms)
        trade: dict[str, Any] | None = None

        if self._prev_dv is None:
            # First frame: initialize state, no trade (F4).
            self._prev_dv = dv
            self._prev_bid = self._extract_best_bid(fields)
            self._prev_ask = self._extract_best_ask(fields)
        elif dv < self._prev_dv:
            # DV reset (session rollover / new day): reinitialize (F4).
            log.debug(
                "tachibana ws: DV reset row=%s prev=%s curr=%s; reinitializing",
                row, self._prev_dv, dv,
            )
            self._prev_dv = dv
            self._prev_bid = self._extract_best_bid(fields)
            self._prev_ask = self._extract_best_ask(fields)
        else:
            qty = dv - self._prev_dv
            if qty > 0:
                _side = self._determine_side(dpp)
                ts_ms = self._parse_ts_ms(fields, recv_ts_ms, row)
                trade = {
                    "price": str(dpp),
                    "qty": str(qty),
                    "side": _side if _side is not None else "unknown",
                    "ts_ms": ts_ms,
                    "is_liquidation": False,
                }
                self._prev_trade_price = dpp

            # Update quote after trade synthesis (quote rule: use prev frame's quote).
            self._prev_dv = dv
            self._prev_bid = self._extract_best_bid(fields)
            self._prev_ask = self._extract_best_ask(fields)

        return trade, depth

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_side(self, price: Decimal) -> str | None:
        """Quote rule + tick rule (F3, data-mapping §3). Returns None when ambiguous."""
        if self._prev_ask is not None and price >= self._prev_ask:
            return "buy"
        if self._prev_bid is not None and price <= self._prev_bid:
            return "sell"
        # Midpoint: tick rule
        if self._prev_trade_price is not None:
            if price > self._prev_trade_price:
                return "buy"
            if price < self._prev_trade_price:
                return "sell"
        # Ambiguous (F-M8b)
        log.warning("tachibana ws: trade side ambiguous for price %s", price)
        return None

    def _extract_best_bid(self, fields: dict[str, str]) -> Decimal | None:
        v = fields.get(f"p_{self.row}_GBP1", "")
        try:
            return Decimal(v) if v else None
        except InvalidOperation:
            return None

    def _extract_best_ask(self, fields: dict[str, str]) -> Decimal | None:
        v = fields.get(f"p_{self.row}_GAP1", "")
        try:
            return Decimal(v) if v else None
        except InvalidOperation:
            return None

    def _extract_depth(
        self, fields: dict[str, str], recv_ts_ms: int
    ) -> dict[str, Any] | None:
        row = self.row
        bids: list[dict[str, str]] = []
        asks: list[dict[str, str]] = []
        for i in range(1, 11):
            bp = fields.get(f"p_{row}_GBP{i}", "")
            bv = fields.get(f"p_{row}_GBV{i}", "")
            ap = fields.get(f"p_{row}_GAP{i}", "")
            av = fields.get(f"p_{row}_GAV{i}", "")
            if bp:
                bids.append({"price": bp, "qty": bv})
            if ap:
                asks.append({"price": ap, "qty": av})

        if not bids and not asks:
            return None

        self._sequence_id += 1
        return {
            "bids": bids,
            "asks": asks,
            "sequence_id": self._sequence_id,
            "recv_ts_ms": recv_ts_ms,
        }

    @staticmethod
    def _parse_ts_ms(fields: dict[str, str], fallback_ms: int, row: str) -> int:
        """ts_ms priority: DPP:T > p_date > recv fallback (data-mapping §3 F17)."""
        p_date = fields.get("p_date", "")
        if p_date:
            # Format: YYYY.MM.DD-HH:MM:SS.TTT  (T = tenths/hundredths/ms)
            try:
                dt = datetime.strptime(p_date, "%Y.%m.%d-%H:%M:%S.%f")
                dt_jst = dt.replace(tzinfo=JST)
                return int(dt_jst.timestamp() * 1000)
            except ValueError:
                pass
        dpp_t = fields.get(f"p_{row}_DPP:T", "")
        if dpp_t:
            # Format: HH:MM — combine with today's JST date.
            try:
                now_jst = datetime.now(JST)
                t = datetime.strptime(dpp_t, "%H:%M")
                dt_jst = now_jst.replace(
                    hour=t.hour, minute=t.minute, second=0, microsecond=0
                )
                return int(dt_jst.timestamp() * 1000)
            except ValueError:
                pass
        return fallback_ms


# ---------------------------------------------------------------------------
# TachibanaEventWs — async WebSocket connection manager
# ---------------------------------------------------------------------------

# websockets is an optional dependency at import time so that unit tests
# that only exercise FdFrameProcessor can run without it.
try:
    import websockets  # type: ignore[import-untyped]
    from websockets.exceptions import ConnectionClosed  # type: ignore[import-untyped]
    _HAS_WEBSOCKETS = True
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]
    ConnectionClosed = Exception  # type: ignore[assignment,misc]
    _HAS_WEBSOCKETS = False

# How long to wait for any frame (KP or data) before treating the connection
# as dead.  12 s = KP_INTERVAL(5) * 2 + 2 s jitter (plan §T5 M2 修正).
_DEAD_FRAME_TIMEOUT_S: float = 12.0

# Exponential back-off for reconnects: [1, 2, 4, 8, 16, 30] seconds.
_BACKOFF_CAPS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)

# depth_unavailable safety: if no FD frame with bid/ask keys arrives within
# this many seconds of stream_depth start, emit VenueError (plan §T5 MEDIUM-6/F-M12).
_DEPTH_SAFETY_TIMEOUT_S: float = 30.0

# depth_unavailable polling fallback: interval and max duration (F-M12).
_DEPTH_POLL_INTERVAL_S: float = 10.0
_DEPTH_POLL_MAX_S: float = 300.0


class TachibanaEventWs:
    """Async iterator that yields parsed FD-frame field dicts.

    Usage (inside stream_trades / stream_depth)::

        async for frame_type, fields, recv_ts_ms in TachibanaEventWs(url):
            if frame_type == "FD":
                ...

    The iterator handles reconnects internally. It exits (returns) when
    ``stop_event`` is set.  Callers receive ``("Disconnected", {}, ts_ms)``
    tuples on clean disconnects so they can emit a Disconnected IPC event.

    ``stop_event`` must be an ``asyncio.Event`` that the caller sets to
    request graceful shutdown.
    """

    def __init__(
        self,
        url: str,
        stop_event: asyncio.Event,
        *,
        ticker: str,
        venue: str = "tachibana",
        proxy: str | None = None,
    ) -> None:
        from .tachibana_codec import decode_response_body, parse_event_frame

        self._url = url
        self._stop = stop_event
        self._ticker = ticker
        self._venue = venue
        self._proxy = proxy
        self._decode = decode_response_body
        self._parse = parse_event_frame
        self._conn_count = 0

    def __aiter__(self) -> TachibanaEventWs:
        return self

    async def __anext__(self) -> tuple[str, dict[str, str], int]:
        raise StopAsyncIteration  # replaced by run()

    async def run(
        self,
        callback: Any,
    ) -> None:
        """Drive the WS loop, calling ``callback(frame_type, fields, recv_ts_ms)``
        for each received frame.  Returns when ``stop_event`` is set.
        """
        if not _HAS_WEBSOCKETS:
            raise RuntimeError(
                "tachibana_ws.TachibanaEventWs requires the 'websockets' package"
            )
        backoff_idx = 0
        while not self._stop.is_set():
            self._conn_count += 1
            try:
                await self._connect_once(callback)
                backoff_idx = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    return
                backoff = _BACKOFF_CAPS[min(backoff_idx, len(_BACKOFF_CAPS) - 1)]
                backoff_idx += 1
                log.warning(
                    "tachibana ws: %s disconnected (%s); reconnecting in %.0f s",
                    self._ticker, exc, backoff,
                )
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=backoff
                    )
                except asyncio.TimeoutError:
                    pass

    async def _connect_once(self, callback: Any) -> None:
        connect_kwargs: dict[str, Any] = {"ping_interval": None}
        if self._proxy is not None:
            connect_kwargs["proxy"] = self._proxy
        async with websockets.connect(self._url, **connect_kwargs) as ws:
            log.debug("tachibana ws: connected to %s (conn #%d)", self._ticker, self._conn_count)

            loop = asyncio.get_event_loop()
            last_frame_t: list[float] = [loop.time()]
            dead_event = asyncio.Event()

            async def _recv_loop() -> None:
                async for raw in ws:
                    last_frame_t[0] = loop.time()
                    recv_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    # Shift-JIS decode (HIGH-C3-1)
                    if isinstance(raw, bytes):
                        text = self._decode(raw)
                    else:
                        text = raw  # str frame (websockets may deliver as str)

                    pairs = self._parse(text)
                    fields: dict[str, str] = {k: v for k, v in pairs}

                    evt_cmd = fields.get("p_cmd", "")
                    if evt_cmd == "KP":
                        log.debug("tachibana ws: KP recv %s", self._ticker)
                        await callback("KP", fields, recv_ts_ms)
                    elif evt_cmd == "FD":
                        await callback("FD", fields, recv_ts_ms)
                    elif evt_cmd == "ST":
                        await callback("ST", fields, recv_ts_ms)

                    if self._stop.is_set():
                        return

            async def _watchdog() -> None:
                # Adaptive check interval: at most 1s, at most half the timeout.
                interval = min(1.0, _DEAD_FRAME_TIMEOUT_S / 2.0)
                while not self._stop.is_set():
                    await asyncio.sleep(interval)
                    elapsed = loop.time() - last_frame_t[0]
                    if elapsed >= _DEAD_FRAME_TIMEOUT_S:
                        log.warning(
                            "tachibana ws: %s dead-frame timeout (%.1f s); reconnecting",
                            self._ticker, elapsed,
                        )
                        dead_event.set()
                        return

            recv_task = asyncio.create_task(_recv_loop())
            watchdog_task = asyncio.create_task(_watchdog())
            stop_task = asyncio.create_task(self._stop.wait())

            done, pending = await asyncio.wait(
                [recv_task, watchdog_task, stop_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if dead_event.is_set():
                raise ConnectionError("dead-frame timeout")

            # Re-raise any unhandled exception from the recv loop.
            if recv_task in done:
                exc = recv_task.exception()
                if exc is not None:
                    raise exc


__all__ = [
    "FdFrameProcessor",
    "TachibanaEventWs",
    "is_market_open",
]
