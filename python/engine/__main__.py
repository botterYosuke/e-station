"""Entry point: python -m engine"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

log = logging.getLogger(__name__)


def _parse_stdin_config() -> dict[str, Any]:
    """Read the initial JSON config line from stdin (production mode).

    Schema (T3, schema 1.2):
        {
            "port": int,                        # required
            "token": str,                       # required
            "dev_tachibana_login_allowed": bool,  # optional, default False
            "config_dir": str | None,           # optional (T4)
            "cache_dir": str | None,            # optional (T4)
        }

    Unknown keys are ignored (forward-compatible). Missing optional keys
    fall back to safe defaults so older Rust binaries remain compatible.
    """
    raw = sys.stdin.readline().strip()
    cfg = json.loads(raw)
    # Defaults for forward / backward compat.
    cfg.setdefault("dev_tachibana_login_allowed", False)
    cfg.setdefault("config_dir", None)
    cfg.setdefault("cache_dir", None)
    return cfg


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flowsurface data engine")
    parser.add_argument("--port", type=int, help="WebSocket port (dev mode only)")
    parser.add_argument("--token", type=str, help="Connection token (dev mode only)")
    return parser.parse_args()


async def _run(port: int, token: str, *, dev_tachibana_login_allowed: bool) -> None:
    from engine.server import DataEngineServer

    server = DataEngineServer(
        port=port,
        token=token,
        dev_tachibana_login_allowed=dev_tachibana_login_allowed,
    )
    await server.serve()


def _coerce_dev_login_allowed(value: Any) -> bool:
    """Strict bool coercion for `dev_tachibana_login_allowed`.

    M-CFG ラウンド 5: the stdin payload is Rust-controlled, but a
    misbehaving launcher (or a future schema bug) might serialise
    `"false"` (string) instead of `false` (bool). Naive `bool(value)`
    would silently turn the truthy string `"false"` into `True` and
    open the dev fast path on a release build. Reject non-bool with
    a warning and fall back to the safe default (`False`).
    """
    if isinstance(value, bool):
        return value
    log.warning(
        "non-bool dev_tachibana_login_allowed=%r (type=%s) — falling back to False",
        value,
        type(value).__name__,
    )
    return False


def _env_dev_login_allowed() -> bool:
    """Resolve `dev_tachibana_login_allowed` from the environment.

    M2 / M-5 / M-17 (2026-04-25): the CLI (`--port` / `--token`) and
    env-var (`FLOWSURFACE_ENGINE_PORT` / `FLOWSURFACE_ENGINE_TOKEN`)
    boot paths used to hardcode the flag to `False`, which made the
    dev fast path unreachable from `uv run python -m engine ...` even
    on a developer's debug build. Honour an opt-in
    `FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED` env var (truthy values:
    "1" / "true" / "yes" / "on") so a developer can explicitly enable
    the fast path. The stdin-payload boot path remains the
    authoritative producer for the Rust-managed mode (release vs
    debug); this helper is only consulted when stdin is absent.
    """
    raw = os.environ.get("FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED", "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    args = _parse_args()

    dev_tachibana_login_allowed = False

    if args.port and args.token:
        port, token = args.port, args.token
        dev_tachibana_login_allowed = _env_dev_login_allowed()
    else:
        # Production: receive config from Rust via stdin
        env_port = os.environ.get("FLOWSURFACE_ENGINE_PORT")
        env_token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
        if env_port and env_token:
            port, token = int(env_port), env_token
            dev_tachibana_login_allowed = _env_dev_login_allowed()
        else:
            # The stdin path is Rust-controlled. The flag rides the
            # build profile (debug = True, release = False) — see
            # `engine-client/src/process.rs::build_stdin_payload`.
            cfg = _parse_stdin_config()
            port, token = cfg["port"], cfg["token"]
            dev_tachibana_login_allowed = _coerce_dev_login_allowed(
                cfg.get("dev_tachibana_login_allowed", False)
            )

    asyncio.run(
        _run(port, token, dev_tachibana_login_allowed=dev_tachibana_login_allowed)
    )


if __name__ == "__main__":
    main()
