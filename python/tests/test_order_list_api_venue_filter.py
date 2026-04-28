"""N1.15: TDD Red — GetOrderList venue=replay routes to WAL, not tachibana API.

Tests call _do_get_order_list_replay() directly to validate WAL→OrderRecordWire
conversion logic without a running server.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a minimal DataEngineServer with a fake cache_dir
# ---------------------------------------------------------------------------


def _make_server(tmp_path: Path):
    """Return a DataEngineServer instance wired to tmp_path as cache_dir."""
    from unittest.mock import AsyncMock

    from engine.server import DataEngineServer

    with patch("engine.server.BinanceWorker", return_value=MagicMock()), patch.object(
        DataEngineServer, "_startup_tachibana", AsyncMock(return_value=None)
    ):
        server = DataEngineServer(
            port=19999,  # not used — we call methods directly
            token="test-token",
            cache_dir=tmp_path,
        )
    return server


def _write_replay_wal(tmp_path: Path, entries: list[dict]) -> Path:
    """Write JSONL entries to the replay WAL file and return the path."""
    wal_path = tmp_path / "tachibana_orders_replay.jsonl"
    with open(wal_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return wal_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOrderListApiVenueFilter:
    """Tests for venue=replay branch in GetOrderList / _do_get_order_list_replay."""

    @pytest.mark.asyncio
    async def test_replay_wal_empty_returns_empty_list(self, tmp_path):
        """WAL ファイルが存在しない場合は空リストの OrderListUpdated を返す。"""
        server = _make_server(tmp_path)
        msg = {"op": "GetOrderList", "request_id": "req-1", "venue": "replay"}

        await server._do_get_order_list_replay(msg)

        outbox = list(server._outbox)
        assert len(outbox) == 1
        event = outbox[0]
        assert event["event"] == "OrderListUpdated"
        assert event["request_id"] == "req-1"
        assert event["orders"] == []

    @pytest.mark.asyncio
    async def test_replay_wal_submit_entry_becomes_order_record(self, tmp_path):
        """WAL の submit エントリが OrderRecordWire 形式に変換される。"""
        _write_replay_wal(
            tmp_path,
            [
                {
                    "phase": "submit",
                    "ts": 1700000000000,
                    "client_order_id": "REPLAY-ord-001",
                    "instrument_id": "7203.TSE",
                    "order_side": "BUY",
                    "order_type": "LIMIT",
                    "quantity": "100",
                    "price": "1500",
                }
            ],
        )
        server = _make_server(tmp_path)
        msg = {"op": "GetOrderList", "request_id": "req-2", "venue": "replay"}

        await server._do_get_order_list_replay(msg)

        outbox = list(server._outbox)
        assert len(outbox) == 1
        event = outbox[0]
        assert event["event"] == "OrderListUpdated"
        orders = event["orders"]
        assert len(orders) == 1

        o = orders[0]
        assert o["client_order_id"] == "REPLAY-ord-001"
        assert o["instrument_id"] == "7203.TSE"
        assert o["order_side"] == "BUY"
        assert o["order_type"] == "LIMIT"
        assert o["quantity"] == "100"
        assert o["filled_qty"] == "0"
        assert o["leaves_qty"] == "100"
        assert o["price"] == "1500"
        assert o["status"] == "SUBMITTED"
        assert o["ts_event_ms"] == 1700000000000

    @pytest.mark.asyncio
    async def test_replay_wal_only_submit_phase_included(self, tmp_path):
        """WAL のうち phase='submit' のエントリのみ返す（他 phase は無視）。"""
        _write_replay_wal(
            tmp_path,
            [
                {
                    "phase": "submit",
                    "ts": 1700000000001,
                    "client_order_id": "REPLAY-ord-A",
                    "instrument_id": "7203.TSE",
                    "order_side": "BUY",
                    "order_type": "MARKET",
                    "quantity": "50",
                },
                {
                    "phase": "fill",
                    "ts": 1700000001000,
                    "client_order_id": "REPLAY-ord-A",
                    "filled_qty": "50",
                },
                {
                    "phase": "cancel",
                    "ts": 1700000002000,
                    "client_order_id": "REPLAY-ord-B",
                },
            ],
        )
        server = _make_server(tmp_path)
        msg = {"op": "GetOrderList", "request_id": "req-3", "venue": "replay"}

        await server._do_get_order_list_replay(msg)

        event = list(server._outbox)[0]
        assert len(event["orders"]) == 1
        assert event["orders"][0]["client_order_id"] == "REPLAY-ord-A"

    @pytest.mark.asyncio
    async def test_tachibana_orders_not_mixed(self, tmp_path):
        """venue=replay のとき tachibana_orders.jsonl は参照しない。

        tachibana_orders.jsonl に submit エントリがあっても orders に含まれないこと。
        """
        # live WAL に書く
        live_wal = tmp_path / "tachibana_orders.jsonl"
        with open(live_wal, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "phase": "submit",
                        "ts": 1700000000000,
                        "client_order_id": "live-ord-001",
                        "instrument_id": "6758.TSE",
                        "order_side": "BUY",
                        "order_type": "LIMIT",
                        "quantity": "200",
                        "price": "3000",
                    }
                )
                + "\n"
            )

        # replay WAL は書かない (空)
        server = _make_server(tmp_path)
        msg = {"op": "GetOrderList", "request_id": "req-4", "venue": "replay"}

        await server._do_get_order_list_replay(msg)

        event = list(server._outbox)[0]
        # live エントリは含まれない
        assert event["orders"] == []

    @pytest.mark.asyncio
    async def test_get_order_list_venue_replay_routes_correctly(self, tmp_path):
        """_do_get_order_list を venue='replay' で呼ぶと replay 経路に入る
        (unknown_venue エラーにならない)。"""
        _write_replay_wal(tmp_path, [])
        server = _make_server(tmp_path)
        msg = {"op": "GetOrderList", "request_id": "req-5", "venue": "replay"}

        await server._do_get_order_list(msg)

        outbox = list(server._outbox)
        assert len(outbox) == 1
        event = outbox[0]
        # エラーではなく OrderListUpdated が返ること
        assert event["event"] == "OrderListUpdated"
        assert event.get("code") != "unknown_venue"
