"""Tachibana e-shiten authentication and startup-time session validation.

* `login(user_id, password, *, is_demo)` — issue ``CLMAuthLoginRequest``,
  parse ``CLMAuthLoginAck``, return a `TachibanaSession` carrying the four
  REQUEST-side virtual URLs (newtype-tagged) plus the EVENT WebSocket URL.
* `StartupLatch.run_once(coro)` — instance-bound single-flight guard. The
  startup validation coroutine is allowed at most once per worker
  lifecycle (M6 / HIGH-B), independent of any module-level state so that
  pytest fixtures stay isolated (M3).
* `validate_session_on_startup(session, *, _latch)` — light-weight
  ``CLMMfdsGetIssueDetail`` ping (sIssueCode=7203, sSizyouC=00) used only
  during ``startup_login`` to confirm a restored session. Runtime
  ``p_errno=2`` detection takes the `VenueError{code:"session_expired"}`
  path instead — see [architecture.md §6](../../../docs/plan/✅tachibana/architecture.md#6-失敗モードと-ui-表現).

Banner text (`message`) is composed here so that Rust UI never carries
fixed Tachibana-specific strings (F-Banner1).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Optional

import httpx

from .tachibana_codec import decode_response_body
from .tachibana_helpers import (
    LoginError,
    PNoCounter,
    SessionExpiredError,
    TachibanaError,
    UnreadNoticesError,
    check_response,
    current_p_sd_date,
)
from .tachibana_url import (
    BASE_URL_DEMO,
    BASE_URL_PROD,
    EventUrl,
    MasterUrl,
    PriceUrl,
    RequestUrl,
    build_auth_url,
    build_request_url,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User-facing banner text (F-Banner1 / architecture.md §6)
# ---------------------------------------------------------------------------
#
# Python composes the entire `VenueError.message`; Rust UI prints it verbatim
# without any fixed string of its own. Server-side `p_err` / `sResultText` is
# intentionally **not** propagated to the user — it leaks Tachibana-internal
# wording (e.g. "session expired", "invalid credentials") that breaks the
# Japanese banner contract — and is logged separately for triage instead.

_MSG_LOGIN_FAILED = "ログインに失敗しました。ID / パスワードを確認してください"
_MSG_SESSION_EXPIRED_STARTUP = (
    "立花のセッションが切れました（夜間閉局）。再ログインしてください"
)
_MSG_TRANSPORT_ERROR = (
    "立花サーバとの通信に失敗しました。ネットワーク / プロキシ設定を確認してください"
)
_MSG_LOGIN_PARSE_FAILED = "立花ログイン応答の形式が不正です。サポートに連絡してください"
_MSG_VIRTUAL_URL_INVALID = (
    "立花ログイン応答の URL が想定と異なります。サポートに連絡してください"
)


# ---------------------------------------------------------------------------
# Session value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TachibanaSession:
    """Result of a successful login.

    Virtual URLs are stored as their newtype-tagged variants so that
    builders refuse the wrong endpoint at compile / call time. The
    WebSocket URL stays a plain `str` because it is consumed by the
    `websockets` library, not by `build_request_url`.

    `expires_at_ms` is `None` in Phase 1 — the API does not return an
    explicit expiry, so callers always go through
    `validate_session_on_startup` on cold start (F-B3).
    """

    url_request: RequestUrl
    url_master: MasterUrl
    url_price: PriceUrl
    url_event: EventUrl
    url_event_ws: str
    zyoutoeki_kazei_c: str
    expires_at_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# StartupLatch (single-flight guard, M6 / HIGH-B)
# ---------------------------------------------------------------------------


class StartupLatch:
    """Allow `validate_session_on_startup` exactly once per instance.

    Held inside `TachibanaWorker` (or equivalent) so that pytest fixtures
    can spawn an independent latch per test — module-level latches leak
    state across tests in the same process (M3).
    """

    __slots__ = ("_lock", "_done")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._done = False

    async def run_once(self, coro: Awaitable[Any]) -> Any:
        """Run `coro` only on the first call; subsequent calls fail-fast.

        The `finally: self._done = True` is **intentional** — even a
        coroutine that raises consumes the latch. A second call after
        failure is a programmer bug (L6) that should crash the process,
        not retry.
        """
        async with self._lock:
            if self._done:
                # Close the never-awaited coroutine to avoid asyncio's
                # "coroutine was never awaited" warning during the failure.
                if asyncio.iscoroutine(coro):
                    coro.close()
                raise RuntimeError(
                    "validate_session_on_startup は 1 プロセスライフサイクル中に "
                    "1 度だけ呼べる。runtime 経路から呼ばれた場合はプログラムのバグ（L6）。"
                )
            try:
                return await coro
            finally:
                self._done = True


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def _validate_virtual_urls(payload: dict[str, Any]) -> None:
    """MEDIUM-C3-3 — REST URLs must be ``https://``, WS must be ``wss://``."""
    for key in ("sUrlRequest", "sUrlMaster", "sUrlPrice", "sUrlEvent"):
        url = payload.get(key, "")
        if not isinstance(url, str) or not url.startswith("https://"):
            log.error(
                "tachibana login: %s did not start with https:// (got %r)",
                key,
                url,
            )
            raise LoginError(code="login_failed", message=_MSG_VIRTUAL_URL_INVALID)
    ws = payload.get("sUrlEventWebSocket", "")
    if not isinstance(ws, str) or not ws.startswith("wss://"):
        log.error(
            "tachibana login: sUrlEventWebSocket did not start with wss:// (got %r)",
            ws,
        )
        raise LoginError(code="login_failed", message=_MSG_VIRTUAL_URL_INVALID)


