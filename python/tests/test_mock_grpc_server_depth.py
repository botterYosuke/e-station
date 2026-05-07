"""MockGrpcServer S5 — gRPC depth bootstrap / continuity テスト.

S5-A: DepthSnapshot → DepthDiff continuity（同一 stream_session_id）
S5-B: stream_session_id 変化（attach reconnect 相当）
S5-C: gap 検出後の RequestDepthSnapshot 再送（1 プロトコル経路）

WS 版 MockIPCServer の S5 テストを gRPC (ScriptedMockGrpcServer) ベースで書き直したもの
（G3 完了後）。全テストは 1 秒以内に PASS する。
"""
from __future__ import annotations

import uuid

import pytest
from grpc import aio

from engine.proto import engine_pb2, engine_pb2_grpc
from tests.fixtures.mock_grpc_server import ScriptedMockGrpcServer

pytestmark = pytest.mark.timeout(1)

_VENUE = "TSE"
_TICKER = "1301"
_MARKET = "Regular"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _snapshot(*, ssid: str, seq: int) -> engine_pb2.Event:
    return engine_pb2.Event(
        depth_snapshot=engine_pb2.DepthSnapshotEvent(
            venue=_VENUE,
            ticker=_TICKER,
            market=_MARKET,
            stream_session_id=ssid,
            sequence_id=seq,
            bids=[engine_pb2.DepthLevel(price="3000", qty="100")],
            asks=[engine_pb2.DepthLevel(price="3001", qty="50")],
        )
    )


def _diff(*, ssid: str, seq: int, prev: int) -> engine_pb2.Event:
    return engine_pb2.Event(
        depth_diff=engine_pb2.DepthDiffEvent(
            venue=_VENUE,
            ticker=_TICKER,
            market=_MARKET,
            stream_session_id=ssid,
            sequence_id=seq,
            prev_sequence_id=prev,
            bids=[engine_pb2.DepthLevel(price="2999", qty="200")],
            asks=[],
        )
    )


def _gap(*, ssid: str) -> engine_pb2.Event:
    return engine_pb2.Event(
        depth_gap=engine_pb2.DepthGapEvent(
            venue=_VENUE,
            ticker=_TICKER,
            market=_MARKET,
            stream_session_id=ssid,
        )
    )


async def _do_hello(stream: aio.StreamStreamCall) -> engine_pb2.ReadyResponse:
    """HelloRequest を送り ReadyResponse を受け取る。ready を返す。"""
    await stream.write(
        engine_pb2.Command(
            hello=engine_pb2.HelloRequest(
                schema_major=3,
                schema_minor=0,
                client_version="test-depth-0.0.1",
                token="test-token",
                mode=engine_pb2.APP_MODE_REPLAY,
            )
        )
    )
    event = await stream.read()
    assert event.HasField("ready"), f"expected ready, got {event.WhichOneof('payload')}"
    return event.ready


# ---------------------------------------------------------------------------
# S5-A: DepthSnapshot → DepthDiff continuity（同一 stream_session_id）
# ---------------------------------------------------------------------------


class TestS5aDepthContinuity:
    """S5-A: 同一 stream_session_id で DepthSnapshot → DepthDiff が届くこと。"""

    async def test_snapshot_then_diff_same_ssid(self) -> None:
        ssid = str(uuid.uuid4())
        snapshot_event = _snapshot(ssid=ssid, seq=100)
        diff_event = _diff(ssid=ssid, seq=101, prev=100)

        async with ScriptedMockGrpcServer(
            events_after_hello=[snapshot_event, diff_event]
        ) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()

            await _do_hello(stream)

            # DepthSnapshot
            ev1 = await stream.read()
            assert ev1.HasField("depth_snapshot"), (
                f"expected depth_snapshot, got {ev1.WhichOneof('payload')}"
            )
            snap = ev1.depth_snapshot
            assert snap.stream_session_id == ssid
            assert snap.sequence_id == 100

            # DepthDiff
            ev2 = await stream.read()
            assert ev2.HasField("depth_diff"), (
                f"expected depth_diff, got {ev2.WhichOneof('payload')}"
            )
            diff = ev2.depth_diff
            assert diff.stream_session_id == ssid, "stream_session_id が一致すること"
            assert diff.sequence_id == 101
            assert diff.prev_sequence_id == 100, "prev_sequence_id は snapshot の seq_id と一致"

            await stream.done_writing()
            await channel.close()

    async def test_diff_seq_is_consecutive(self) -> None:
        """DepthDiff.sequence_id == DepthSnapshot.sequence_id + 1 であること。"""
        ssid = str(uuid.uuid4())

        async with ScriptedMockGrpcServer(
            events_after_hello=[
                _snapshot(ssid=ssid, seq=200),
                _diff(ssid=ssid, seq=201, prev=200),
            ]
        ) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()
            await _do_hello(stream)

            snap = (await stream.read()).depth_snapshot
            diff = (await stream.read()).depth_diff

            assert diff.sequence_id == snap.sequence_id + 1
            assert diff.prev_sequence_id == snap.sequence_id

            await stream.done_writing()
            await channel.close()


