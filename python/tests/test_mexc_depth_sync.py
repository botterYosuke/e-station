"""MEXC depth syncer tests — Phase 3."""

from __future__ import annotations

import pytest

from engine.exchanges.mexc import MexcDepthSyncer


def _make_syncer(outbox=None):
    if outbox is None:
        outbox = []
    return MexcDepthSyncer(
        venue="mexc",
        ticker="BTC_USDT",
        market="spot",
        stream_session_id="test-session",
        outbox=outbox,
    ), outbox


def _bids(items=None):
    return items or [{"price": "29999.0", "qty": "1.0"}]


def _asks(items=None):
    return items or [{"price": "30001.0", "qty": "0.5"}]


# ---------------------------------------------------------------------------
# Snapshot handling
# ---------------------------------------------------------------------------


def test_snapshot_emits_depth_snapshot():
    syncer, outbox = _make_syncer()
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())

    assert len(outbox) == 1
    event = outbox[0]
    assert event["event"] == "DepthSnapshot"
    assert event["venue"] == "mexc"
    assert event["ticker"] == "BTC_USDT"
    assert event["sequence_id"] == 1000
    assert event["bids"] == _bids()
    assert event["asks"] == _asks()


def test_snapshot_sets_initialized_state():
    syncer, _ = _make_syncer()
    assert not syncer._snapshot_ready
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    assert syncer._snapshot_ready


def test_snapshot_sets_applied_version():
    syncer, _ = _make_syncer()
    syncer.apply_snapshot(version=5000, bids=_bids(), asks=_asks())
    assert syncer._applied_version == 5000


# ---------------------------------------------------------------------------
# Diff handling (after snapshot)
# ---------------------------------------------------------------------------


def test_diff_after_snapshot_emits_depth_diff():
    syncer, outbox = _make_syncer()
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    outbox.clear()

    syncer.process_diff(version=1001, bids=_bids(), asks=_asks())
    assert len(outbox) == 1
    event = outbox[0]
    assert event["event"] == "DepthDiff"
    assert event["sequence_id"] == 1001
    assert event["prev_sequence_id"] == 1000


def test_stale_diff_is_dropped():
    syncer, outbox = _make_syncer()
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    syncer.process_diff(version=1001, bids=_bids(), asks=_asks())
    outbox.clear()

    # Replay stale diff
    syncer.process_diff(version=1001, bids=_bids(), asks=_asks())
    assert len(outbox) == 0


def test_diff_before_snapshot_is_buffered():
    syncer, outbox = _make_syncer()
    syncer.process_diff(version=1001, bids=_bids(), asks=_asks())
    assert len(outbox) == 0


def test_buffered_diffs_replayed_after_snapshot():
    syncer, outbox = _make_syncer()
    # Buffer a diff before snapshot
    syncer.process_diff(version=1001, bids=_bids(), asks=_asks())
    assert len(outbox) == 0

    # Apply snapshot at 1000 → diff 1001 should be replayed
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    events = [e["event"] for e in outbox]
    assert "DepthSnapshot" in events
    assert "DepthDiff" in events


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def test_non_consecutive_diff_emits_depth_gap():
    syncer, outbox = _make_syncer()
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    outbox.clear()

    # Skip version 1001 → gap
    syncer.process_diff(version=1002, bids=_bids(), asks=_asks())
    gap_events = [e for e in outbox if e["event"] == "DepthGap"]
    assert len(gap_events) == 1
    assert gap_events[0]["venue"] == "mexc"
    assert gap_events[0]["ticker"] == "BTC_USDT"


def test_gap_sets_needs_resync():
    syncer, _ = _make_syncer()
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    assert not syncer.needs_resync

    syncer.process_diff(version=1002, bids=_bids(), asks=_asks())
    assert syncer.needs_resync


def test_buffer_overflow_emits_gap_and_sets_needs_resync():
    syncer, outbox = _make_syncer()
    for i in range(MexcDepthSyncer.MAX_PENDING + 1):
        syncer.process_diff(version=1000 + i, bids=_bids(), asks=_asks())

    gap_events = [e for e in outbox if e["event"] == "DepthGap"]
    assert len(gap_events) >= 1
    assert syncer.needs_resync


def test_new_snapshot_after_gap_clears_needs_resync():
    syncer, outbox = _make_syncer()
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    syncer.process_diff(version=1002, bids=_bids(), asks=_asks())  # gap
    assert syncer.needs_resync

    # New snapshot (after WS reconnect)
    syncer.apply_snapshot(version=2000, bids=_bids(), asks=_asks())
    assert not syncer.needs_resync


def test_diff_during_needs_resync_is_buffered():
    syncer, outbox = _make_syncer()
    syncer.apply_snapshot(version=1000, bids=_bids(), asks=_asks())
    syncer.process_diff(version=1002, bids=_bids(), asks=_asks())  # gap
    outbox.clear()
    assert syncer.needs_resync

    # Diffs during resync should be buffered, not emitted
    syncer.process_diff(version=1003, bids=_bids(), asks=_asks())
    assert len(outbox) == 0