def _decode_json(body: bytes) -> dict[str, Any]:
    text = decode_response_body(body)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("tachibana login: JSON parse failed: %s", exc)
        raise LoginError(
            code="login_failed",
            message=_MSG_LOGIN_PARSE_FAILED,
        ) from exc
    if not isinstance(data, dict):
        log.error(
            "tachibana login: response is not a JSON object (got %s)",
            type(data).__name__,
        )
        raise LoginError(code="login_failed", message=_MSG_LOGIN_PARSE_FAILED)
    return data


def _raise_for_error(data: dict[str, Any], *, login_path: bool) -> None:
    """Convert API-level errors to typed exceptions.

    On the login path, generic non-2 `p_errno` and non-zero `sResultCode`
    are wrapped in `LoginError` with a **fixed Japanese banner message**
    (architecture.md §6 / F-Banner1). Server-side `p_err` / `sResultText`
    is logged for triage but never reaches the UI — leaking that text
    would break the banner-text-is-Python's-job contract and surface
    English / inconsistent strings in the Japanese UI.
    """
    err = check_response(data)
    if err is None:
        return
    if isinstance(err, UnreadNoticesError):
        # Default Japanese message is already set on UnreadNoticesError.
        raise err
    if isinstance(err, SessionExpiredError):
        # M1 / L-2: both branches used to override with the same fixed
        # banner string. Collapse to a single raise — the login_path
        # flag has no semantic effect for SessionExpiredError because
        # the runtime path also wants the fixed Japanese banner (the
        # server text is always Tachibana-internal wording).
        raise SessionExpiredError(message=_MSG_SESSION_EXPIRED_STARTUP)
    if login_path:
        log.error(
            "tachibana login: API error code=%r server_message=%r",
            err.code,
            err.message,
        )
        raise LoginError(code=err.code, message=_MSG_LOGIN_FAILED)
    # Runtime path: re-raise the typed error untouched (the runtime error
    # mapper will translate it to a VenueError with proper banner text).
    raise err


async def _safe_get(client: httpx.AsyncClient, url: str) -> bytes:
    """GET `url`, raise `LoginError(transport_error)` on HTTP / network failure.

    Without `raise_for_status()` a 502 / 503 / proxy HTML response would
    flow into `_decode_json` and surface as "JSON parse failed" — burying
    the real transport problem. We map the entire transport-failure
    surface to a single `transport_error` code so the Rust UI banner can
    reason about it without enumerating httpx exception types.
    """
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.error(
            "tachibana login: HTTP %s from server (body prefix=%r)",
            exc.response.status_code,
            exc.response.content[:200],
        )
        raise LoginError(code="transport_error", message=_MSG_TRANSPORT_ERROR) from exc
    except httpx.HTTPError as exc:
        log.error("tachibana login: transport failure: %s", exc)
        raise LoginError(code="transport_error", message=_MSG_TRANSPORT_ERROR) from exc
    return resp.content


