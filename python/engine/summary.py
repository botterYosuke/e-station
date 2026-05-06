"""Aggregate summary for a replay run-buffer.

Computes the four headline metrics — `total_pnl`, `max_drawdown`,
`trade_count`, `win_rate` — from `equity.jsonl` / `fills.jsonl` plus two
diagnostic fields (`equity_points`, `fills_count`).

This module is a **stable contract** consumed by:
    - blacksheep/publish_run.py  (uploads metrics; W&B responsibility migrated to blacksheep)
    - external research repos ingesting run-buffers

Definitions (kept simple so audit by hand is feasible):
    - total_pnl    : equity[-1] - equity[0]
    - max_drawdown : max(running_peak - equity), absolute
    - trade_count  : FIFO-matched closing slices
    - win_rate     : fraction of closing slices with realised pnl > 0
                     (None when trade_count == 0)

Out of scope: fee_total — the replay fill event does not carry commission
yet. That requires an upstream schema change (replay -> fills.jsonl) before
it can be aggregated here.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional


def _coerce_float(value) -> Optional[float]:
    """Best-effort numeric parse for jsonl values that may arrive as str."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def compute_summary(run_buffer_dir: Path) -> dict:
    """Compute aggregate metrics from a run-buffer's equity.jsonl / fills.jsonl.

    Returns a dict suitable for ``run.summary.update(...)`` and for being
    persisted as ``summary.json`` next to (or derived from) the source files.
    """
    run_buffer_dir = Path(run_buffer_dir)
    equity_rows = _read_jsonl(run_buffer_dir / "equity.jsonl")
    fills_rows = _read_jsonl(run_buffer_dir / "fills.jsonl")

    equity_values: list[float] = []
    for row in equity_rows:
        v = _coerce_float(row.get("equity"))
        if v is not None:
            equity_values.append(v)

    if equity_values:
        total_pnl = equity_values[-1] - equity_values[0]
        peak = equity_values[0]
        max_dd = 0.0
        for v in equity_values:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
    else:
        total_pnl = 0.0
        max_dd = 0.0

    # Trade pairing: walk fills chronologically, FIFO-match opposite-side
    # quantities. Each closing slice accrues one realised-pnl entry and
    # counts as one trade.
    open_lots: list[tuple[str, float, float]] = []  # (side, qty_remaining, price)
    realised: list[float] = []
    for row in fills_rows:
        side = row.get("side")
        qty = _coerce_float(row.get("qty"))
        price = _coerce_float(row.get("price"))
        if side not in ("BUY", "SELL") or qty is None or price is None or qty <= 0:
            continue
        opposite = "SELL" if side == "BUY" else "BUY"
        remaining = qty
        while remaining > 0 and open_lots and open_lots[0][0] == opposite:
            entry_side, entry_qty, entry_price = open_lots[0]
            close_qty = min(remaining, entry_qty)
            sign = 1.0 if entry_side == "BUY" else -1.0
            realised.append((price - entry_price) * sign * close_qty)
            entry_qty -= close_qty
            remaining -= close_qty
            if entry_qty <= 0:
                open_lots.pop(0)
            else:
                open_lots[0] = (entry_side, entry_qty, entry_price)
        if remaining > 0:
            open_lots.append((side, remaining, price))

    trade_count = len(realised)
    if trade_count > 0:
        win_rate: Optional[float] = sum(1 for r in realised if r > 0) / trade_count
    else:
        win_rate = None

    return {
        "total_pnl": total_pnl,
        "max_drawdown": max_dd,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "equity_points": len(equity_values),
        "fills_count": len(fills_rows),
    }


def write_summary_json(target_dir: Path, summary: dict) -> Path:
    """Persist *summary* as ``summary.json`` under *target_dir* atomically."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "summary.json"
    fd, tmp_path = tempfile.mkstemp(prefix="summary.", suffix=".json", dir=str(target_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target)
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target
