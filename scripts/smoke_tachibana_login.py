"""Smoke test: drive a real-demo Tachibana login through the production
code path (`tachibana_login_flow.startup_login` → `tachibana_auth.login` →
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
import shutil
import sys
import tempfile
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
    from engine.exchanges.tachibana_login_flow import (
        LoginCancelled,
        startup_login,
    )

    # Use throw-away config / cache dirs so a stale cached session from a
    # previous smoke run cannot mask a real login regression. The dev fast
    # path skips the tkinter dialog when DEV_TACHIBANA_* env vars are set.
    tmp_root = Path(tempfile.mkdtemp(prefix="tachibana_smoke_"))
    config_dir = tmp_root / "config"
    cache_dir = tmp_root / "cache"
    config_dir.mkdir()
    cache_dir.mkdir()

    p_no = PNoCounter()
    try:
        session = await startup_login(
            config_dir,
            cache_dir,
            p_no_counter=p_no,
            startup_latch=StartupLatch(),
            dev_login_allowed=True,
        )
    except LoginCancelled:
        logging.error("LOGIN FAILED — dialog was cancelled (DEV_TACHIBANA_* unset?)")
        return 1
    finally:
        # Best-effort cleanup; smoke runs are short-lived.
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Re-validate via the same path SetVenueCredentials uses on startup.
    # `startup_login` already validated cached sessions internally, but
    # re-running on the freshly minted session catches regressions where
    # a brand-new session wouldn't survive the post-login ping.
    await validate_session_on_startup(
        session, latch=StartupLatch(), p_no_counter=p_no
    )
    logging.info("Tachibana session validated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
