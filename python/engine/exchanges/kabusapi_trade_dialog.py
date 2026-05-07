"""kabuステーション 取引パスワード収集 tkinter ダイアログ。

このファイルはサブプロセスとして起動する:
    python -m engine.exchanges.kabusapi_trade_dialog

結果は stdout に JSON で返す:
  {"status": "ok", "trade_password": "..."}
  {"status": "cancelled"}
"""
from __future__ import annotations

import json
import sys


_TITLE = "kabuステーション 取引パスワード"
_LABEL_PASS = "取引パスワード"
_LABEL_OK = "OK"
_LABEL_CANCEL = "キャンセル"


def _emit_result(result: dict) -> None:
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

    ttk.Label(frame, text=_LABEL_PASS).grid(row=0, column=0, sticky="w", pady=4)
    pass_entry = ttk.Entry(frame, textvariable=pass_var, show="*", width=24)
    pass_entry.grid(row=0, column=1, sticky="ew", pady=4)

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=1, column=0, columnspan=2, pady=(8, 0))

    def on_ok() -> None:
        pw = pass_var.get()
        if not pw:
            messagebox.showerror(_TITLE, "取引パスワードを入力してください")
            return
        result.clear()
        result.update(status="ok", trade_password=pw)
        root.destroy()

    def on_cancel() -> None:
        result.clear()
        result["status"] = "cancelled"
        root.destroy()

    ttk.Button(button_frame, text=_LABEL_CANCEL, command=on_cancel).pack(side="right", padx=4)
    ttk.Button(button_frame, text=_LABEL_OK, command=on_ok).pack(side="right", padx=4)

    root.bind("<Return>", lambda _e: on_ok())
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
        sys.stderr.write(f"trade dialog crashed: {exc}\n")
        _emit_result({"status": "cancelled"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
