"""issue #42 Phase 4: KabuStationEventBridge unit tests.

kabu PUSH order event JSON → Nautilus 風の events に変換するブリッジ:

- State=1 (受付) → ``OrderAccepted``
- State=5 (約定) → ``OrderFilled``
- 取消通知 → ``OrderCanceled``
- DetailState=4 (拒否) → ``OrderRejected``

kabu の PUSH JSON は polling (GET /orders) でも取得できる。bridge は dict を受け取り
``client.generate_*`` 互換の callback を呼ぶ。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_bridge():
    from engine.nautilus.clients.kabu_station.kabu_station_event_bridge import (
        KabuStationEventBridge,
    )

    client = MagicMock()
    bridge = KabuStationEventBridge(client=client)
    return bridge, client


class TestKabuOrderEventToNautilusFilled:
    def test_state_5_emits_order_filled(self):
        bridge, client = _make_bridge()
        # kabu 注文レコード (GET /orders 抜粋) — State=5 は約定
        record = {
            "ID": "ORD-100",
            "OrderID": "ORD-100",
            "State": 5,
            "Symbol": "9433",
            "Exchange": 1,
            "Price": 1500.0,
            "OrderQty": 100,
            "CumQty": 100,
            "Side": "2",  # buy
            "RecvTime": "2025-05-10T10:00:00+09:00",
        }
        bridge.process_order_record(record)

        client.generate_order_filled.assert_called_once()

    def test_state_1_emits_order_accepted(self):
        bridge, client = _make_bridge()
        record = {
            "ID": "ORD-101",
            "OrderID": "ORD-101",
            "State": 1,
            "Symbol": "9433",
            "Exchange": 1,
            "Price": 1500.0,
            "OrderQty": 100,
            "CumQty": 0,
            "Side": "2",
        }
        bridge.process_order_record(record)
        client.generate_order_accepted.assert_called_once()

    def test_canceled_state_emits_order_canceled(self):
        bridge, client = _make_bridge()
        # kabu 取消は DetailState または State 経路で表現される。
        # 実装上は ``State == 3`` (取消済) を canceled とみなす契約を pin する。
        record = {
            "ID": "ORD-102",
            "OrderID": "ORD-102",
            "State": 3,  # canceled
            "Symbol": "9433",
            "Exchange": 1,
            "Price": 1500.0,
            "OrderQty": 100,
            "CumQty": 0,
            "Side": "2",
        }
        bridge.process_order_record(record)
        client.generate_order_canceled.assert_called_once()


class TestIdempotency:
    def test_same_order_record_processed_twice_emits_once(self):
        """同一 OrderID の record を 2 回投入しても 1 回だけ generate される（冪等）。"""
        bridge, client = _make_bridge()
        record = {
            "ID": "ORD-103",
            "OrderID": "ORD-103",
            "State": 5,
            "Symbol": "9433",
            "Exchange": 1,
            "Price": 1500.0,
            "OrderQty": 100,
            "CumQty": 100,
            "Side": "2",
        }
        bridge.process_order_record(record)
        bridge.process_order_record(record)
        # 1 回だけ
        assert client.generate_order_filled.call_count == 1
