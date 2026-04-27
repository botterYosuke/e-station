"""TDD Red: BybitDepthSyncer gap detection and snapshot protocol tests."""

from __future__ import annotations

import pytest

from engine.exchanges.bybit import BybitDepthSyncer


class TestBybitDepthSyncer:

    def _make_outbox(self) -> list[dict]:
        return []

    def _make_syncer(self, ssid: str = "sess:1") -> tuple[BybitDepthSyncer, list[dict]]:
        outbox: list[dict] = []
        syncer = BybitDepthSyncer(
            venue="bybit",
            ticker="BTCUSDT",
            market="linear",
            stream_session_id=ssid,
            outbox=outbox,
        )
        return syncer, outbox

    def _snap(self, update_id: int, bids=None, asks=None) -> dict:
        return {
            "msg_type": "snapshot",
            "update_id": update_id,
            "bids": bids or [["68000.0", "1.5"]],
            "asks": asks or [["68001.0", "0.5"]],
        }

    def _delta(self, update_id: int, bids=None, asks=None) -> dict:
        return {
            "msg_type": "delta",
            "update_id": update_id,
            "bids": bids or [["68000.0", "2.0"]],
            "asks": asks or [],
        }

    # ------------------------------------------------------------------
    # Snapshot initialisation
    # ------------------------------------------------------------------

    def test_snapshot_emits_depth_snapshot_event(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._snap(1000))

        snap_events = [e for e in outbox if e["event"] == "DepthSnapshot"]
        assert len(snap_events) == 1
        ev = snap_events[0]
        assert ev["sequence_id"] == 1000
        assert ev["venue"] == "bybit"
        assert ev["ticker"] == "BTCUSDT"
        assert ev["bids"] == [{"price": "68000.0", "qty": "1.5"}]
        assert ev["asks"] == [{"price": "68001.0", "qty": "0.5"}]

    def test_snapshot_stream_session_id(self):
        syncer, outbox = self._make_syncer("mysess:7")
        syncer.process_message(**self._snap(500))
        assert outbox[0]["stream_session_id"] == "mysess:7"

    # ------------------------------------------------------------------
    # Delta after snapshot
    # ------------------------------------------------------------------

    def test_valid_delta_after_snapshot_emits_depth_diff(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._snap(1000))
        syncer.process_message(**self._delta(1001))

        diff_events = [e for e in outbox if e["event"] == "DepthDiff"]
        assert len(diff_events) == 1
        ev = diff_events[0]
        assert ev["sequence_id"] == 1001
        assert ev["prev_sequence_id"] == 1000

    def test_multiple_contiguous_deltas(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._snap(100))
        syncer.process_message(**self._delta(101))
        syncer.process_message(**self._delta(102))
        syncer.process_message(**self._delta(103))

        diff_events = [e for e in outbox if e["event"] == "DepthDiff"]
        assert len(diff_events) == 3
        assert [e["sequence_id"] for e in diff_events] == [101, 102, 103]

    def test_stale_delta_is_ignored(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._snap(200))
        # Already covered by snapshot
        syncer.process_message(**self._delta(150))

        diff_events = [e for e in outbox if e["event"] == "DepthDiff"]
        assert len(diff_events) == 0

    # ------------------------------------------------------------------
    # Gap detection
    # ------------------------------------------------------------------

    def test_gap_emits_depth_gap_and_sets_needs_resync(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._snap(100))
        syncer.process_message(**self._delta(101))
        # Gap: expected 102 but got 105
        syncer.process_message(**self._delta(105))

        gap_events = [e for e in outbox if e["event"] == "DepthGap"]
        assert len(gap_events) >= 1
        assert gap_events[0]["venue"] == "bybit"
        assert gap_events[0]["ticker"] == "BTCUSDT"
        assert syncer.needs_resync is True

    def test_after_gap_no_further_diffs_emitted(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._snap(100))
        syncer.process_message(**self._delta(105))  # Gap

        outbox_before = len(outbox)
        syncer.process_message(**self._delta(106))
        outbox_after = len(outbox)
        # No new DepthDiff should be emitted after gap
        new_diffs = [e for e in outbox[outbox_before:outbox_after] if e["event"] == "DepthDiff"]
        assert len(new_diffs) == 0

    # ------------------------------------------------------------------
    # Delta before snapshot (buffering)
    # ------------------------------------------------------------------

    def test_delta_before_snapshot_is_buffered(self):
        syncer, outbox = self._make_syncer()
        # Delta arrives before snapshot
        syncer.process_message(**self._delta(101))
        # No events yet
        assert len(outbox) == 0
        assert not syncer.needs_resync

    def test_buffered_delta_replayed_after_snapshot(self):
        syncer, outbox = self._make_syncer()
        # Buffer two deltas before snapshot
        syncer.process_message(**self._delta(101))
        syncer.process_message(**self._delta(102))
        # Now snapshot arrives
        syncer.process_message(**self._snap(100))

        diff_events = [e for e in outbox if e["event"] == "DepthDiff"]
        assert any(e["sequence_id"] == 101 for e in diff_events)
        assert any(e["sequence_id"] == 102 for e in diff_events)

    def test_buffer_overflow_emits_gap(self):
        syncer, outbox = self._make_syncer()
        # Fill buffer beyond MAX_PENDING
        for i in range(1, BybitDepthSyncer.MAX_PENDING + 2):
            syncer.process_message(**self._delta(i))

        gap_events = [e for e in outbox if e["event"] == "DepthGap"]
        assert len(gap_events) >= 1
        assert syncer.needs_resync is True
