"""Tests for IPC schema models."""

import pytest
from flowsurface_data.schemas import (
    SCHEMA_MAJOR,
    SCHEMA_MINOR,
    DepthDiffMsg,
    DepthGap,
    DepthSnapshotMsg,
    Hello,
    KlineMsg,
    KlineUpdate,
    Ready,
    Subscribe,
    TradeMsg,
    Trades,
)
import uuid


def test_hello_roundtrip():
    h = Hello(schema_major=0, schema_minor=1, client_version="0.8.7", token="abc123")
    assert h.op == "Hello"
    assert h.token == "abc123"


def test_hello_rejects_extra_fields():
    h = Hello.model_validate(
        {"op": "Hello", "schema_major": 0, "schema_minor": 1, "client_version": "x", "token": "t", "unknown": "y"}
    )
    assert not hasattr(h, "unknown")


def test_ready_fields():
    sid = uuid.uuid4()
    r = Ready(
        schema_major=SCHEMA_MAJOR,
        schema_minor=SCHEMA_MINOR,
        engine_version="0.1.0",
        engine_session_id=sid,
        capabilities={"supported_venues": ["binance"]},
    )
    assert r.event == "Ready"
    assert r.engine_session_id == sid


def test_trade_msg_side():
    t = TradeMsg(price="68000.5", qty="0.012", side="buy", ts_ms=1700000000000)
    assert t.side == "buy"
    assert t.is_liquidation is False


def test_trades_batch():
    trades_msg = Trades(
        venue="binance",
        ticker="BTCUSDT",
        stream_session_id="abc:1",
        trades=[
            TradeMsg(price="68000.0", qty="0.1", side="buy", ts_ms=1000),
            TradeMsg(price="68001.0", qty="0.2", side="sell", ts_ms=1001),
        ],
    )
    assert len(trades_msg.trades) == 2


def test_depth_diff_sequence_fields():
    diff = DepthDiffMsg(
        venue="binance",
        ticker="BTCUSDT",
        stream_session_id="abc:1",
        sequence_id=100,
        prev_sequence_id=99,
        bids=[{"price": "67999.0", "qty": "1.5"}],
        asks=[{"price": "68000.0", "qty": "0.5"}],
    )
    assert diff.sequence_id == 100
    assert diff.prev_sequence_id == 99


def test_depth_snapshot_optional_checksum():
    snap = DepthSnapshotMsg(
        venue="binance",
        ticker="BTCUSDT",
        stream_session_id="abc:1",
        sequence_id=50,
        bids=[],
        asks=[],
    )
    assert snap.checksum is None


def test_depth_gap_fields():
    gap = DepthGap(venue="binance", ticker="BTCUSDT", stream_session_id="abc:1")
    assert gap.event == "DepthGap"


def test_kline_update():
    ku = KlineUpdate(
        venue="binance",
        ticker="BTCUSDT",
        timeframe="1m",
        kline=KlineMsg(
            open_time_ms=1700000000000,
            open="67000.0",
            high="68000.0",
            low="66500.0",
            close="67800.0",
            volume="100.5",
            is_closed=False,
        ),
    )
    assert ku.kline.is_closed is False


def test_subscribe_op():
    s = Subscribe(venue="binance", ticker="BTCUSDT", stream="trade")
    assert s.op == "Subscribe"
