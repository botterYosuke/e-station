"""kabuステーション API パスワード収集 tkinter ダイアログ。

このファイルはサブプロセスとして起動する:
    python -m engine.exchanges.kabusapi_login_dialog

結果は stdout に JSON で返す:
  {"status": "ok", "api_password": "...", "env": "verify"}
  {"status": "cancelled"}
"""
from __future__ import annotations

import json
import sys


_TITLE = "kabuステーション ログイン"
_LABEL_PASS = "API パスワード"
_LABEL_ENV_VERIFY = "検証環境 (localhost:18081)"
_LABEL_ENV_PROD = "本番環境 (localhost:18080)"
_LABEL_LOGIN = "ログイン"
_LABEL_CANCEL = "キャンセル"


def _emit_result(result: dict) -> None:
    """Write the result JSON to stdout as a single line and flush."""
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _run_gui() -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(_TITLE)
    root.resizable(False, False)

    result: dict = {"status": "cancelled"}

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")

    pass_var = tk.StringVar(value="")
    env_var = tk.StringVar(value="verify")

    ttk.Label(frame, text=_LABEL_PASS).grid(row=0, column=0, sticky="w", pady=4)
    pass_entry = ttk.Entry(frame, textvariable=pass_var, show="*", width=24)
    pass_entry.grid(row=0, column=1, sticky="ew", pady=4)

    env_frame = ttk.Frame(frame)
    env_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
    ttk.Radiobutton(
        env_frame, text=_LABEL_ENV_VERIFY, variable=env_var, value="verify"
    ).pack(side="left", padx=4)
    ttk.Radiobutton(
        env_frame, text=_LABEL_ENV_PROD, variable=env_var, value="prod"
    ).pack(side="left", padx=4)

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=2, column=0, columnspan=2, pady=(8, 0))

    def on_login() -> None:
        pw = pass_var.get()
        if not pw:
            messagebox.showerror(_TITLE, "API パスワードを入力してください")
            return
        result.clear()
        result.update(
            status="ok",
            api_password=pw,
            env=env_var.get(),
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

    pass_entry.focus_set()
    root.mainloop()

    _emit_result(result)
    return 0


def main() -> int:
    try:
        return _run_gui()
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"login dialog crashed: {exc}\n")
        _emit_result({"status": "cancelled"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
