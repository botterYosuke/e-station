"""Entry point: python -m engine"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any


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


def main() -> None:
    args = _parse_args()

    dev_tachibana_login_allowed = False

    if args.port and args.token:
        port, token = args.port, args.token
    else:
        # Production: receive config from Rust via stdin
        env_port = os.environ.get("FLOWSURFACE_ENGINE_PORT")
        env_token = os.environ.get("FLOWSURFACE_ENGINE_TOKEN")
        if env_port and env_token:
            port, token = int(env_port), env_token
        else:
            cfg = _parse_stdin_config()
            port, token = cfg["port"], cfg["token"]
            dev_tachibana_login_allowed = bool(cfg.get("dev_tachibana_login_allowed", False))

    asyncio.run(
        _run(port, token, dev_tachibana_login_allowed=dev_tachibana_login_allowed)
    )


if __name__ == "__main__":
    main()
