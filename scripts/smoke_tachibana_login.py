"""Smoke test: drive a real-demo Tachibana login through the production
code path (`tachibana_login_flow.run_login` → `tachibana_auth.login` →
`validate_session_on_startup`).

Reads `.env` (DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD /
DEV_TACHIBANA_DEMO — H10 2026-04-25: legacy unprefixed names removed),
fires the dev fast path, and expects the canonical success log line
"Tachibana session validated successfully".

Requires a phone-authenticated demo account. Exit code 0 on success,
non-zero on any failure. Does NOT touch keyring or Rust.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    repo_root = Path(__file__).resolve().parent.parent
    _load_env(repo_root / ".env")

    sys.path.insert(0, str(repo_root / "python"))

    from engine.exchanges.tachibana_auth import (
        StartupLatch,
        validate_session_on_startup,
    )
    from engine.exchanges.tachibana_helpers import PNoCounter
    from engine.exchanges.tachibana_login_flow import run_login
    from engine.exchanges.tachibana_url import (
        EventUrl,
        MasterUrl,
        PriceUrl,
        RequestUrl,
    )
    from engine.exchanges.tachibana_auth import TachibanaSession

    p_no = PNoCounter()
    events = await run_login(
        request_id="smoke-1",
        p_no_counter=p_no,
        dev_login_allowed=True,
        is_startup=True,
    )

    print("=== Login events ===", file=sys.stderr)
    for ev in events:
        # Mask URL values explicitly so we never print session secrets.
        masked = {
            k: ("***" if k == "session" else v)
            for k, v in ev.items()
        }
        print(masked, file=sys.stderr)

    if not any(ev.get("event") == "VenueReady" for ev in events):
        logging.error("LOGIN FAILED — no VenueReady in event sequence")
        return 1

    # Reconstruct the session from VenueCredentialsRefreshed and validate
    # it via the same path SetVenueCredentials uses on startup.
    refresh = next(
        (ev for ev in events if ev.get("event") == "VenueCredentialsRefreshed"),
        None,
    )
    assert refresh is not None
    s = refresh["session"]
    session = TachibanaSession(
        url_request=RequestUrl(s["url_request"]),
        url_master=MasterUrl(s["url_master"]),
        url_price=PriceUrl(s["url_price"]),
        url_event=EventUrl(s["url_event"]),
        url_event_ws=s["url_event_ws"],
        zyoutoeki_kazei_c=s.get("zyoutoeki_kazei_c", ""),
        expires_at_ms=s.get("expires_at_ms"),
    )

    latch = StartupLatch()
    await validate_session_on_startup(
        session, latch=latch, p_no_counter=p_no
    )
    logging.info("Tachibana session validated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
