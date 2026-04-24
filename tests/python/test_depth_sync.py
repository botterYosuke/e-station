"""TDD Red: Depth gap detection and resync protocol tests."""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flowsurface_data.exchanges.binance import BinanceDepthSyncer


class TestDepthSyncerGapDetection:
    """Tests for depth gap detection and resync in BinanceDepthSyncer."""

    def _make_diff(
        self,
        first_id: int,
        final_id: int,
        prev_final_id: int,
        bids: list | None = None,
        asks: list | None = None,
    ) -> dict:
        return {
            "type": "perp_diff",
            "U": first_id,
            "u": final_id,
            "pu": prev_final_id,
            "b": bids or [["68000.0", "1.0"]],
            "a": asks or [["68001.0", "0.5"]],
            "T": 1700000000000,
        }

    def _make_snapshot(self, last_update_id: int, bids=None, asks=None) -> dict:
        return {
            "last_update_id": last_update_id,
            "bids": bids or [{"price": "68000.0", "qty": "1.0"}],
            "asks": asks or [{"price": "68001.0", "qty": "0.5"}],
        }

    @pytest.mark.asyncio
    async def test_normal_sequence_no_gap(self):
        """Contiguous diffs should apply cleanly and emit DepthDiff events."""
        outbox: list[dict] = []
        snapshot_fetcher = AsyncMock(
            return_value=self._make_snapshot(last_update_id=100)
        )

        syncer = BinanceDepthSyncer(
            venue="binance",
            ticker="BTCUSDT",
            market="linear_perp",
            stream_session_id="sess:1",
            snapshot_fetcher=snapshot_fetcher,
            outbox=outbox,
        )
        await syncer.initialize()

        # First diff after snapshot (U<=101<=u, pu==100 for perp)
        await syncer.apply_diff(self._make_diff(first_id=100, final_id=101, prev_final_id=99))
        await syncer.apply_diff(self._make_diff(first_id=102, final_id=103, prev_final_id=101))

        depth_events = [e for e in outbox if e.get("event") == "DepthDiff"]
        assert len(depth_events) == 2
        assert depth_events[0]["sequence_id"] == 101
        assert depth_events[1]["sequence_id"] == 103

    @pytest.mark.asyncio
    async def test_gap_triggers_depth_gap_event(self):
        """A skipped sequence_id should emit DepthGap and trigger resync."""
        outbox: list[dict] = []
        snapshot_fetcher = AsyncMock(
            return_value=self._make_snapshot(last_update_id=100)
        )

        syncer = BinanceDepthSyncer(
            venue="binance",
            ticker="BTCUSDT",
            market="linear_perp",
            stream_session_id="sess:1",
            snapshot_fetcher=snapshot_fetcher,
            outbox=outbox,
        )
        await syncer.initialize()

        # Valid first diff
        await syncer.apply_diff(self._make_diff(first_id=100, final_id=101, prev_final_id=99))
        # Gap: pu=105 != prev applied=101
        await syncer.apply_diff(self._make_diff(first_id=106, final_id=107, prev_final_id=105))

        gap_events = [e for e in outbox if e.get("event") == "DepthGap"]
        assert len(gap_events) >= 1
        assert gap_events[0]["venue"] == "binance"
        assert gap_events[0]["ticker"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_resync_fetches_new_snapshot(self):
        """After a gap, snapshot_fetcher should be called again to resync."""
        outbox: list[dict] = []
        snapshot_fetcher = AsyncMock(
            side_effect=[
                self._make_snapshot(last_update_id=100),
                self._make_snapshot(last_update_id=200),
            ]
        )

        syncer = BinanceDepthSyncer(
            venue="binance",
            ticker="BTCUSDT",
            market="linear_perp",
            stream_session_id="sess:1",
            snapshot_fetcher=snapshot_fetcher,
            outbox=outbox,
        )
        await syncer.initialize()
        assert snapshot_fetcher.call_count == 1

        # Introduce gap
        await syncer.apply_diff(self._make_diff(first_id=100, final_id=101, prev_final_id=99))
        await syncer.apply_diff(self._make_diff(first_id=300, final_id=301, prev_final_id=299))

        # Resync should have triggered a second snapshot fetch
        await syncer.resync()
        assert snapshot_fetcher.call_count == 2

    @pytest.mark.asyncio
    async def test_diffs_before_snapshot_are_buffered_and_replayed(self):
        """Diffs arriving before snapshot is applied should be buffered, not dropped."""
        outbox: list[dict] = []

        # Snapshot returns after a small delay (simulated with immediate value)
        snapshot_fetcher = AsyncMock(
            return_value=self._make_snapshot(last_update_id=50)
        )

        syncer = BinanceDepthSyncer(
            venue="binance",
            ticker="BTCUSDT",
            market="linear_perp",
            stream_session_id="sess:1",
            snapshot_fetcher=snapshot_fetcher,
            outbox=outbox,
        )

        # Queue diffs BEFORE calling initialize
        syncer.queue_diff(self._make_diff(first_id=48, final_id=50, prev_final_id=47))
        syncer.queue_diff(self._make_diff(first_id=51, final_id=52, prev_final_id=50))

        await syncer.initialize()

        # After snapshot(lastUpdateId=50), the diff with final_id=50 is stale (skip),
        # but final_id=52 should be replayed
        depth_events = [e for e in outbox if e.get("event") == "DepthDiff"]
        assert any(e["sequence_id"] == 52 for e in depth_events)

    @pytest.mark.asyncio
    async def test_snapshot_emitted_as_depth_snapshot_event(self):
        """A DepthSnapshot IPC event is emitted after snapshot is applied."""
        outbox: list[dict] = []
        snapshot_fetcher = AsyncMock(
            return_value=self._make_snapshot(last_update_id=100)
        )

        syncer = BinanceDepthSyncer(
            venue="binance",
            ticker="BTCUSDT",
            market="linear_perp",
            stream_session_id="sess:1",
            snapshot_fetcher=snapshot_fetcher,
            outbox=outbox,
        )
        await syncer.initialize()

        snap_events = [e for e in outbox if e.get("event") == "DepthSnapshot"]
        assert len(snap_events) == 1
        assert snap_events[0]["sequence_id"] == 100
        assert snap_events[0]["venue"] == "binance"
        assert snap_events[0]["ticker"] == "BTCUSDT"
        assert snap_events[0]["bids"] == [{"price": "68000.0", "qty": "1.0"}]

    @pytest.mark.asyncio
    async def test_stream_session_id_on_events(self):
        """All emitted events carry the stream_session_id."""
        outbox: list[dict] = []
        snapshot_fetcher = AsyncMock(
            return_value=self._make_snapshot(last_update_id=10)
        )

        syncer = BinanceDepthSyncer(
            venue="binance",
            ticker="BTCUSDT",
            market="linear_perp",
            stream_session_id="mysess:42",
            snapshot_fetcher=snapshot_fetcher,
            outbox=outbox,
        )
        await syncer.initialize()
        await syncer.apply_diff(self._make_diff(first_id=10, final_id=11, prev_final_id=9))

        for event in outbox:
            assert event.get("stream_session_id") == "mysess:42"
