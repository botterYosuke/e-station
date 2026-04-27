"""TDD Red: HyperliquidDepthSyncer tests.

Hyperliquid WS always sends full l2Book snapshots (no partial diffs).
Each message replaces the entire order book. The syncer emits DepthSnapshot
for every message with a monotonically increasing sequence_id.
"""

from __future__ import annotations

import pytest

from engine.exchanges.hyperliquid import HyperliquidDepthSyncer


class TestHyperliquidDepthSyncer:

    def _make_syncer(self, ssid: str = "sess:1") -> tuple[HyperliquidDepthSyncer, list[dict]]:
        outbox: list[dict] = []
        syncer = HyperliquidDepthSyncer(
            venue="hyperliquid",
            ticker="BTC",
            market="perp",
            stream_session_id=ssid,
            outbox=outbox,
        )
        return syncer, outbox

    def _book(self, time: int, bids=None, asks=None) -> dict:
        return {
            "time": time,
            "bids": bids or [{"px": "68000.0", "sz": "1.5"}],
            "asks": asks or [{"px": "68001.0", "sz": "0.5"}],
        }

    # ------------------------------------------------------------------
    # Basic snapshot emission
    # ------------------------------------------------------------------

    def test_first_message_emits_depth_snapshot(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._book(time=1700000000000))

        assert len(outbox) == 1
        ev = outbox[0]
        assert ev["event"] == "DepthSnapshot"
        assert ev["venue"] == "hyperliquid"
        assert ev["ticker"] == "BTC"
        assert ev["stream_session_id"] == "sess:1"

    def test_depth_snapshot_contains_bids_and_asks(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(
            time=1700000000000,
            bids=[{"px": "68000.0", "sz": "1.5"}, {"px": "67999.0", "sz": "2.0"}],
            asks=[{"px": "68001.0", "sz": "0.5"}],
        )

        ev = outbox[0]
        assert ev["bids"] == [
            {"price": "68000.0", "qty": "1.5"},
            {"price": "67999.0", "qty": "2.0"},
        ]
        assert ev["asks"] == [{"price": "68001.0", "qty": "0.5"}]

    def test_multiple_messages_each_emit_depth_snapshot(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._book(time=1700000001000))
        syncer.process_message(**self._book(time=1700000002000))
        syncer.process_message(**self._book(time=1700000003000))

        snap_events = [e for e in outbox if e["event"] == "DepthSnapshot"]
        assert len(snap_events) == 3

    # ------------------------------------------------------------------
    # Sequence ID monotonicity
    # ------------------------------------------------------------------

    def test_sequence_id_equals_time(self):
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._book(time=1700000000123))

        assert outbox[0]["sequence_id"] == 1700000000123

    def test_sequence_id_is_monotonically_increasing(self):
        syncer, outbox = self._make_syncer()
        t1, t2, t3 = 1000, 2000, 3000
        syncer.process_message(**self._book(time=t1))
        syncer.process_message(**self._book(time=t2))
        syncer.process_message(**self._book(time=t3))

        seqs = [e["sequence_id"] for e in outbox]
        assert seqs == sorted(seqs)
        assert seqs[0] < seqs[1] < seqs[2]

    def test_same_timestamp_yields_unique_sequence_ids(self):
        """If two WS messages arrive with the same time, sequence must still be unique."""
        syncer, outbox = self._make_syncer()
        syncer.process_message(**self._book(time=1700000000000))
        syncer.process_message(**self._book(time=1700000000000))

        seqs = [e["sequence_id"] for e in outbox]
        assert seqs[0] != seqs[1]
        assert seqs[1] > seqs[0]

    # ------------------------------------------------------------------
    # stream_session_id propagation
    # ------------------------------------------------------------------

    def test_stream_session_id_propagated_to_events(self):
        syncer, outbox = self._make_syncer("mysess:42")
        syncer.process_message(**self._book(time=1700000000000))

        assert outbox[0]["stream_session_id"] == "mysess:42"

    # ------------------------------------------------------------------
    # No DepthDiff or DepthGap events (Hyperliquid is full-snapshot only)
    # ------------------------------------------------------------------

    def test_no_depth_diff_events_emitted(self):
        syncer, outbox = self._make_syncer()
        for t in range(1000, 1010):
            syncer.process_message(**self._book(time=t * 1000))

        diff_events = [e for e in outbox if e["event"] == "DepthDiff"]
        assert len(diff_events) == 0

    def test_no_depth_gap_events_emitted(self):
        syncer, outbox = self._make_syncer()
        for t in range(1000, 1010):
            syncer.process_message(**self._book(time=t * 1000))

        gap_events = [e for e in outbox if e["event"] == "DepthGap"]
        assert len(gap_events) == 0
