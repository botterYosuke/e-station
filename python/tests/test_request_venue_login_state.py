"""H-TA1: `RequestVenueLogin` during CONNECTING returns VenueLoginStarted.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import asyncio


def test_request_venue_login_connecting_returns_venue_login_started():
    """H-TA1: CONNECTING 中に RequestVenueLogin を送ると EngineBusy でなく
    VenueLoginStarted が返ること。
    """
    from engine.server import DataEngineServer, LiveState

    srv = DataEngineServer.__new__(DataEngineServer)
    srv._mode = "live"
    srv._live_state = LiveState.CONNECTING

    # _outbox をモックに置き換える
    emitted: list[dict] = []

    class _FakeOutbox:
        def append(self, item):
            emitted.append(item)

        def count(self):
            return 1

    srv._outbox = _FakeOutbox()
    srv._tachibana_login_inflight = asyncio.Lock()

    async def _run():
        msg = {
            "op": "RequestVenueLogin",
            "request_id": "test-req-1",
            "venue": "tachibana",
        }
        await srv._do_request_venue_login(msg)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()

    # EngineBusy でなく VenueLoginStarted が返ること
    events = [e.get("event") for e in emitted]
    assert "EngineBusy" not in events, f"EngineBusy should not be returned for CONNECTING; got: {emitted}"
    assert "VenueLoginStarted" in events, f"VenueLoginStarted should be returned; got: {emitted}"
