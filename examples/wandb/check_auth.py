"""W&B authentication check script.

Usage:
    uv run --with wandb python examples/wandb/check_auth.py

Prints exactly one JSON line to stdout and exits with code 0.
Never leaks the API key value to stdout.

Output format:
    {"authenticated": <bool>, "method": "env"|"netrc"|"none",
     "username": <string|null>, "error": <string|null>}

Resolution priority:
    1. WANDB_API_KEY environment variable
    2. ~/.netrc (Windows: %USERPROFILE%/_netrc) machine api.wandb.ai entry
    3. Neither found -> unauthenticated
"""
from __future__ import annotations

import json
import netrc
import os
import sys


def main() -> None:
    try:
        # Priority 1: WANDB_API_KEY environment variable
        if os.environ.get("WANDB_API_KEY"):
            print(json.dumps({
                "authenticated": True,
                "method": "env",
                "username": None,
                "error": None,
            }))
            return

        # Priority 2: ~/.netrc (or %USERPROFILE%/_netrc on Windows)
        try:
            n = netrc.netrc()
            auth = n.authenticators("api.wandb.ai")
        except FileNotFoundError:
            auth = None
        except netrc.NetrcParseError as exc:
            print(json.dumps({
                "authenticated": False,
                "method": "none",
                "username": None,
                "error": str(exc),
            }))
            return

        if auth is not None:
            # Resolve username via wandb API; fall back gracefully on timeout
            try:
                import wandb  # noqa: PLC0415
                viewer = wandb.Api(timeout=5).viewer
                username = getattr(viewer, "username", None)
                err = None
            except Exception:
                username = None
                err = "viewer_lookup_timeout"

            print(json.dumps({
                "authenticated": True,
                "method": "netrc",
                "username": username,
                "error": err,
            }))
            return

        # Priority 3: no credentials found
        print(json.dumps({
            "authenticated": False,
            "method": "none",
            "username": None,
            "error": None,
        }))

    except Exception as exc:  # noqa: BLE001
        # Catch-all: always exit 0 with error in JSON
        print(json.dumps({
            "authenticated": False,
            "method": "none",
            "username": None,
            "error": str(exc),
        }))

    sys.exit(0)


if __name__ == "__main__":
    main()
