"""TDD: stream_depth が FD フレームの bid/ask を受信して DepthSnapshot を emit する。

サンプル e_api_websocket_receive_tel.py の websockets.serve パターンを踏襲:
  - モックではなく実際の websockets.serve サーバーを起動
  - FD フレーム形式: key^Bvalue^Akey^Bvalue^A... (Shift-JIS エンコード)
  - p_N_GBP1..10 / p_N_GAP1..10 キーを持つ完全な気配フレームを送信
  - DepthSnapshot イベントが outbox に積まれ、bids/asks が正しく展開されることを検証
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from engine.exchanges.tachibana_codec import parse_event_frame

import pytest
import websockets
import websockets.server  # type: ignore[import-untyped]

import engine.exchanges.tachibana_ws as _ws_mod
from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# ヘルパー
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
    p_date: str = "2024.01.01-09:30:00.000",
) -> bytes:
    """サンプルの電文形式を忠実に再現する FD フレームを生成する。

    電文形式（api_event_if.xlsx §3 通知データ仕様）:
        項目^B値^A項目^B値^A...
    区切り子:
        ^A (\\x01) = 項目区切り
        ^B (\\x02) = 項目名と値の区切り
    """
    bids = bids or [("2499", "100"), ("2498", "200"), ("2497", "300")]
    asks = asks or [("2501", "150"), ("2502", "250"), ("2503", "350")]

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
    parts.append(f"p_date\x02{p_date}")

    # サンプルと同様に各項目の前に ^A を付けて連結する
    text = "\x01" + "\x01".join(parts)
    return text.encode("shift_jis")


def _build_kp_frame() -> bytes:
    """KP (keep-alive) フレーム。サーバーは 5 秒間送信がないと KP を送る。"""
    return "\x01p_cmd\x02KP".encode("shift_jis")


# ---------------------------------------------------------------------------
# HAPPY-1: FD フレームに気配が含まれるとき DepthSnapshot が emit される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_depth_emits_depth_snapshot_from_fd_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FD フレームに GBP/GAP キーが含まれるとき、DepthSnapshot が outbox に積まれる。

    サンプル e_api_websocket_receive_tel.py の websockets.serve パターンを使い、
    モックなしで実際の WebSocket 通信経路を検証する。
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 3.0)
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 5.0)

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        # 1 フレーム目: DV のみ（初期化用、トレードなし）
        init_frame = _build_fd_frame(dv="0", bids=[], asks=[])
        await ws.send(init_frame)
        await asyncio.sleep(0.05)

        # 2 フレーム目: 完全な気配付き FD フレームを送信
        await ws.send(_build_fd_frame())
        await asyncio.sleep(0.1)

        # テスト終了を通知して WebSocket を閉じる
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
    assert depth_events, (
        "DepthSnapshot イベントが outbox に積まれていない。"
        f"outbox の内容: {outbox}"
    )
    snap = depth_events[0]
    assert snap["venue"] == "tachibana"
    assert snap["ticker"] == "7203"
    assert snap["market"] == "stock"
    assert snap["bids"], "bids が空"
    assert snap["asks"], "asks が空"


# ---------------------------------------------------------------------------
# HAPPY-2: bid/ask の値が FD フレームの内容と一致する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_depth_depth_snapshot_bid_ask_values_match_fd_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DepthSnapshot の bids/asks が FD フレームで送信した値と一致すること。

    立花証券サンプルの電文形式（^A^B 区切り）を使ったリアルな WebSocket サーバーで検証。
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 3.0)
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 5.0)

    expected_bids = [("3000", "100"), ("2999", "200"), ("2998", "300")]
    expected_asks = [("3001", "150"), ("3002", "250"), ("3003", "350")]

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_build_fd_frame(
            dpp="3000",
            dv="5000",
            bids=expected_bids,
            asks=expected_asks,
        ))
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
    assert depth_events, f"DepthSnapshot なし。outbox: {outbox}"
    snap = depth_events[0]

    # bids: [(price, vol), ...] — FD フレームに送った値と一致すること
    received_bids = snap["bids"]
    assert len(received_bids) == len(expected_bids), (
        f"bids 件数不一致: expected={len(expected_bids)} actual={len(received_bids)}"
    )
    for i, (exp_price, exp_vol) in enumerate(expected_bids):
        got_price = received_bids[i]["price"]
        got_vol = received_bids[i]["qty"]
        assert got_price == exp_price, f"bids[{i}].price: expected={exp_price} got={got_price}"
        assert got_vol == exp_vol, f"bids[{i}].vol: expected={exp_vol} got={got_vol}"

    # asks
    received_asks = snap["asks"]
    assert len(received_asks) == len(expected_asks), (
        f"asks 件数不一致: expected={len(expected_asks)} actual={len(received_asks)}"
    )
    for i, (exp_price, exp_vol) in enumerate(expected_asks):
        got_price = received_asks[i]["price"]
        got_vol = received_asks[i]["qty"]
        assert got_price == exp_price, f"asks[{i}].price: expected={exp_price} got={got_price}"
        assert got_vol == exp_vol, f"asks[{i}].vol: expected={exp_vol} got={got_vol}"


# ---------------------------------------------------------------------------
# HAPPY-3: 10 レベルフル気配 — サンプルと同じ 10 段気配が正しく展開される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_depth_10_level_depth_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """立花証券 API の最大 10 段気配 (GBP1..GBP10 / GAP1..GAP10) が全て展開される。

    サンプルコメント:
        p_N_GBP1..GBP10: 買い気配値 1〜10 位
        p_N_GBV1..GBV10: 買い気配数量 1〜10 位
        p_N_GAP1..GAP10: 売り気配値 1〜10 位
        p_N_GAV1..GAV10: 売り気配数量 1〜10 位
    """
    monkeypatch.setattr(_ws_mod, "_DEPTH_SAFETY_TIMEOUT_S", 3.0)
    monkeypatch.setattr(_ws_mod, "_DEAD_FRAME_TIMEOUT_S", 5.0)

    base_price = 2500
    bids_10 = [(str(base_price - i), str((i + 1) * 100)) for i in range(10)]
    asks_10 = [(str(base_price + 1 + i), str((i + 1) * 100)) for i in range(10)]

    stop = asyncio.Event()

    async def _handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await ws.send(_build_fd_frame(bids=bids_10, asks=asks_10))
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
                worker.stream_depth("7203", "stock", "session-3", outbox, stop),
                timeout=5.0,
            )

    depth_events = [e for e in outbox if e.get("event") == "DepthSnapshot"]
    assert depth_events, f"DepthSnapshot なし。outbox: {outbox}"
    snap = depth_events[0]

    assert len(snap["bids"]) == 10, (
        f"10 段気配を期待したが bids={len(snap['bids'])} 段"
    )
    assert len(snap["asks"]) == 10, (
        f"10 段気配を期待したが asks={len(snap['asks'])} 段"
    )
    # 最良気配（1 位）の値を確認
    assert snap["bids"][0]["price"] == str(base_price)
    assert snap["asks"][0]["price"] == str(base_price + 1)


# ---------------------------------------------------------------------------
# M3: _build_kp_frame() のプレフィックス検証
# ---------------------------------------------------------------------------


def test_build_kp_frame_is_parseable_and_has_p_cmd_kp() -> None:
    """`_build_kp_frame()` が返すバイト列を parse_event_frame に通すと p_cmd=KP が得られる。

    M3: `_build_kp_frame()` の先頭に \\x01 プレフィックスが必要。
    parse_event_frame は先頭 \\x01 を区切りとして使うため、プレフィックスなしだと
    先頭フィールドが読み飛ばされる。
    """
    kp_bytes = _build_kp_frame()
    text = kp_bytes.decode("shift_jis")
    pairs = parse_event_frame(text)
    fields = dict(pairs)
    assert fields.get("p_cmd") == "KP", (
        f"parse_event_frame が p_cmd=KP を返さなかった。fields={fields}\n"
        "原因: _build_kp_frame() の先頭に \\x01 プレフィックスが必要。"
    )