async def login(
    user_id: str,
    password: str,
    *,
    is_demo: bool,
    p_no_counter: PNoCounter,
    http_client: Optional[httpx.AsyncClient] = None,
) -> TachibanaSession:
    """Issue ``CLMAuthLoginRequest`` and return a `TachibanaSession`.

    `p_no_counter` is **required** so that retries / startup re-login can
    never reuse a `p_no` already accepted by the server (R4 monotonic
    contract). Callers hold a `PNoCounter` on the worker instance; this
    function calls `.next()` exactly once per HTTP attempt.

    Raises:
        UnreadNoticesError: ``sKinsyouhouMidokuFlg=='1'`` (HIGH-C2-1).
        SessionExpiredError: ``p_errno=='2'``.
        LoginError: any other auth-time failure (mapped to
            `VenueError{code:"login_failed"}` upstream).
    """
    base = BASE_URL_DEMO if is_demo else BASE_URL_PROD
    payload: dict[str, Any] = {
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMAuthLoginRequest",
        "sUserId": user_id,
        "sPassword": password,
    }
    url = build_auth_url(base, payload, sJsonOfmt="5")

    own_client = http_client is None
    # Use explicit per-component timeouts instead of a single scalar.
    # On Windows, a scalar timeout of 15.0 is treated as read-only and
    # does not bound the connect phase when the virtual URL has expired
    # (DNS resolves but TCP SYN never gets a reply), causing a silent hang.
    _DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
    client = http_client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    try:
        body = await _safe_get(client, url)
    finally:
        if own_client:
            await client.aclose()

    data = _decode_json(body)
    _raise_for_error(data, login_path=True)
    _validate_virtual_urls(data)

    return TachibanaSession(
        url_request=RequestUrl(data["sUrlRequest"]),
        url_master=MasterUrl(data["sUrlMaster"]),
        url_price=PriceUrl(data["sUrlPrice"]),
        url_event=EventUrl(data["sUrlEvent"]),
        url_event_ws=data["sUrlEventWebSocket"],
        zyoutoeki_kazei_c=str(data.get("sZyoutoekiKazeiC", "")),
        expires_at_ms=None,
    )


# ---------------------------------------------------------------------------
# validate_session_on_startup
# ---------------------------------------------------------------------------


async def _do_validate(
    session: TachibanaSession,
    *,
    p_no_counter: PNoCounter,
    http_client: Optional[httpx.AsyncClient],
) -> bool:
    """Hit ``CLMMfdsGetIssueDetail`` for 7203 / 00 to confirm the session.

    The chosen sCLMID is the lightest master-side endpoint
    (no column list required, single-record reply). If a future spec
    review picks a different probe, update both this function and the
    pinned test (HIGH-D2).
    """
    # Manual reference: `mfds_json_api_ref_text.html#CLMMfdsGetIssueDetail`.
    # The param name is `sTargetIssueCode` (comma-separated list), not
    # `sIssueCode` + `sSizyouC`. Fixed in T3 after demo-environment
    # smoke testing surfaced the typo.
    payload: dict[str, Any] = {
        "p_no": str(p_no_counter.next()),
        "p_sd_date": current_p_sd_date(),
        "sCLMID": "CLMMfdsGetIssueDetail",
        "sTargetIssueCode": "7203",
    }
    url = build_request_url(session.url_master, payload, sJsonOfmt="4")

    own_client = http_client is None
    _DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
    client = http_client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    try:
        body = await _safe_get(client, url)
    finally:
        if own_client:
            await client.aclose()

    data = _decode_json(body)
    _raise_for_error(data, login_path=False)
    return True


