"""H-SF2: `_Broadcaster` queue-full handling.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import asyncio
import logging


def test_broadcaster_append_continues_on_queue_full():
    """H-SF2: 1クライアントのキューが満杯でも他クライアントにイベントが届くこと。"""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_broadcaster_test())
    finally:
        loop.close()


async def _run_broadcaster_test():
    from engine.server import _Broadcaster

    broadcaster = _Broadcaster()

    # 2つのモックWebSocket接続を作る
    class _FakeWs:
        def __init__(self, name):
            self.name = name

        def __hash__(self):
            return hash(self.name)

        def __eq__(self, other):
            return isinstance(other, _FakeWs) and self.name == other.name

    ws1 = _FakeWs("client1")
    ws2 = _FakeWs("client2")

    # ws1 のキューは maxsize=1 で満杯にする
    q1 = asyncio.Queue(maxsize=1)
    q1.put_nowait({"event": "dummy"})  # 満杯にする
    broadcaster._queues[ws1] = q1

    # ws2 のキューは空
    q2 = asyncio.Queue()
    broadcaster._queues[ws2] = q2

    # append を呼ぶ（ws1 は満杯なのでスキップ、ws2 には届く）
    broadcaster.append({"event": "TestEvent", "data": "hello"})

    # ws2 にはイベントが届いているはず
    assert not q2.empty(), "ws2 should receive the event even if ws1's queue is full"
    event = q2.get_nowait()
    assert event.get("event") == "TestEvent"

    # ws1 のキューはまだ満杯のまま（溢れたイベントは破棄）
    assert q1.qsize() == 1  # 元の dummy だけ


def test_broadcaster_append_logs_on_queue_full(caplog):
    """H-SF2: キューが満杯の場合 WARNING ログが出ること。"""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_broadcaster_log_test(caplog))
    finally:
        loop.close()


async def _run_broadcaster_log_test(caplog):
    from engine.server import _Broadcaster

    broadcaster = _Broadcaster()

    class _FakeWs:
        def __init__(self, name):
            self.name = name
        def __hash__(self):
            return hash(self.name)
        def __eq__(self, other):
            return isinstance(other, _FakeWs) and self.name == other.name

    ws1 = _FakeWs("client1")
    q1 = asyncio.Queue(maxsize=1)
    q1.put_nowait({"event": "dummy"})  # 満杯
    broadcaster._queues[ws1] = q1

    with caplog.at_level(logging.WARNING, logger="engine.server"):
        broadcaster.append({"event": "TestEvent"})

    msgs = [r.message for r in caplog.records]
    assert any("full" in m.lower() or "drop" in m.lower() or "outbox" in m.lower() for m in msgs), \
        f"expected QueueFull warning; got: {msgs}"
