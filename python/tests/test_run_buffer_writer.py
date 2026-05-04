"""F9a: RunBuffer 書き出しのテスト（TDD: RED → GREEN → REFACTOR）。

P9-wandb-submit-menu.md §F9a DoD より:
  - ExecutionMarker → fills.jsonl
  - ReplayBuyingPower → equity.jsonl
  - StrategySignal → narrative.jsonl
  - finish() → meta.json status="completed"
  - abort() → meta.json status="aborted"
  - jsonl の fsync が meta.json rewrite より前（呼出し順序を mock で assert）
  - PII 禁止キー が出力に出ない
  - status="running" & no .lock → sweep 時 "aborted" に正規化
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.run_buffer import RunBuffer, sweep_old_runs


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_run_buffer(tmp_path: Path, run_id: str = "test-run-001") -> RunBuffer:
    """テスト用 RunBuffer を tmp_path 配下に作成する。"""
    return RunBuffer(
        run_id=run_id,
        strategy_file="docs/example/buy_and_hold.py",
        scenario={"instrument": "1301.TSE"},
        base_dir=tmp_path,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# test 1: ExecutionMarker を write_event → fills.jsonl に書かれる
# ---------------------------------------------------------------------------


def test_write_fills_event(tmp_path: Path) -> None:
    """ExecutionMarker を write_event → fills.jsonl に書かれる。"""
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "ExecutionMarker",
        "strategy_id": "user-strategy",
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "2500.0",
        "qty": "100",
        "ts_event_ms": 1714800123000,
    }
    rb.write_event(evt)

    fills_path = rb.run_dir / "fills.jsonl"
    assert fills_path.exists(), "fills.jsonl が生成されていない"

    rows = _read_jsonl(fills_path)
    assert len(rows) == 1
    assert rows[0]["side"] == "BUY"
    assert rows[0]["price"] == "2500.0"
    # event キーは除去されている（allow-list から外れているため）
    assert "event" not in rows[0]


# ---------------------------------------------------------------------------
# test 2: ReplayBuyingPower を write_event → equity.jsonl に書かれる
# ---------------------------------------------------------------------------


def test_write_equity_event(tmp_path: Path) -> None:
    """ReplayBuyingPower を write_event → equity.jsonl に書かれる。"""
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "ReplayBuyingPower",
        "strategy_id": "user-strategy",
        "cash": "990000",
        "buying_power": "990000",
        "equity": "995000",
        "ts_event_ms": 1714800123001,
    }
    rb.write_event(evt)

    equity_path = rb.run_dir / "equity.jsonl"
    assert equity_path.exists(), "equity.jsonl が生成されていない"

    rows = _read_jsonl(equity_path)
    assert len(rows) == 1
    assert rows[0]["equity"] == "995000"
    assert rows[0]["cash"] == "990000"
    # event キーは除去されている
    assert "event" not in rows[0]


# ---------------------------------------------------------------------------
# test 3: StrategySignal を write_event → narrative.jsonl に書かれる
# ---------------------------------------------------------------------------


def test_write_narrative_event(tmp_path: Path) -> None:
    """StrategySignal を write_event → narrative.jsonl に書かれる。"""
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "StrategySignal",
        "strategy_id": "user-strategy",
        "instrument_id": "1301.TSE",
        "signal_kind": "EntryLong",
        "side": "BUY",
        "price": "2500.0",
        "tag": "momentum",
        "note": "RSI cross",
        "ts_event_ms": 1714800123002,
    }
    rb.write_event(evt)

    narrative_path = rb.run_dir / "narrative.jsonl"
    assert narrative_path.exists(), "narrative.jsonl が生成されていない"

    rows = _read_jsonl(narrative_path)
    assert len(rows) == 1
    assert rows[0]["signal_kind"] == "EntryLong"
    assert rows[0]["tag"] == "momentum"
    assert rows[0]["note"] == "RSI cross"
    # event キーは除去されている
    assert "event" not in rows[0]


# ---------------------------------------------------------------------------
# test 4: finish() → meta.json の status が "completed"
# ---------------------------------------------------------------------------


def test_finish_writes_meta_completed(tmp_path: Path) -> None:
    """finish() → meta.json の status が "completed"、finished_at が設定される。"""
    rb = _make_run_buffer(tmp_path)

    # 起動直後は "running"
    meta_path = rb.run_dir / "meta.json"
    assert meta_path.exists(), "meta.json が生成されていない"
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "running"

    rb.finish()

    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "completed"
    assert meta["finished_at"] is not None
    assert meta["run_id"] == "test-run-001"


# ---------------------------------------------------------------------------
# test 5: abort() → status が "aborted"
# ---------------------------------------------------------------------------


def test_abort_writes_meta_aborted(tmp_path: Path) -> None:
    """abort() → meta.json の status が "aborted"。"""
    rb = _make_run_buffer(tmp_path)

    rb.abort()

    meta_path = rb.run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "aborted"


# ---------------------------------------------------------------------------
# test 6: finish() 呼出し順序: jsonl の fsync が meta.json rewrite より前
# ---------------------------------------------------------------------------


def test_meta_json_flushed_after_jsonl(tmp_path: Path) -> None:
    """finish() で jsonl の fsync が meta.json rewrite より前に行われること。

    os.fsync と os.replace（atomic rewrite）の呼出し順序を assert する。
    """
    rb = _make_run_buffer(tmp_path)

    # equity.jsonl にデータを書いてファイルを開かせる
    rb.write_event({
        "event": "ReplayBuyingPower",
        "strategy_id": "s1",
        "cash": "1000000",
        "buying_power": "1000000",
        "equity": "1000000",
        "ts_event_ms": 1714800123000,
    })

    call_log: list[str] = []

    original_fsync = os.fsync

    def mock_fsync(fd: int) -> None:
        call_log.append(f"fsync:{fd}")
        original_fsync(fd)

    original_replace = os.replace

    def mock_replace(src: str, dst: str) -> None:
        call_log.append("meta_replace")
        original_replace(src, dst)

    with patch("os.fsync", side_effect=mock_fsync):
        with patch("os.replace", side_effect=mock_replace):
            rb.finish()

    # fsync が meta_replace より前に少なくとも 1 回呼ばれていること
    fsync_indices = [i for i, c in enumerate(call_log) if c.startswith("fsync:")]
    replace_indices = [i for i, c in enumerate(call_log) if c == "meta_replace"]

    assert len(fsync_indices) >= 1, "fsync が 1 回も呼ばれていない"
    assert len(replace_indices) >= 1, "meta_replace（os.replace）が呼ばれていない"
    assert max(fsync_indices) < min(replace_indices), (
        f"fsync が meta_replace より後に呼ばれている: {call_log}"
    )


# ---------------------------------------------------------------------------
# test 7: PII scrub が禁止キーを除去する
# ---------------------------------------------------------------------------


def test_pii_scrub_removes_forbidden_keys(tmp_path: Path) -> None:
    """禁止キー（venue_order_id, client_order_id, raw_data, payload）を含む
    ExecutionMarker イベントが fills.jsonl に書かれないこと（scrub で None → skip）。
    """
    rb = _make_run_buffer(tmp_path)
    evt = {
        "event": "ExecutionMarker",
        "strategy_id": "user-strategy",
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "2500.0",
        "qty": "100",
        "ts_event_ms": 1714800123000,
        # 禁止キー（engine.pii_scrub.FORBIDDEN_KEYS に含まれるもの）
        "venue_order_id": "V12345678",
        "client_order_id": "C-abc-001",
        "raw_data": {"secret": "leak"},
        "payload": "raw_bytes",
    }
    rb.write_event(evt)

    fills_path = rb.run_dir / "fills.jsonl"
    rows = _read_jsonl(fills_path)

    # 禁止キーを持つイベントは None が返るため skip → 0行
    assert len(rows) == 0, (
        f"禁止キーを含む event が fills.jsonl に書かれた: {rows}"
    )


# ---------------------------------------------------------------------------
# test 8: status="running" & no .lock → sweep 時 "aborted" に正規化
# ---------------------------------------------------------------------------


def test_running_without_lock_is_normalized_to_aborted(tmp_path: Path) -> None:
    """status="running" & .lock ファイル無し → sweep_old_runs が "aborted" に正規化する。"""
    # running のまま放置された run-buffer を手動作成
    run_dir = tmp_path / "1714800123-strategy-1301_TSE"
    run_dir.mkdir(parents=True)

    meta = {
        "schema_version": 1,
        "run_id": "1714800123-strategy-1301_TSE",
        "strategy_file": "strategy.py",
        "strategy_sha256": "abc",
        "git_rev": "unknown",
        "scenario": None,
        "started_at": "2026-05-04T07:42:03Z",
        "finished_at": None,
        "status": "running",
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    # .lock ファイルなし

    sweep_old_runs(tmp_path, max_runs=30)

    meta_after = json.loads((run_dir / "meta.json").read_text())
    assert meta_after["status"] == "aborted", (
        f"running & no .lock の run が aborted に正規化されていない: {meta_after['status']}"
    )
