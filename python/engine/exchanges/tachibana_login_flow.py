"""Tachibana login orchestration — the bridge between the IPC server,
the env fast paths, and the tkinter login helper subprocess.

Architecture: [docs/plan/✅tachibana/architecture.md §7](../../../docs/plan/✅tachibana/architecture.md).

Public entry point:

    async def startup_login(
        config_dir: Path,
        cache_dir: Path,
        *,
        p_no_counter: PNoCounter,
        startup_latch: StartupLatch,
        dev_login_allowed: bool = False,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> TachibanaSession

The function is called once per connection after handshake completes.
Python self-initiates the login flow — Rust does not send credentials.

Possible outcomes:
* Returns TachibanaSession — login succeeded (from cache or fresh login).
* Raises LoginCancelled — user dismissed the tkinter dialog.
* Raises LoginError / TachibanaError — network or API failure.

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
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import httpx

from .tachibana_auth import (
    StartupLatch,
    TachibanaSession,
    login as tachibana_login,
    validate_session_on_startup,
)
from .tachibana_file_store import (
    _is_session_fresh,
    clear_session,
    load_account,
    load_session,
    save_account,
    save_session,
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


class LoginCancelled(Exception):
    """Raised by startup_login when the user dismisses the login dialog."""


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
    success, None on cancellation, or raises on transport / decode error.

    Note (MEDIUM-11 ラウンド 6): the timeout-and-kill branch reads
    `proc.stderr` only after `proc.communicate()` has already raised
    `asyncio.TimeoutError`, which means the stderr pipe may have been
    partially consumed by the killed `communicate()` call's internal
    drain. The post-kill stderr read is therefore **best-effort**: we
    capture whatever bytes are still available within a 1-second
    budget and log them. Missing or truncated stderr in this branch is
    expected, not a bug. Switching to a separate `proc.stderr.read()`
    task started in parallel with `communicate()` would give complete
    capture but adds significant complexity for a path that fires only
    on a hung helper (10-min budget) — kept best-effort intentionally.
    """
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
                # Best-effort final reap; surface the unreaped PID via
                # `log.error` so an OS-level zombie isn't a silent leak
                # (MEDIUM-13 ラウンド 6 — previously this was a bare
                # `pass` that hid kill failures).
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    log.error(
                        "tachibana login dialog: failed to reap helper PID %s "
                        "after kill — giving up; OS may show a zombie",
                        proc.pid,
                    )
            except ProcessLookupError:
                pass
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)

    try:
        # 10-minute total budget. Real interactive logins are typically
        # under 1 min; this only protects against a hung helper.
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600.0)
    except asyncio.CancelledError:
        # The parent task (_startup_tachibana) was cancelled — Rust reconnected
        # or the connection dropped.  Kill the subprocess so the dialog window
        # does not linger as an orphan while a new login flow starts.
        log.info(
            "tachibana login dialog: task cancelled — terminating helper pid=%d",
            proc.pid,
        )
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError, asyncio.CancelledError):
                    log.error(
                        "tachibana login dialog: helper pid=%d did not exit after kill on cancel",
                        proc.pid,
                    )
            except ProcessLookupError:
                pass
        raise
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
                log.debug(
                    "tachibana login dialog: stderr read after kill failed: %s", exc
                )
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
            stderr.decode("utf-8", errors="replace")[:1000] if stderr else "",
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
            last_line[:40] if last_line else "(empty)",
            exc,
        )
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)

    if result.get("status") == "cancelled":
        return None
    if result.get("status") != "ok":
        log.error("tachibana login dialog: unexpected status %r", result.get("status"))
        raise LoginError(code="login_failed", message=_MSG_HELPER_NO_RESPONSE)
    return result


# ── Public orchestration ─────────────────────────────────────────────────────


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


