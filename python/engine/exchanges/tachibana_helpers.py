"""Common helpers for the Tachibana e-shiten adapter.

* `PNoCounter`        — monotonic ``p_no`` request counter (R4). Per-instance
  state, no module-level singleton: tests get clean fixtures, and the future
  Python-only mode can run multiple workers in one process without sharing.
  asyncio is single-threaded, so no `Lock` is needed (F-L5).
* `current_p_sd_date` — ``YYYY.MM.DD-hh:mm:ss.sss`` in JST (R4). Always JST,
  never UTC; the CI lint guard rejects bare `datetime.now()` outside this
  function (MEDIUM-C8).
* `check_response`    — two-stage error judgment (R6, MEDIUM-C5). Returns
  ``None`` on success, otherwise a `TachibanaError` subclass. ``p_errno=""``
  counts as success.

Error classes form a useful hierarchy::

    TachibanaError
    ├── LoginError
    │   └── UnreadNoticesError    # sKinsyouhouMidokuFlg == "1"
    └── SessionExpiredError       # p_errno == "2"
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

JST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TachibanaError(Exception):
    """Base class for all Tachibana API errors."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"Tachibana API error: code={code!r} message={message!r}")


class LoginError(TachibanaError):
    """Authentication-time failures (login API path)."""


class UnreadNoticesError(LoginError):
    """`sKinsyouhouMidokuFlg == "1"` — virtual URLs returned empty."""

    def __init__(self, message: str = "立花からの未読通知があります。ブラウザで確認後に再ログインしてください") -> None:
        super().__init__(code="unread_notices", message=message)


class SessionExpiredError(TachibanaError):
    """`p_errno == "2"` — virtual URL no longer valid."""

    def __init__(self, message: str = "Tachibana セッションが切れています") -> None:
        super().__init__(code="session_expired", message=message)


# ---------------------------------------------------------------------------
# p_no counter
# ---------------------------------------------------------------------------


class PNoCounter:
    """Per-instance monotonic `p_no` generator.

    Initialized to current Unix seconds so that values stay roughly increasing
    across process restarts (R4 explicitly requires monotonicity even after a
    cold start). asyncio is single-threaded — no `Lock` (F-L5).
    """

    __slots__ = ("_value",)

    def __init__(self) -> None:
        # allowlist: PNo monotonic init from Unix seconds (R4). The
        # MEDIUM-C8 CI guard rejects bare time.time() / datetime.now() outside
        # current_p_sd_date — this call is the documented exception because
        # p_no MUST stay roughly increasing across cold restarts.
        self._value = int(time.time())

    def next(self) -> int:
        self._value += 1
        return self._value

    def peek(self) -> int:
        """Return the last value returned by `next()` (0-state before first `next()`).

        Useful for tests/debug logs; not part of the request path. Call `next()`
        whenever you need a fresh `p_no` to send.
        """
        return self._value


# ---------------------------------------------------------------------------
# p_sd_date
# ---------------------------------------------------------------------------


def current_p_sd_date() -> str:
    """Return ``YYYY.MM.DD-hh:mm:ss.sss`` in JST (R4).

    Tachibana rejects UTC timestamps. All call sites for ``p_sd_date`` MUST
    route through this function (CI lint guard MEDIUM-C8).
    """
    now = datetime.now(JST)
    return now.strftime("%Y.%m.%d-%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


# ---------------------------------------------------------------------------
# check_response (R6)
# ---------------------------------------------------------------------------


def check_response(payload: Mapping[str, Any]) -> TachibanaError | None:
    """Two-stage error judgment (R6).

    Order:
        1. ``p_errno``  — API/transport-level. ``""`` and ``"0"`` are success.
           ``"2"`` is `SessionExpiredError`; anything else is `TachibanaError`.
        2. ``sResultCode`` — business-level. ``""`` and ``"0"`` are success.
        3. ``sKinsyouhouMidokuFlg == "1"`` — `UnreadNoticesError`.

    Returns:
        ``None`` on success, otherwise a `TachibanaError` subclass instance
        (callers `raise` it or wrap into IPC `VenueError`).
    """
    p_errno = payload.get("p_errno", "")
    if p_errno not in ("", "0"):
        message = str(payload.get("p_err") or payload.get("sResultText") or "")
        if p_errno == "2":
            return SessionExpiredError(message=message or "Tachibana セッションが切れています")
        return TachibanaError(code=str(p_errno), message=message)

    sresult = payload.get("sResultCode", "")
    if sresult not in ("", "0"):
        message = str(payload.get("sResultText") or "")
        return TachibanaError(code=str(sresult), message=message)

    if str(payload.get("sKinsyouhouMidokuFlg", "")) == "1":
        return UnreadNoticesError()

    return None
