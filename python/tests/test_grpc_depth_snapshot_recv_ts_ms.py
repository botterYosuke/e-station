"""TDD: Tachibana stream_depth が生成する DepthSnapshot が gRPC proto に変換されること。

Bug: tachibana.py の _cb_depth / _depth_polling_fallback は outbox に
     "recv_ts_ms" を含む DepthSnapshot dict を追加していた。
     proto の DepthSnapshotEvent に recv_ts_ms フィールドは存在しないため、
     server_grpc.py の _dict_to_proto_event が ParseDict(ignore_unknown_fields=False)
     で変換に失敗し、イベントが None になって Rust 側に配信されなかった (サイレント DROP)。
     その結果 Ladder が空白のままになっていた。

Fix: outbox に追加する前に recv_ts_ms キーを除去する (tachibana.py)。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
import websockets
import websockets.server  # type: ignore[import-untyped]

import engine.exchanges.tachibana_ws as _ws_mod
from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl
from engine.server_grpc import _dict_to_proto_event


# ---------------------------------------------------------------------------
# ヘルパー（test_tachibana_ws_fd_depth_recv.py と同様の構造）
# ---------------------------------------------------------------------------


def _fake_session(ws_port: int) -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws=f"ws://127.0.0.1:{ws_port}/event/",
        zyoutoeki_kazei_c="",
    )


def _make_worker(tmp_path: Path) -> TachibanaWorker:
    return TachibanaWorker(cache_dir=tmp_path, is_demo=True)


def _build_fd_frame(
    row: str = "1",
    dpp: str = "2500",
    dv: str = "1000",
    bids: list[tuple[str, str]] | None = None,
    asks: list[tuple[str, str]] | None = None,
) -> bytes:
    """FD フレーム（^B区切り）を生成する。"""
    bids = bids or [("2499", "100"), ("2498", "200")]
    asks = asks or [("2501", "150"), ("2502", "250")]
    parts: list[str] = [
        f"p_cmd\x02FD",
        f"p_{row}_DPP\x02{dpp}",
        f"p_{row}_DV\x02{dv}",
    ]
    for i, (price, vol) in enumerate(bids, start=1):
        parts.append(f"p_{row}_GBP{i}\x02{price}")
        parts.append(f"p_{row}_GBV{i}\x02{vol}")
    for i, (price, vol) in enumerate(asks, start=1):
        parts.append(f"p_{row}_GAP{i}\x02{price}")
        parts.append(f"p_{row}_GAV{i}\x02{vol}")
    parts.append(f"p_date\x022024.01.01-09:30:00.000")
    text = "\x01" + "\x01".join(parts)
    return text.encode("shift_jis")


# ---------------------------------------------------------------------------
# RED → GREEN テスト: stream_depth の outbox が proto 変換可能であること
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_snapshot_from_stream_depth_is_proto_convertible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stream_depth が outbox に積む DepthSnapshot が gRPC proto に変換できること。

    Bug 再現: 修正前は recv_ts_ms が outbox dict に含まれており、
    _dict_to_proto_event が ParseError で None を返していた（サイレント DROP）。
    修正後は recv_ts_ms が含まれないため、正常に変換されて Rust 側に配信される。
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 3.0)
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 5.0)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_build_fd_frame())
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.sleep(0.3)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)
        outbox: list[dict] = []

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-1", outbox, stop),
                timeout=5.0,
            )

    depth_events = [e for e in outbox if e.get("event") == "DepthSnapshot"]
    assert depth_events, f"DepthSnapshot が outbox にない: {outbox}"

    for event in depth_events:
        proto_event = _dict_to_proto_event(event)
        assert proto_event is not None, (
            f"DepthSnapshot の proto 変換が None になった — "
            f"outbox dict に proto 未定義フィールドが含まれている可能性がある。"
            f"dict のキー: {sorted(event.keys())}"
        )
        assert proto_event.HasField("depth_snapshot"), (
            "proto_event が depth_snapshot フィールドを持っていない"
        )
        snap = proto_event.depth_snapshot
        assert snap.venue == "tachibana"
        assert snap.ticker == "7203"
        assert snap.market == "stock"
        assert len(snap.bids) > 0 or len(snap.asks) > 0, (
            "bids と asks が両方空"
        )


@pytest.mark.asyncio
async def test_depth_snapshot_outbox_dict_has_no_recv_ts_ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stream_depth が生成する DepthSnapshot dict に recv_ts_ms が含まれないこと。

    recv_ts_ms は proto DepthSnapshotEvent に存在しないため、outbox dict に含めると
    ParseDict(ignore_unknown_fields=False) で ParseError が発生してイベントが DROP する。
    修正後は recv_ts_ms を含まないことを直接検証する。
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 3.0)
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 5.0)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_build_fd_frame())
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.sleep(0.3)
        await ws.close()

    async with websockets.serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        worker = _make_worker(tmp_path)
        worker._session = _fake_session(port)
        outbox: list[dict] = []

        with patch("engine.exchanges.tachibana_ws.is_market_open", return_value=True):
            await asyncio.wait_for(
                worker.stream_depth("7203", "stock", "session-2", outbox, stop),
                timeout=5.0,
            )

    depth_events = [e for e in outbox if e.get("event") == "DepthSnapshot"]
    assert depth_events, f"DepthSnapshot が outbox にない: {outbox}"

    for event in depth_events:
        assert "recv_ts_ms" not in event, (
            f"outbox の DepthSnapshot dict に recv_ts_ms が含まれている: {sorted(event.keys())}.\n"
            "recv_ts_ms は proto DepthSnapshotEvent に存在せず、gRPC 変換時に DROP を引き起こす。"
        )
