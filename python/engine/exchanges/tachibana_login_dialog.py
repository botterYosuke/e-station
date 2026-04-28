"""Tkinter login dialog helper for the Tachibana e-shiten venue.

Architecture: [docs/✅tachibana/architecture.md §7.4](../../../docs/✅tachibana/architecture.md#74).

Run as a subprocess of `engine.exchanges.tachibana_login_flow`:

    python -m engine.exchanges.tachibana_login_dialog [--headless]

stdin: a single JSON line with the prefill / option payload
    {
        "prefill": {"user_id": str?, "is_demo": bool?},
        "allow_prod_choice": bool   # if False, prod radio is hidden (L2)
    }

stdout: a single JSON line with the result
    {"status": "ok",        "user_id": str, "password": str, "is_demo": bool}
    {"status": "cancelled"} (× / ESC / Cancel button)

stderr: human-readable progress / errors. Never echoes the password.

Headless mode (--headless): no GUI is shown. Instead, a single JSON
object is read from stdin and validated using the same rules the real
GUI applies (non-empty user_id / password). The dialog returns the
validated values without prompting — used by pytest to test the
validation logic without a display server.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional


log = logging.getLogger(__name__)


# ── Constants (L2: fixed Japanese strings; never come from stdin) ────────────

_TITLE = "立花証券 ログイン"
_LABEL_USER = "ユーザーID"
_LABEL_PASS = "パスワード"
_LABEL_ENV_DEMO = "デモ環境"
_LABEL_ENV_PROD = "本番環境"
_LABEL_LOGIN = "ログイン"
_LABEL_CANCEL = "キャンセル"
_NOTE_DEMO_FIXED = (
    "デモ環境固定（本番接続には TACHIBANA_ALLOW_PROD=1 env が別途必要です）"
)
_NOTE_PHONE = (
    "※ 立花の電話認証を済ませた状態で実行してください。\n"
    "※ 認証情報は OS の keyring に保存されます。"
)


# ── Validation (shared between GUI and headless mode) ────────────────────────


def validate_input(user_id: str, password: str) -> Optional[str]:
    """Return None if input is valid, an error string otherwise."""
    if not user_id.strip():
        return "ユーザー ID を入力してください"
    if not password:
        return "パスワードを入力してください"
    return None


def _emit_result(result: dict) -> None:
    """Write the result JSON to stdout as a single line and flush."""
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ── GUI mode ────────────────────────────────────────────────────────────────


def _run_gui(stdin_payload: dict) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    prefill: dict = stdin_payload.get("prefill") or {}
    allow_prod = bool(stdin_payload.get("allow_prod_choice", False))

    root = tk.Tk()
    root.title(_TITLE)
    root.resizable(False, False)

    # Track result to write on exit.
    result: dict = {"status": "cancelled"}

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")

    user_var = tk.StringVar(value=prefill.get("user_id", ""))
    pass_var = tk.StringVar(value="")
    is_demo_var = tk.BooleanVar(value=bool(prefill.get("is_demo", True)))

    ttk.Label(frame, text=_LABEL_USER).grid(row=0, column=0, sticky="w", pady=4)
    user_entry = ttk.Entry(frame, textvariable=user_var, width=24)
    user_entry.grid(row=0, column=1, sticky="ew", pady=4)

    ttk.Label(frame, text=_LABEL_PASS).grid(row=1, column=0, sticky="w", pady=4)
    pass_entry = ttk.Entry(frame, textvariable=pass_var, show="*", width=24)
    pass_entry.grid(row=1, column=1, sticky="ew", pady=4)

    if allow_prod:
        env_frame = ttk.Frame(frame)
        env_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Radiobutton(
            env_frame, text=_LABEL_ENV_DEMO, variable=is_demo_var, value=True
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            env_frame, text=_LABEL_ENV_PROD, variable=is_demo_var, value=False
        ).pack(side="left", padx=4)
    else:
        # L2 修正: prod radio hidden — show fixed-demo notice instead.
        ttk.Label(frame, text=_NOTE_DEMO_FIXED, foreground="#555").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=4
        )
        is_demo_var.set(True)

    ttk.Label(frame, text=_NOTE_PHONE, foreground="#555", justify="left").grid(
        row=3, column=0, columnspan=2, sticky="w", pady=(8, 4)
    )

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=4, column=0, columnspan=2, pady=(8, 0))

    def on_login() -> None:
        err = validate_input(user_var.get(), pass_var.get())
        if err is not None:
            messagebox.showerror(_TITLE, err)
            return
        result.clear()
        result.update(
            status="ok",
            user_id=user_var.get().strip(),
            password=pass_var.get(),
            is_demo=bool(is_demo_var.get()),
        )
        root.destroy()

    def on_cancel() -> None:
        result.clear()
        result["status"] = "cancelled"
        root.destroy()

    ttk.Button(button_frame, text=_LABEL_CANCEL, command=on_cancel).pack(
        side="right", padx=4
    )
    ttk.Button(button_frame, text=_LABEL_LOGIN, command=on_login).pack(
        side="right", padx=4
    )

    root.bind("<Return>", lambda _e: on_login())
    root.bind("<Escape>", lambda _e: on_cancel())
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    user_entry.focus_set()
    root.mainloop()

    _emit_result(result)
    return 0


# ── Headless mode ───────────────────────────────────────────────────────────


def _run_headless(stdin_payload: dict) -> int:
    """Validate the prefill payload as if it were the user's input.

    The headless mode is the test seam — pytest can drive
    `tachibana_login_dialog` end-to-end without a display server. The
    JSON shape for headless input matches what the GUI would emit:

        {"prefill": {"user_id": str, "password": str, "is_demo": bool?}}

    On valid input emit `{"status": "ok", ...}`; on missing fields emit
    `{"status": "cancelled"}` (the simplest signal for an aborted form).
    """
    prefill = stdin_payload.get("prefill") or {}
    allow_prod = bool(stdin_payload.get("allow_prod_choice", False))
    user_id = str(prefill.get("user_id", ""))
    password = str(prefill.get("password", ""))
    is_demo = bool(prefill.get("is_demo", True))
    # M16: when prod is not allowed, force is_demo=True regardless of
    # what the prefill carried. This mirrors the GUI's L2 behaviour
    # ("デモ環境固定" notice + radio hidden) and prevents a release
    # build from sliding into prod via an unsanitised prefill.
    if not allow_prod:
        is_demo = True

    err = validate_input(user_id, password)
    if err is not None:
        sys.stderr.write(f"headless: {err}\n")
        _emit_result({"status": "cancelled"})
        return 0

    _emit_result(
        {
            "status": "ok",
            "user_id": user_id.strip(),
            "password": password,
            "is_demo": is_demo,
        }
    )
    return 0


def _read_stdin_payload() -> dict:
    """Read the prefill / options JSON from stdin.

    M-4 (2026-04-25): an empty stdin used to silently fall through as
    `{}`, which the GUI then rendered as a blank dialog in production
    and the headless mode treated as a missing-input cancellation —
    but the helper still exited 0, leaving the parent unable to tell
    "spawn raced" apart from "user cancelled". Treat empty stdin as a
    hard cancel: emit `{"status": "cancelled"}` and exit non-zero so
    `_spawn_login_dialog` can attribute the failure correctly.

    M-IO ラウンド 5: also guard `OSError` from `readline()` (detached
    console on Windows, pty tear-down on Linux). Without this guard
    the helper exited with an unhandled traceback instead of the
    structured `{"status":"cancelled"}` line, leaving the parent
    unable to classify the failure under the venue contract."""
    try:
        raw = sys.stdin.readline()
    except OSError as exc:
        sys.stderr.write(f"login dialog: stdin read failed: {exc}\n")
        _emit_result({"status": "cancelled"})
        sys.exit(2)
    if not raw.strip():
        sys.stderr.write("login dialog: no stdin payload received\n")
        _emit_result({"status": "cancelled"})
        sys.exit(2)
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Tachibana login dialog")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Skip GUI and validate stdin only (test mode).",
    )
    parser.add_argument(
        "--auto-cancel",
        action="store_true",
        help=(
            'Immediately emit {"status":"cancelled"} without reading stdin or '
            "showing a dialog. Used by CI tkinter smoke tests (F-M2c) and the "
            "E2E cancel-injection path to verify the cancel contract without a "
            "real display server or user interaction."
        ),
    )
    args = parser.parse_args()

    if args.auto_cancel:
        _emit_result({"status": "cancelled"})
        return 0

    try:
        payload = _read_stdin_payload()
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"failed to parse stdin: {exc}\n")
        _emit_result({"status": "cancelled"})
        return 2

    if args.headless:
        return _run_headless(payload)
    try:
        return _run_gui(payload)
    except Exception as exc:  # pragma: no cover — last-resort error path
        sys.stderr.write(f"login dialog crashed: {exc}\n")
        _emit_result({"status": "cancelled"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
