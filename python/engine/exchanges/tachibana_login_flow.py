"""Tachibana login orchestration — the bridge between the IPC server,
the env / keyring fast paths, and the tkinter login helper subprocess.

Architecture: [docs/plan/tachibana/architecture.md §7](../../../docs/plan/tachibana/architecture.md).

Public entry point:

    async def run_login(
        *,
        request_id: Optional[str],
        p_no_counter: PNoCounter,
        http_client: Optional[httpx.AsyncClient] = None,
        dev_login_allowed: bool = False,
        is_startup: bool = False,
    ) -> List[dict]

The function returns a list of IPC event dicts that the caller appends to
the engine outbox in order. Possible event sequences:

* `[VenueLoginStarted, VenueReady]` — login succeeded.
* `[VenueLoginStarted, VenueError]` — login failed (after up to 3
  retries on the dialog path).
* `[VenueLoginStarted, VenueLoginCancelled]` — user dismissed the
  tkinter dialog (×, ESC, Cancel button).

Dev fast path (architecture §7.7, R10): when `dev_login_allowed` is True
*and* the env vars are set, the tkinter helper is **not** spawned —
instead `tachibana_auth.login(...)` is called directly with the env
credentials. `dev_login_allowed=False` (release builds) makes the env
read a no-op even if the variables are set, ensuring release builds
never auto-login.

Env variable schema (H10 — legacy aliases removed 2026-04-25):

    user_id : DEV_TACHIBANA_USER_ID
    password: DEV_TACHIBANA_PASSWORD
    is_demo : DEV_TACHIBANA_DEMO  (default True per F-Default-Demo)

The unprefixed `DEV_USER_ID` / `DEV_PASSWORD` / `DEV_IS_DEMO` aliases
that the very-early `.env` template used are **no longer accepted** —
they collided with other tooling and confused operators about which
venue's creds were being read. Update your `.env` to the
`DEV_TACHIBANA_*` form. The Rust release-profile guard
(`dev_tachibana_login_allowed=false`) is unaffected.

`fallback_user_id` / `fallback_password` / `fallback_is_demo` are
silent re-login parameters consumed by `run_login` (typically the
keyring-stored creds redirected by `_do_set_venue_credentials`); they
are tried before the dialog spawns, only on `is_startup=True`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import List, Optional

import httpx

from .tachibana_auth import (
    TachibanaSession,
    login as tachibana_login,
)
from .tachibana_helpers import (
    LoginError,
    PNoCounter,
    SessionExpiredError,
    TachibanaError,
    UnreadNoticesError,
)

log = logging.getLogger(__name__)

VENUE = "tachibana"

# ── Banner text (F-Banner1) ──────────────────────────────────────────────────

_MSG_LOGIN_FAILED = "ログインに失敗しました。ID / パスワードを確認してください"
_MSG_HELPER_NO_RESPONSE = "ログインヘルパーが応答せず終了しました"
_MSG_HELPER_TIMEOUT = "ログイン操作がタイムアウトしました"
_MSG_DEV_ENV_MISSING = (
    "DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD が設定されていません"
)


# ── Env loading ──────────────────────────────────────────────────────────────


def _truthy(s: Optional[str]) -> bool:
    return s is not None and s.strip().lower() in ("1", "true", "yes", "on")


def _load_dev_env() -> Optional[dict]:
    """Read `DEV_TACHIBANA_*` env vars. Returns None if either of
    user_id / password is missing — the caller falls back to the dialog
    path. `is_demo` defaults to True per F-Default-Demo (S2 in SKILL.md).

    H10 (2026-04-25): the legacy unprefixed aliases (`DEV_USER_ID` /
    `DEV_PASSWORD` / `DEV_IS_DEMO`) are no longer recognised. They
    collided with other tooling and made it ambiguous whose creds were
    being read. Operators must switch their `.env` to the
    `DEV_TACHIBANA_*` form.
    """
    user_id = os.environ.get("DEV_TACHIBANA_USER_ID")
    password = os.environ.get("DEV_TACHIBANA_PASSWORD")
    if not user_id or not password:
        return None
    raw_demo = os.environ.get("DEV_TACHIBANA_DEMO")
    is_demo = True if raw_demo is None else _truthy(raw_demo)
    return {"user_id": user_id, "password": password, "is_demo": is_demo}


# ── Tkinter helper subprocess (spawned only on the dialog path) ──────────────


async def _spawn_login_dialog(prefill: Optional[dict]) -> Optional[dict]:
    """Run the tkinter helper as a subprocess. stdin: JSON prefill / opts.
    stdout (final line): JSON result. Returns the parsed result dict on
    success, None on cancellation, or raises on transport / decode error."""
    cmd = [
        sys.executable,
        "-m",
        "engine.exchanges.tachibana_login_dialog",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    payload = {
        "prefill": prefill or {},
        "allow_prod_choice": _truthy(os.environ.get("TACHIBANA_ALLOW_PROD")),
    }
    try:
        proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError) as exc:
        # H2 / M-3-py / M-15: previously we logged and continued, then
        # blocked on `proc.communicate()` until the 10-min timeout.
        # The helper is unable to receive its prefill payload — there
        # is nothing to wait for. Tear it down immediately and surface
        # `login_failed` so the user sees a banner instead of a 10-min
        # silence.
        #
        # M-15 ラウンド 5 (orphan reap): `proc.terminate()` only sends
        # the signal — without an `await proc.wait()` the helper PID
        # lingers as a zombie until the parent exits. Always reap; if
        # terminate() doesn't take effect within 5 s, escalate to kill().
        log.error("tachibana login dialog: failed to write stdin: %s", exc)
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                proc.kill()
                # Best-effort final reap; ignore secondary failures.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
            except ProcessLookupError:
                pass
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)

    try:
        # 10-minute total budget. Real interactive logins are typically
        # under 1 min; this only protects against a hung helper.
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600.0)
    except asyncio.TimeoutError:
        log.error("tachibana login dialog: timed out after 10 minutes")
        proc.terminate()
        # After terminate(), give the helper a brief grace period and then
        # SIGKILL it. Even on the kill path we still want any stderr the
        # helper produced — read it best-effort so a misbehaving helper
        # is debuggable instead of silently disappearing.
        leftover_stderr = b""
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("tachibana login dialog: helper did not exit after kill")
        if proc.stderr is not None:
            try:
                leftover_stderr = await asyncio.wait_for(
                    proc.stderr.read(), timeout=1.0
                )
            except (asyncio.TimeoutError, Exception) as exc:
                log.debug("tachibana login dialog: stderr read after kill failed: %s", exc)
        if leftover_stderr:
            log.error(
                "tachibana login dialog: stderr after timeout/kill = %r",
                leftover_stderr.decode("utf-8", errors="replace")[:1000],
            )
        raise LoginError(code="login_failed", message=_MSG_HELPER_TIMEOUT)

    if proc.returncode != 0:
        log.error(
            "tachibana login dialog: helper exited code=%s stderr=%r",
            proc.returncode,
            stderr.decode("utf-8", errors="replace") if stderr else "",
        )
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        log.error("tachibana login dialog: helper produced no output")
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)

    # The helper writes status lines to stderr and ONE result JSON object
    # to stdout (last non-empty line is the canonical result).
    last_line = next(
        (line for line in reversed(text.splitlines()) if line.strip()),
        "",
    )
    try:
        result = json.loads(last_line)
    except json.JSONDecodeError as exc:
        log.error(
            "tachibana login dialog: failed to parse helper result %r: %s",
            last_line,
            exc,
        )
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)

    if result.get("status") == "cancelled":
        return None
    if result.get("status") != "ok":
        log.error("tachibana login dialog: unexpected status %r", result)
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)
    return result


# ── Public orchestration ─────────────────────────────────────────────────────


def _venue_ready_event(request_id: Optional[str]) -> dict:
    return {"event": "VenueReady", "venue": VENUE, "request_id": request_id}


def _venue_error_event(
    request_id: Optional[str], code: str, message: str
) -> dict:
    return {
        "event": "VenueError",
        "venue": VENUE,
        "request_id": request_id,
        "code": code,
        "message": message,
    }


def _venue_credentials_refreshed_event(
    session: TachibanaSession,
    *,
    user_id: str,
    password: str,
    is_demo: bool,
) -> dict:
    """Build the IPC event for a successful login.

    The full credential triple (user_id / password / is_demo) is included
    alongside the derived session URLs so Rust can persist all four into
    the keyring. The previous shape carried only `session`, which silently
    let the keyring's `user_id` / `password` / `is_demo` drift whenever
    the user re-logged with a different account, toggled demo↔prod, or
    changed their password — making the next cold-start fallback try
    stale credentials.
    """
    return {
        "event": "VenueCredentialsRefreshed",
        "venue": VENUE,
        "user_id": user_id,
        "password": password,
        "is_demo": is_demo,
        "session": {
            "url_request": str(session.url_request),
            "url_master": str(session.url_master),
            "url_price": str(session.url_price),
            "url_event": str(session.url_event),
            "url_event_ws": session.url_event_ws,
            "expires_at_ms": session.expires_at_ms,
            "zyoutoeki_kazei_c": session.zyoutoeki_kazei_c,
        },
    }


def _login_started_event(request_id: Optional[str]) -> dict:
    return {
        "event": "VenueLoginStarted",
        "venue": VENUE,
        "request_id": request_id,
    }


def _login_cancelled_event(request_id: Optional[str]) -> dict:
    return {
        "event": "VenueLoginCancelled",
        "venue": VENUE,
        "request_id": request_id,
    }


async def _do_login_call(
    user_id: str,
    password: str,
    is_demo: bool,
    *,
    p_no_counter: PNoCounter,
    http_client: Optional[httpx.AsyncClient],
) -> TachibanaSession:
    return await tachibana_login(
        user_id,
        password,
        is_demo=is_demo,
        p_no_counter=p_no_counter,
        http_client=http_client,
    )


async def run_login(
    *,
    request_id: Optional[str],
    p_no_counter: PNoCounter,
    http_client: Optional[httpx.AsyncClient] = None,
    dev_login_allowed: bool = False,
    is_startup: bool = False,
    fallback_user_id: Optional[str] = None,
    fallback_password: Optional[str] = None,
    fallback_is_demo: Optional[bool] = None,
) -> List[dict]:
    """Drive a Tachibana login. Returns IPC events to enqueue, in order.

    `VenueLoginStarted` is emitted **only when the tkinter helper
    subprocess is actually spawned** — env fast path and credential-
    fallback paths do not surface that event because the UI banner
    "別ウィンドウでログイン中" would be a lie. The schema docstring
    pins this semantic.

    `fallback_user_id` / `fallback_password` / `fallback_is_demo` are
    consumed only on `is_startup=True`. They come from the
    `SetVenueCredentials` payload that Rust derived from the keyring;
    they are tried as a non-interactive re-login when the stored
    session has expired. If they are missing or rejected, the dialog
    path takes over (which DOES emit `VenueLoginStarted`).
    """
    events: List[dict] = []

    # ── (a) Dev env fast path (architecture.md §7.7, R10) ────────────────
    # No `VenueLoginStarted` here — the env path never opens a window.
    if dev_login_allowed:
        env_creds = _load_dev_env()
        if env_creds is not None:
            log.info(
                "tachibana login: using dev env fast path (is_demo=%s)",
                env_creds["is_demo"],
            )
            err_event = await _try_silent_login(
                events,
                request_id=request_id,
                user_id=env_creds["user_id"],
                password=env_creds["password"],
                is_demo=env_creds["is_demo"],
                p_no_counter=p_no_counter,
                http_client=http_client,
            )
            if err_event is None:
                # Success: events already extended with refresh + ready.
                return events
            # err_event is a non-recoverable VenueError (unread_notices,
            # session_expired, login_failed). Surface it; do NOT fall
            # through to the dialog — env was authoritative.
            events.append(err_event)
            return events
        else:
            log.info(
                "tachibana login: dev_login_allowed=True but env creds missing — checking fallback creds"
            )

    # ── (b) Stored-creds fallback (startup only) ─────────────────────────
    # Same no-`VenueLoginStarted` rule: this is silent re-auth, not a
    # dialog. If it fails with anything other than a clean LoginError
    # we surface the typed error; if it's a clean LoginError (bad
    # password etc.) we drop into the dialog path so the user can fix
    # the credentials.
    if (
        is_startup
        and fallback_user_id
        and fallback_password
        and fallback_is_demo is not None
    ):
        log.info(
            "tachibana login: trying fallback credentials from keyring (user_id=%s)",
            fallback_user_id,
        )
        err_event = await _try_silent_login(
            events,
            request_id=request_id,
            user_id=fallback_user_id,
            password=fallback_password,
            is_demo=fallback_is_demo,
            p_no_counter=p_no_counter,
            http_client=http_client,
        )
        if err_event is None:
            return events
        # Re-classify: terminal vs retryable. Terminal errors return
        # immediately; retryable errors (login_failed, transport_error)
        # fall through to the dialog so the user can correct input.
        terminal_codes = {"unread_notices", "session_expired"}
        if err_event.get("code") in terminal_codes:
            events.append(err_event)
            return events
        log.info(
            "tachibana login: fallback creds rejected (code=%s); falling through to dialog",
            err_event.get("code"),
        )

    # ── (c) Dialog path (tkinter helper subprocess) ──────────────────────
    # Now we DO emit VenueLoginStarted because a subprocess will spawn.
    events.append(_login_started_event(request_id))
    prefill: Optional[dict] = None
    if fallback_user_id:
        prefill = {"user_id": fallback_user_id}
        if fallback_is_demo is not None:
            prefill["is_demo"] = fallback_is_demo

    last_error_event: Optional[dict] = None
    for attempt in range(1, 4):  # up to 3 retries
        try:
            result = await _spawn_login_dialog(prefill=prefill)
        except LoginError as exc:
            log.error("tachibana login: dialog spawn failed: %s", exc)
            events.append(_venue_error_event(request_id, exc.code, str(exc)))
            return events

        if result is None:
            # User cancelled.
            events.append(_login_cancelled_event(request_id))
            return events

        dialog_user_id = result["user_id"]
        dialog_password = result["password"]
        dialog_is_demo = bool(result.get("is_demo", True))
        try:
            session = await _do_login_call(
                dialog_user_id,
                dialog_password,
                dialog_is_demo,
                p_no_counter=p_no_counter,
                http_client=http_client,
            )
        except UnreadNoticesError as exc:
            events.append(_venue_error_event(request_id, "unread_notices", str(exc)))
            return events
        except SessionExpiredError as exc:
            events.append(_venue_error_event(request_id, "session_expired", str(exc)))
            return events
        except LoginError as exc:
            log.warning(
                "tachibana login: attempt %d/%d failed: %s", attempt, 3, exc
            )
            last_error_event = _venue_error_event(request_id, exc.code, str(exc))
            continue
        except TachibanaError as exc:
            last_error_event = _venue_error_event(
                request_id, "login_failed", _MSG_LOGIN_FAILED
            )
            log.error("tachibana login: unexpected TachibanaError: %s", exc)
            continue

        events.append(
            _venue_credentials_refreshed_event(
                session,
                user_id=dialog_user_id,
                password=dialog_password,
                is_demo=dialog_is_demo,
            )
        )
        events.append(_venue_ready_event(request_id))
        return events

    # All retries exhausted.
    events.append(
        last_error_event
        or _venue_error_event(request_id, "login_failed", _MSG_LOGIN_FAILED)
    )
    return events


async def _try_silent_login(
    events: List[dict],
    *,
    request_id: Optional[str],
    user_id: str,
    password: str,
    is_demo: bool,
    p_no_counter: PNoCounter,
    http_client: Optional[httpx.AsyncClient],
) -> Optional[dict]:
    """Attempt a non-interactive login and append `VenueCredentialsRefreshed
    + VenueReady` on success. Returns `None` on success and a
    `VenueError`-shaped dict on failure (caller decides whether to
    surface it terminally or fall through to the dialog).

    Crucially, this helper does **not** append `VenueLoginStarted` — it
    is shared by the dev env fast path and the keyring-fallback path,
    neither of which open a window.
    """
    try:
        session = await _do_login_call(
            user_id,
            password,
            is_demo,
            p_no_counter=p_no_counter,
            http_client=http_client,
        )
    except UnreadNoticesError as exc:
        return _venue_error_event(request_id, "unread_notices", str(exc))
    except SessionExpiredError as exc:
        return _venue_error_event(request_id, "session_expired", str(exc))
    except LoginError as exc:
        return _venue_error_event(request_id, exc.code, str(exc))
    except TachibanaError as exc:
        log.error("tachibana login: unexpected TachibanaError: %s", exc)
        return _venue_error_event(request_id, "login_failed", _MSG_LOGIN_FAILED)
    events.append(
        _venue_credentials_refreshed_event(
            session,
            user_id=user_id,
            password=password,
            is_demo=is_demo,
        )
    )
    events.append(_venue_ready_event(request_id))
    return None


__all__ = [
    "run_login",
    "VENUE",
]
