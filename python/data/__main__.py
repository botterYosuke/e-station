"""Entry point: python -m data"""

import argparse
import asyncio
import json
import os
import sys


def _parse_stdin_config() -> dict:
    """Read {port, token} JSON from stdin (production mode)."""
    raw = sys.stdin.readline().strip()
    return json.loads(raw)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flowsurface data engine")
    parser.add_argument("--port", type=int, help="WebSocket port (dev mode only)")
    parser.add_argument("--token", type=str, help="Connection token (dev mode only)")
    return parser.parse_args()


async def _run(port: int, token: str) -> None:
    from data.server import DataEngineServer

    server = DataEngineServer(port=port, token=token)
    await server.serve()


def main() -> None:
    args = _parse_args()

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

    asyncio.run(_run(port, token))


if __name__ == "__main__":
    main()
