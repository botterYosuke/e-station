"""Tests for engine.summary.

These pin the canonical engine-side import path for `compute_summary` and
`write_summary_json`. If either is moved or renamed inside engine/, this test
suite must be updated. The publish path has been migrated to the blacksheep
repository (blacksheep/tests/test_publish_run.py).
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
    assert s["fee_total"] == pytest.approx(0.0)
    assert s["equity_points"] == 4
    assert s["fills_count"] == 4


def test_compute_summary_empty_buffer(tmp_path: Path) -> None:
    s = compute_summary(tmp_path)
    assert s == {
        "total_pnl": 0.0,
        "max_drawdown": 0.0,
        "trade_count": 0,
        "win_rate": None,
        "fee_total": 0.0,
        "equity_points": 0,
        "fills_count": 0,
    }


def test_compute_summary_sums_commission(tmp_path: Path) -> None:
    """schema 3.21+: commission フィールドが fee_total に積算される。"""
    _write_jsonl(tmp_path / "equity.jsonl", [
        {"ts": 1, "equity": "1000000"},
        {"ts": 2, "equity": "1000000"},
    ])
    _write_jsonl(tmp_path / "fills.jsonl", [
        {"ts": 1, "side": "BUY",  "qty": "100", "price": "3950", "commission": "59.25"},
        {"ts": 2, "side": "SELL", "qty": "100", "price": "3955", "commission": "59.32"},
        # commission 欠落行は 0 として扱われる（混在後方互換）
        {"ts": 3, "side": "BUY",  "qty": "50",  "price": "4000"},
        # 非数値は 0 として扱われる
        {"ts": 4, "side": "SELL", "qty": "50",  "price": "4010", "commission": "n/a"},
    ])

    s = compute_summary(tmp_path)
    assert s["fee_total"] == pytest.approx(118.57)
    assert s["fills_count"] == 4


def test_compute_summary_legacy_fills_no_commission_yields_zero_fee(tmp_path: Path) -> None:
    """schema 3.20 以前で生成された run-buffer (commission キー無し) は fee_total=0.0 を返す。

    旧 run-buffer 後方互換契約。blacksheep / 外部 ingest は新フィールド追加で破壊されない。
    """
    _write_jsonl(tmp_path / "equity.jsonl", [
        {"ts": 1, "equity": "1000000"},
        {"ts": 2, "equity": "1010000"},
    ])
    _write_jsonl(tmp_path / "fills.jsonl", [
        {"ts": 1, "side": "BUY",  "qty": "100", "price": "3950"},
        {"ts": 2, "side": "SELL", "qty": "100", "price": "3960"},
    ])

    s = compute_summary(tmp_path)
    assert s["fee_total"] == pytest.approx(0.0)
    assert s["fills_count"] == 2


def test_compute_summary_negative_commission_is_rebate(tmp_path: Path) -> None:
    """負の commission (rebate) は fee_total を減じる。符号は保持する設計。"""
    _write_jsonl(tmp_path / "equity.jsonl", [{"ts": 1, "equity": "0"}])
    _write_jsonl(tmp_path / "fills.jsonl", [
        {"ts": 1, "side": "BUY",  "qty": "1", "price": "100", "commission": "5.0"},
        {"ts": 2, "side": "SELL", "qty": "1", "price": "100", "commission": "-1.5"},
    ])

    s = compute_summary(tmp_path)
    assert s["fee_total"] == pytest.approx(3.5)


def test_compute_summary_empty_string_commission_treated_as_missing(
    tmp_path: Path, caplog: "pytest.LogCaptureFixture"
) -> None:
    """空文字 commission は欠落と同等に 0 扱い。WARN ログも出さない。"""
    import logging

    _write_jsonl(tmp_path / "equity.jsonl", [{"ts": 1, "equity": "0"}])
    _write_jsonl(tmp_path / "fills.jsonl", [
        {"ts": 1, "side": "BUY", "qty": "1", "price": "100", "commission": ""},
    ])

    with caplog.at_level(logging.WARNING, logger="engine.summary"):
        s = compute_summary(tmp_path)

    assert s["fee_total"] == pytest.approx(0.0)
    # 空文字は upstream missing sentinel として silent に扱う（audit ノイズ抑止）
    assert not any(
        "non-numeric commission" in rec.message for rec in caplog.records
    )


def test_compute_summary_non_numeric_commission_logs_warning(
    tmp_path: Path, caplog: "pytest.LogCaptureFixture"
) -> None:
    """非数値 commission は WARN ログを残し fee_total には加算されない。"""
    import logging

    _write_jsonl(tmp_path / "equity.jsonl", [{"ts": 1, "equity": "0"}])
    _write_jsonl(tmp_path / "fills.jsonl", [
        {"ts": 1, "side": "BUY", "qty": "1", "price": "100", "commission": "ABC"},
    ])

    with caplog.at_level(logging.WARNING, logger="engine.summary"):
        s = compute_summary(tmp_path)

    assert s["fee_total"] == pytest.approx(0.0)
    assert any("non-numeric commission" in rec.message for rec in caplog.records)


def test_write_summary_json_atomic_create(tmp_path: Path) -> None:
    target_dir = tmp_path / "Silver" / "runs" / "abc"
    summary = {"total_pnl": 1.5, "trade_count": 2, "win_rate": 0.5}

    written = write_summary_json(target_dir, summary)

    assert written == target_dir / "summary.json"
    assert json.loads(written.read_text(encoding="utf-8")) == summary
    # No leftover tempfiles in the target dir
    leftover = [p for p in target_dir.iterdir() if p.name != "summary.json"]
    assert leftover == []
