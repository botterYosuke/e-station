"""Tachibana file-backed credential and session store (T-SC1).

Manages two JSON files:
  tachibana_account.json  — user_id + is_demo (password is NEVER stored)
  tachibana_session.json  — virtual URLs + saved_at_ms

Atomic writes: tempfile + os.replace (POSIX and Windows both guarantee
overwrite of an existing target with os.replace).

Session freshness: a cached session is considered valid when it was saved
on the current JST calendar day AND before JST 15:30:00.  Boundary is
closed on the invalid side (>= 15:30:00 JST → invalid).  A saved_at_ms
that is in the future relative to now (clock skew) is also treated as
invalid (conservative fallback).

saved_at_ms is stored in expires_at_ms field of TachibanaSession so the
caller can pass the loaded session directly to _is_session_fresh without
an extra wrapper type.  The Tachibana API never populates expires_at_ms
itself (it returns no explicit session expiry), so repurposing the field
here is safe.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .tachibana_auth import TachibanaSession
from .tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl

log = logging.getLogger(__name__)

ACCOUNT_FILENAME = "tachibana_account.json"
SESSION_FILENAME = "tachibana_session.json"

_JST = timezone(timedelta(hours=9))
_JST_CUTOFF_HOUR = 15
_JST_CUTOFF_MINUTE = 30


# ---------------------------------------------------------------------------
# Account (user_id / is_demo only — password never written)
# ---------------------------------------------------------------------------


def save_account(config_dir: Path, user_id: str, is_demo: bool) -> None:
    """Persist user_id and is_demo to tachibana_account.json.

    password is deliberately excluded (F-SC-NoPassword).
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    payload = {"user_id": user_id, "is_demo": is_demo}
    _atomic_write(config_dir / ACCOUNT_FILENAME, payload)


def load_account(config_dir: Path) -> dict[str, Any] | None:
    """Load account info.  Returns None if file is absent or corrupt."""
    path = config_dir / ACCOUNT_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data.get("user_id"), str):
            return None
        if not isinstance(data.get("is_demo"), bool):
            return None
        return {"user_id": data["user_id"], "is_demo": data["is_demo"]}
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("tachibana_file_store: failed to load %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Session (virtual URLs + saved_at_ms)
# ---------------------------------------------------------------------------


def save_session(cache_dir: Path, session: TachibanaSession) -> None:
    """Persist session URLs to tachibana_session.json with a saved_at_ms timestamp."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    saved_at_ms = int(time.time() * 1000)
    payload = {
        "url_request": str(session.url_request),
        "url_master": str(session.url_master),
        "url_price": str(session.url_price),
        "url_event": str(session.url_event),
        "url_event_ws": session.url_event_ws,
        "zyoutoeki_kazei_c": session.zyoutoeki_kazei_c,
        "saved_at_ms": saved_at_ms,
    }
    _atomic_write(cache_dir / SESSION_FILENAME, payload)


def load_session(cache_dir: Path) -> TachibanaSession | None:
    """Load session from tachibana_session.json.

    Returns None if absent, corrupt, or missing required fields.
    saved_at_ms is stored in expires_at_ms so the caller can pass the
    returned session to _is_session_fresh without an extra wrapper.
    """
    path = cache_dir / SESSION_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        url_request = data["url_request"]
        url_master = data["url_master"]
        url_price = data["url_price"]
        url_event = data["url_event"]
        url_event_ws = data["url_event_ws"]
        zyoutoeki_kazei_c = data.get("zyoutoeki_kazei_c", "")
        saved_at_ms = int(data["saved_at_ms"])
        return TachibanaSession(
            url_request=RequestUrl(url_request),
            url_master=MasterUrl(url_master),
            url_price=PriceUrl(url_price),
            url_event=EventUrl(url_event),
            url_event_ws=url_event_ws,
            zyoutoeki_kazei_c=zyoutoeki_kazei_c,
            expires_at_ms=saved_at_ms,
        )
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("tachibana_file_store: failed to load %s: %s", path, exc)
        return None


def clear_session(cache_dir: Path) -> None:
    """Delete the session cache file if it exists."""
    path = cache_dir / SESSION_FILENAME
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("tachibana_file_store: failed to delete session cache: %s", exc)


# ---------------------------------------------------------------------------
# Freshness check (F-SC-FreshJST)
# ---------------------------------------------------------------------------


def _is_session_fresh(session: TachibanaSession) -> bool:
    """Return True if the session was saved on the current JST day before JST 15:30.

    session.expires_at_ms is used as the saved_at_ms timestamp (the field
    is populated by load_session — the Tachibana API itself never sets it).

    Invariants (F-SC-FreshJST):
    - saved_at_ms > now_ms (clock skew) → invalid
    - saved JST date != today JST date → invalid
    - saved JST time >= 15:30:00 → invalid (boundary closed on invalid side)
    """
    saved_at_ms = session.expires_at_ms
    if saved_at_ms is None:
        return False

    now_ms = int(time.time() * 1000)

    # Clock skew guard: a future save timestamp is conservatively invalid.
    if saved_at_ms > now_ms:
        return False

    saved_dt_jst = datetime.fromtimestamp(saved_at_ms / 1000, tz=_JST)
    now_dt_jst = datetime.fromtimestamp(now_ms / 1000, tz=_JST)

    # Must be the same JST calendar day.
    if saved_dt_jst.date() != now_dt_jst.date():
        return False

    # Cutoff: must be strictly before JST 15:30:00 (>= is invalid).
    cutoff_jst = saved_dt_jst.replace(
        hour=_JST_CUTOFF_HOUR, minute=_JST_CUTOFF_MINUTE, second=0, microsecond=0
    )
    return saved_dt_jst < cutoff_jst


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, payload: dict) -> None:
    """Write JSON to path atomically via tempfile + os.replace (F-SC-Atomic).

    On failure before os.replace the final file is untouched.
    On Windows and POSIX, os.replace guarantees overwrite of an existing target.
    """
    dir_ = path.parent
    fd, tmp_path_str = tempfile.mkstemp(dir=dir_, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path_str, path)
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


__all__ = [
    "save_account",
    "load_account",
    "save_session",
    "load_session",
    "clear_session",
    "_is_session_fresh",
    "ACCOUNT_FILENAME",
    "SESSION_FILENAME",
]
