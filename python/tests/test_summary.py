"""Tests for engine.summary.

These complement the regression tests in
examples/wandb/tests/test_submit_run.py::TestComputeSummary by pinning the
canonical engine-side import path. If `compute_summary` is ever moved or
renamed inside engine/, both test suites must be updated together.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.summary import compute_summary, write_summary_json


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_compute_summary_normal_case(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "equity.jsonl", [
        {"ts": 1, "equity": "1000000"},
        {"ts": 2, "equity": "1010000"},
        {"ts": 3, "equity": "990000"},
        {"ts": 4, "equity": "998959"},
    ])
    _write_jsonl(tmp_path / "fills.jsonl", [
        {"ts": 1, "side": "BUY",  "qty": "100", "price": "3950"},
        {"ts": 2, "side": "SELL", "qty": "100", "price": "3955"},
        {"ts": 3, "side": "BUY",  "qty": "100", "price": "4000"},
        {"ts": 4, "side": "SELL", "qty": "100", "price": "3990"},
    ])

    s = compute_summary(tmp_path)

    assert s["total_pnl"] == pytest.approx(-1041.0)
    assert s["max_drawdown"] == pytest.approx(20000.0)
    assert s["trade_count"] == 2
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["equity_points"] == 4
    assert s["fills_count"] == 4


def test_compute_summary_empty_buffer(tmp_path: Path) -> None:
    s = compute_summary(tmp_path)
    assert s == {
        "total_pnl": 0.0,
        "max_drawdown": 0.0,
        "trade_count": 0,
        "win_rate": None,
        "equity_points": 0,
        "fills_count": 0,
    }


def test_write_summary_json_atomic_create(tmp_path: Path) -> None:
    target_dir = tmp_path / "Silver" / "runs" / "abc"
    summary = {"total_pnl": 1.5, "trade_count": 2, "win_rate": 0.5}

    written = write_summary_json(target_dir, summary)

    assert written == target_dir / "summary.json"
    assert json.loads(written.read_text(encoding="utf-8")) == summary
    # No leftover tempfiles in the target dir
    leftover = [p for p in target_dir.iterdir() if p.name != "summary.json"]
    assert leftover == []
