"""W&B login helper — reads API key from stdin, calls wandb.login().

Usage (internal, called by flowsurface):
    uv run --with wandb python examples/wandb/do_login.py

Reads one line from stdin (the API key), calls wandb.login(key=..., relogin=True),
and exits with code 0 on success or 1 on failure.
Prints a single JSON line to stdout: {"ok": true} or {"ok": false, "error": "..."}.
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    key = sys.stdin.readline().strip()
    if not key:
        print(json.dumps({"ok": False, "error": "empty key"}))
        sys.exit(1)

    try:
        import wandb  # noqa: PLC0415
        logged_in = wandb.login(key=key, relogin=True)
        if logged_in:
            print(json.dumps({"ok": True}))
            sys.exit(0)
        else:
            print(json.dumps({"ok": False, "error": "wandb.login returned False"}))
            sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
