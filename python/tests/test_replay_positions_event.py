"""Issue 5: replay PositionsUpdated push 用 wire builder のユニットテスト。

`build_replay_positions_event(portfolio_state, ts_ms)` は
StepBackward 復元時 (server.py) と fill 時 (engine_runner.py) の双方から呼ばれる
pure helper で、PositionRecordWire 互換の dict を返す。
"""

from __future__ import annotations

import pytest


def test_build_replay_positions_event_with_position():
    from engine.server import build_replay_positions_event

    portfolio_state = {
        "cash": "1000000",
        "positions": {
            "7203.TSE": {"qty": "100", "cost": "2500"},
        },
        "last_prices": {"7203.TSE": "2600"},
    }

    ev = build_replay_positions_event(portfolio_state, ts_ms=1746000000000)

    assert ev["event"] == "PositionsUpdated"
    assert ev["request_id"] == ""
    assert ev["venue"] == "replay"
    assert ev["ts_ms"] == 1746000000000

    assert len(ev["positions"]) == 1
    p = ev["positions"][0]
    assert p["instrument_id"] == "7203.TSE"
    assert p["qty"] == "100"
    # market_value = qty(100) * last_price(2600) = 260000
    assert p["market_value"] == "260000"
    # PositionType enum (Rust 側) は cash | margin_credit | margin_general のみ
    assert p["position_type"] == "cash"
    assert p["tategyoku_id"] is None
    assert p["venue"] == "replay"


def test_build_replay_positions_event_empty():
    from engine.server import build_replay_positions_event

    ev = build_replay_positions_event(
        {"cash": "0", "positions": {}, "last_prices": {}},
        ts_ms=42,
    )

    assert ev["event"] == "PositionsUpdated"
    assert ev["positions"] == []
    assert ev["ts_ms"] == 42


def test_build_replay_positions_event_missing_last_price():
    """last_price が無いとき market_value は空文字（schema は "" 不明を許容）。"""
    from engine.server import build_replay_positions_event

    portfolio_state = {
        "cash": "0",
        "positions": {"9999.TSE": {"qty": "50", "cost": "1000"}},
        "last_prices": {},
    }

    ev = build_replay_positions_event(portfolio_state, ts_ms=1)

    assert len(ev["positions"]) == 1
    assert ev["positions"][0]["market_value"] == ""


def test_build_replay_positions_event_multiple_positions_sorted():
    """複数 position 時の決定論性を保証（dict 順序依存にしないため instrument_id ソート）。"""
    from engine.server import build_replay_positions_event

    portfolio_state = {
        "cash": "0",
        "positions": {
            "9999.TSE": {"qty": "10", "cost": "100"},
            "7203.TSE": {"qty": "20", "cost": "200"},
        },
        "last_prices": {"9999.TSE": "150", "7203.TSE": "250"},
    }

    ev = build_replay_positions_event(portfolio_state, ts_ms=1)

    iids = [p["instrument_id"] for p in ev["positions"]]
    assert iids == sorted(iids), f"positions must be sorted by instrument_id: {iids}"


def test_build_replay_positions_event_handles_missing_keys():
    """portfolio_state が空 dict の場合でも例外を投げず空イベントを返す。"""
    from engine.server import build_replay_positions_event

    ev = build_replay_positions_event({}, ts_ms=0)

    assert ev["event"] == "PositionsUpdated"
    assert ev["positions"] == []


# ── StepBackward 統合: PositionsUpdated emit ──────────────────────────────────


def _make_server(tmp_path):
    """test_replay_streaming_order_list.py と同じパターンで test server を作る。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    from engine.server import DataEngineServer

    with patch("engine.server.BinanceWorker", return_value=MagicMock()), patch.object(
        DataEngineServer, "_startup_tachibana", AsyncMock(return_value=None)
    ):
        server = DataEngineServer(
            port=19999,
            token="test-token",
            cache_dir=tmp_path,
        )
    return server


@pytest.mark.asyncio
async def test_stepbackward_emits_positions_updated(tmp_path):
    """StepBackward 後に PositionsUpdated が ts_ms 付きで送出されること。"""
    from engine.server import ReplaySnapshot, ReplayState

    server = _make_server(tmp_path)
    server._replay_state = ReplayState.PAUSED

    # snapshot 2 個（StepBackward は最低 2 個必要）。末尾が「戻る先」。
    snap_prev = ReplaySnapshot(
        step_index=0,
        portfolio={"event": "ReplayBuyingPower", "ts_event_ms": 1000,
                   "cash": "1000000", "buying_power": "1000000", "equity": "1260000",
                   "strategy_id": "s"},
        open_orders=[],
        strategy_state=None,
        ui_events=[],
        portfolio_state={
            "cash": "1000000",
            "positions": {"7203.TSE": {"qty": "100", "cost": "2500"}},
            "last_prices": {"7203.TSE": "2600"},
        },
    )
    snap_curr = ReplaySnapshot(
        step_index=1,
        portfolio={"event": "ReplayBuyingPower", "ts_event_ms": 2000,
                   "cash": "0", "buying_power": "0", "equity": "0", "strategy_id": "s"},
        open_orders=[],
        strategy_state=None,
        ui_events=[],
        portfolio_state={"cash": "0", "positions": {}, "last_prices": {}},
    )
    server._replay_snapshots.append(snap_prev)
    server._replay_snapshots.append(snap_curr)

    await server._dispatch("StepBackward", {"op": "StepBackward", "request_id": "r1"}, ws=None)

    events = list(server._outbox)
    pos_events = [e for e in events if e.get("event") == "PositionsUpdated"]
    assert len(pos_events) == 1, (
        f"StepBackward 後に PositionsUpdated が emit されていない. outbox events: "
        f"{[e.get('event') for e in events]}"
    )

    pe = pos_events[0]
    assert pe["request_id"] == ""
    assert pe["venue"] == "replay"
    assert "ts_ms" in pe and isinstance(pe["ts_ms"], int) and pe["ts_ms"] > 0
    assert len(pe["positions"]) == 1
    assert pe["positions"][0]["instrument_id"] == "7203.TSE"
    assert pe["positions"][0]["qty"] == "100"