async def startup_login(
    config_dir: Path,
    cache_dir: Path,
    *,
    p_no_counter: PNoCounter,
    startup_latch: StartupLatch,
    dev_login_allowed: bool = False,
    http_client: Optional[httpx.AsyncClient] = None,
) -> TachibanaSession:
    """Drive the Tachibana startup login flow (T-SC2).

    Returns TachibanaSession on success.
    Raises LoginCancelled if the user dismissed the login dialog.
    Raises LoginError / TachibanaError on network or API failure.

    Flow:
    1. Load cached session; if fresh, validate via API ping — return on success.
    2. Load account info for dialog prefill.
    3. Dev env fast path (dev_login_allowed only): skip dialog if env creds set.
    4. Tkinter dialog → login API → save account + session.
    """
    # ── 1. Session cache fast path ────────────────────────────────────────
    cached = load_session(cache_dir)
    if cached and _is_session_fresh(cached):
        try:
            await validate_session_on_startup(
                cached,
                latch=startup_latch,
                p_no_counter=p_no_counter,
                http_client=http_client,
            )
            log.info("tachibana startup_login: cached session is valid, skipping login")
            return cached
        except (LoginError, SessionExpiredError) as exc:
            log.info(
                "tachibana startup_login: cached session invalid (%s), will re-login",
                exc,
            )
            clear_session(cache_dir)
        except RuntimeError as exc:
            # StartupLatch violated (L6) — clear stale session before propagating
            # so the engine does not re-enter the same broken state on next start.
            clear_session(cache_dir)
            raise

    # ── 2. Account prefill ────────────────────────────────────────────────
    account = load_account(config_dir)
    prefill_user_id: Optional[str] = account["user_id"] if account else None
    prefill_is_demo: bool = account["is_demo"] if account else True

    # ── 3. Dev env fast path (dev_login_allowed only, spec §3.1 F-DevEnv-Release-Guard) ──
    if dev_login_allowed:
        env_creds = _load_dev_env()
        if env_creds is not None:
            log.info(
                "tachibana startup_login: using dev env fast path (is_demo=%s)",
                env_creds["is_demo"],
            )
            session = await _do_login_call(
                env_creds["user_id"],
                env_creds["password"],
                env_creds["is_demo"],
                p_no_counter=p_no_counter,
                http_client=http_client,
            )
            save_account(config_dir, env_creds["user_id"], env_creds["is_demo"])
            save_session(cache_dir, session)
            return session

    # ── 4. Dialog path ────────────────────────────────────────────────────
    prefill: Optional[dict] = None
    if prefill_user_id is not None:
        prefill = {"user_id": prefill_user_id, "is_demo": prefill_is_demo}

    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):  # up to 3 retries
        try:
            result = await _spawn_login_dialog(prefill=prefill)
        except LoginError as exc:
            log.error("tachibana startup_login: dialog spawn failed: %s", exc)
            raise

        if result is None:
            raise LoginCancelled()

        dialog_user_id = result.get("user_id")
        dialog_password = result.get("password")
        if not dialog_user_id or not dialog_password:
            log.error("tachibana startup_login: helper result missing credential fields")
            raise LoginError(code="login_failed", message=_MSG_LOGIN_FAILED)
        dialog_is_demo = bool(result.get("is_demo", True))

        try:
            session = await _do_login_call(
                dialog_user_id,
                dialog_password,
                dialog_is_demo,
                p_no_counter=p_no_counter,
                http_client=http_client,
            )
        except UnreadNoticesError:
            raise
        except SessionExpiredError:
            raise
        except LoginError as exc:
            log.warning(
                "tachibana startup_login: attempt %d/3 failed: %s", attempt, exc
            )
            last_exc = exc
            continue
        except TachibanaError as exc:
            log.error("tachibana startup_login: unexpected TachibanaError: %s", exc)
            last_exc = exc
            continue

        # Success — persist account (password excluded) and session.
        save_account(config_dir, dialog_user_id, dialog_is_demo)
        save_session(cache_dir, session)
        return session

    raise last_exc or LoginError(code="login_failed", message=_MSG_LOGIN_FAILED)


__all__ = [
    "startup_login",
    "LoginCancelled",
    "VENUE",
]
