"""OKX depth syncer tests — Phase 3."""

from __future__ import annotations

import pytest

from engine.exchanges.okex import OkexDepthSyncer


def _make_syncer(outbox=None):
    if outbox is None:
        outbox = []
    return OkexDepthSyncer(
        venue="okex",
        ticker="BTC-USDT",
        stream_session_id="test-session",
        outbox=outbox,
    ), outbox


def _snapshot(update_id, bids=None, asks=None):
    return {
        "action": "snapshot",
        "update_id": update_id,
        "bids": bids or [["30000", "1.0", "0", "1"]],
        "asks": asks or [["30001", "0.5", "0", "1"]],
    }


def _delta(update_id, bids=None, asks=None):
    return {
        "action": "update",
        "update_id": update_id,
        "bids": bids or [],
        "asks": asks or [["30002", "0.3", "0", "1"]],
    }


# ---------------------------------------------------------------------------
# Snapshot handling
# ---------------------------------------------------------------------------


def test_snapshot_emits_depth_snapshot():
    syncer, outbox = _make_syncer()
    msg = _snapshot(update_id=1000)
    syncer.process_message(**msg)

    assert len(outbox) == 1
    event = outbox[0]
    assert event["event"] == "DepthSnapshot"
    assert event["venue"] == "okex"
    assert event["ticker"] == "BTC-USDT"
    assert event["sequence_id"] == 1000
    assert len(event["bids"]) == 1
    assert event["bids"][0]["price"] == "30000"


def test_snapshot_sets_initialized_state():
    syncer, _ = _make_syncer()
    assert not syncer._initialized
    syncer.process_message(**_snapshot(update_id=1000))
    assert syncer._initialized


# ---------------------------------------------------------------------------
# Delta handling
# ---------------------------------------------------------------------------


def test_delta_after_snapshot_emits_depth_diff():
    syncer, outbox = _make_syncer()
    syncer.process_message(**_snapshot(update_id=1000))
    outbox.clear()

    syncer.process_message(**_delta(update_id=1001))
    assert len(outbox) == 1
    event = outbox[0]
    assert event["event"] == "DepthDiff"
    assert event["sequence_id"] == 1001
    assert event["prev_sequence_id"] == 1000


def test_stale_delta_is_dropped():
    syncer, outbox = _make_syncer()
    syncer.process_message(**_snapshot(update_id=1000))
    syncer.process_message(**_delta(update_id=1001))
    outbox.clear()

    # Replay stale delta (same seqId as already applied)
    syncer.process_message(**_delta(update_id=1001))
    assert len(outbox) == 0


def test_delta_before_snapshot_is_buffered():
    syncer, outbox = _make_syncer()
    # Send delta before snapshot arrives — should buffer, not emit
    syncer.process_message(**_delta(update_id=1001))
    assert len(outbox) == 0


def test_pending_deltas_replayed_after_snapshot():
    syncer, outbox = _make_syncer()
    # Buffer a delta before snapshot
    syncer.process_message(**_delta(update_id=1001))
    assert len(outbox) == 0

    # Now apply snapshot at 1000
    syncer.process_message(**_snapshot(update_id=1000))
    # Should have: DepthSnapshot + DepthDiff (replayed)
    events = [e["event"] for e in outbox]
    assert "DepthSnapshot" in events
    assert "DepthDiff" in events


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def test_non_consecutive_delta_emits_depth_gap():
    syncer, outbox = _make_syncer()
    syncer.process_message(**_snapshot(update_id=1000))
    outbox.clear()

    # Skip seqId 1001 → gap
    syncer.process_message(**_delta(update_id=1002))
    gap_events = [e for e in outbox if e["event"] == "DepthGap"]
    assert len(gap_events) == 1
    assert gap_events[0]["venue"] == "okex"


def test_gap_sets_needs_resync():
    syncer, outbox = _make_syncer()
    syncer.process_message(**_snapshot(update_id=1000))
    assert not syncer.needs_resync

    syncer.process_message(**_delta(update_id=1002))
    assert syncer.needs_resync


def test_buffer_overflow_emits_gap_and_sets_needs_resync():
    syncer, outbox = _make_syncer()
    # Fill beyond MAX_PENDING without snapshot
    for i in range(OkexDepthSyncer.MAX_PENDING + 1):
        syncer.process_message(**_delta(update_id=1000 + i))

    gap_events = [e for e in outbox if e["event"] == "DepthGap"]
    assert len(gap_events) >= 1
    assert syncer.needs_resync


def test_new_snapshot_after_gap_clears_needs_resync():
    syncer, outbox = _make_syncer()
    syncer.process_message(**_snapshot(update_id=1000))
    syncer.process_message(**_delta(update_id=1002))  # gap
    assert syncer.needs_resync

    # New snapshot (e.g., after WS reconnect)
    syncer.process_message(**_snapshot(update_id=2000))
    assert not syncer.needs_resync