async def validate_session_on_startup(
    session: TachibanaSession,
    *,
    latch: StartupLatch,
    p_no_counter: PNoCounter,
    http_client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """Single-flight wrapper around `_do_validate` (M6 / HIGH-B).

    Caller passes the latch and `PNoCounter` held on the worker instance.
    `p_no_counter.next()` is consumed inside `_do_validate` so the value
    sent to the server is monotonically greater than the login `p_no`
    (R4). A second invocation per process is a programmer bug and
    surfaces as `RuntimeError`, which the engine top-level supervisor
    catches and fails the process (L6).

    L-7 (2026-04-25): the parameter was previously named `_latch` for
    no good reason — the underscore prefix on a public keyword arg is
    misleading. It is now `latch`; update call sites accordingly.
    """
    return await latch.run_once(
        _do_validate(
            session,
            p_no_counter=p_no_counter,
            http_client=http_client,
        )
    )


class TachibanaSessionHolder:
    """第二暗証番号のメモリ保持 + idle forget タイマー + lockout state。

    architecture.md §5.3 (C3 / C-R5-H2) の設計に従う。

    * idle timer: SubmitOrder/ModifyOrder/CancelOrder/CancelAllOrders/SetSecondPassword
      受信時に touch() でリセット。idle_forget_minutes 経過で自動 None 化。
    * lockout: p_errno=4 (SECOND_PASSWORD_INVALID) が max_retries 回連続した場合に
      lockout_secs 間は is_locked_out() が True を返す。SubmitOrder 成功時にカウンタリセット。
    """

    def __init__(
        self,
        idle_forget_minutes: float = 30.0,
        max_retries: int = 3,
        lockout_secs: float = 1800.0,
    ) -> None:
        self._password: str | None = None
        self._idle_forget_secs = idle_forget_minutes * 60.0
        self._max_retries = max_retries
        self._lockout_secs = lockout_secs
        self._last_use_time: float | None = None
        self._invalid_count: int = 0
        self._lockout_until: float | None = None

    def _now(self) -> float:
        import asyncio
        import time

        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:
            return time.monotonic()

    def set_password(self, value: str) -> None:
        """SetSecondPassword コマンド受信時に呼ぶ。"""
        self._password = value
        self.touch()

    def touch(self, now: float | None = None) -> None:
        """idle timer をリセット。second_password を expose するコマンド受信時に呼ぶ。"""
        self._last_use_time = now if now is not None else self._now()

    def is_idle_expired(self, now: float | None = None) -> bool:
        """idle forget 閾値を超えていれば True。"""
        if self._last_use_time is None:
            return False
        t = now if now is not None else self._now()
        return (t - self._last_use_time) >= self._idle_forget_secs

    def is_locked_out(self, now: float | None = None) -> bool:
        """lockout 期間中かを返す。"""
        if self._lockout_until is None:
            return False
        t = now if now is not None else self._now()
        if t >= self._lockout_until:
            self._lockout_until = None
            return False
        return True

    def get_password(self, now: float | None = None) -> str | None:
        """発注時に呼ぶ。idle 期限切れなら自動クリアして None を返す。"""
        if self.is_idle_expired(now):
            self._password = None
            self._last_use_time = None
        return self._password

    def clear(self) -> None:
        """ForgetSecondPassword / p_errno=2 受領時に呼ぶ。"""
        self._password = None
        self._last_use_time = None

    def on_invalid(self, now: float | None = None) -> bool:
        """p_errno=4 (SECOND_PASSWORD_INVALID) 受領時に呼ぶ。
        Returns True ならば lockout 状態に入った（以降の発注をブロックすべき）。
        """
        self._password = None
        self._invalid_count += 1
        if self._invalid_count >= self._max_retries:
            t = now if now is not None else self._now()
            self._lockout_until = t + self._lockout_secs
            return True
        return False

    def on_submit_success(self) -> None:
        """SubmitOrder 成功時に invalid_count をリセット。"""
        self._invalid_count = 0


__all__ = [
    "StartupLatch",
    "TachibanaSession",
    "TachibanaSessionHolder",
    "login",
    "validate_session_on_startup",
]