# ---------------------------------------------------------------------------
# S5-B: stream_session_id 変化（attach reconnect 相当）
# ---------------------------------------------------------------------------


class TestS5bStreamSessionIdChange:
    """S5-B: DepthSnapshot と DepthDiff の stream_session_id が異なる場合を検出できること。"""

    async def test_snapshot_diff_ssid_mismatch_is_detectable(self) -> None:
        """ssid 不一致のスナップショット/差分をクライアントが検出できること。

        MockGrpcServer は両方を送るだけ。不一致検出はクライアント責務であることを
        proto レベルで確認する。
        """
        ssid_old = str(uuid.uuid4())
        ssid_new = str(uuid.uuid4())

        async with ScriptedMockGrpcServer(
            events_after_hello=[
                _snapshot(ssid=ssid_old, seq=100),
                _diff(ssid=ssid_new, seq=101, prev=100),  # ssid が変わった
            ]
        ) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()
            await _do_hello(stream)

            snap = (await stream.read()).depth_snapshot
            diff = (await stream.read()).depth_diff

            # ssid が異なることをクライアントが検出できる
            assert snap.stream_session_id != diff.stream_session_id, (
                "ssid の不一致を proto レベルで検出できること"
            )
            assert snap.stream_session_id == ssid_old
            assert diff.stream_session_id == ssid_new

            await stream.done_writing()
            await channel.close()

    async def test_depth_gap_event_carries_ssid(self) -> None:
        """DepthGapEvent が stream_session_id を持つこと（gap 検出の識別に使う）。"""
        ssid = str(uuid.uuid4())

        async with ScriptedMockGrpcServer(
            events_after_hello=[_gap(ssid=ssid)]
        ) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()
            await _do_hello(stream)

            ev = await stream.read()
            assert ev.HasField("depth_gap"), (
                f"expected depth_gap, got {ev.WhichOneof('payload')}"
            )
            assert ev.depth_gap.stream_session_id == ssid

            await stream.done_writing()
            await channel.close()


# ---------------------------------------------------------------------------
# S5-C: gap 検出後の RequestDepthSnapshot 再送
# ---------------------------------------------------------------------------


class TestS5cGapRequestDepthSnapshot:
    """S5-C: DepthGap 受信後に RequestDepthSnapshot を送ると新 DepthSnapshot が返ること。"""

    async def test_depth_gap_then_request_snapshot_returns_new_snapshot(self) -> None:
        ssid_old = str(uuid.uuid4())
        ssid_new = str(uuid.uuid4())

        gap_event = _gap(ssid=ssid_old)
        new_snapshot = _snapshot(ssid=ssid_new, seq=200)

        async with ScriptedMockGrpcServer(
            events_after_hello=[gap_event],
            on_request_depth_snapshot=[new_snapshot],
        ) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()
            await _do_hello(stream)

            # DepthGap を受信
            ev = await stream.read()
            assert ev.HasField("depth_gap"), f"expected depth_gap, got {ev.WhichOneof('payload')}"
            assert ev.depth_gap.stream_session_id == ssid_old

            # RequestDepthSnapshot を送る（gap 検出後の再要求）
            await stream.write(
                engine_pb2.Command(
                    request_depth_snapshot=engine_pb2.RequestDepthSnapshotRequest(
                        venue=_VENUE,
                        ticker=_TICKER,
                        market=_MARKET,
                    )
                )
            )

            # 新しい DepthSnapshot が返ってくること
            ev2 = await stream.read()
            assert ev2.HasField("depth_snapshot"), (
                f"expected depth_snapshot after RequestDepthSnapshot, got {ev2.WhichOneof('payload')}"
            )
            snap = ev2.depth_snapshot
            assert snap.stream_session_id == ssid_new, "新 ssid の snapshot が返ること"
            assert snap.sequence_id == 200

            await stream.done_writing()
            await channel.close()

    async def test_request_depth_snapshot_without_prior_gap_also_works(self) -> None:
        """gap なしでも RequestDepthSnapshot を送れば新 snapshot が返ること。"""
        ssid = str(uuid.uuid4())
        initial_snapshot = _snapshot(ssid=ssid, seq=100)
        refresh_snapshot = _snapshot(ssid=str(uuid.uuid4()), seq=300)

        async with ScriptedMockGrpcServer(
            events_after_hello=[initial_snapshot],
            on_request_depth_snapshot=[refresh_snapshot],
        ) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()
            await _do_hello(stream)

            # 初期 snapshot
            ev1 = await stream.read()
            assert ev1.depth_snapshot.sequence_id == 100

            # 再要求
            await stream.write(
                engine_pb2.Command(
                    request_depth_snapshot=engine_pb2.RequestDepthSnapshotRequest(
                        venue=_VENUE,
                        ticker=_TICKER,
                        market=_MARKET,
                    )
                )
            )

            # 新 snapshot
            ev2 = await stream.read()
            assert ev2.HasField("depth_snapshot")
            assert ev2.depth_snapshot.sequence_id == 300

            await stream.done_writing()
            await channel.close()
