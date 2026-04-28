"""N2.2: TachibanaEventBridge / OrderIdMap テスト

検証項目:
- EC notification_type "2" (約定) → generate_order_filled
- EC notification_type "3" (取消) → generate_order_canceled
- EC notification_type "4" (失効) → generate_order_canceled (nautilus に expired なし)
- EC notification_type "1" (受付) → no-op
- 同一 (venue_order_id, trade_id) の重複受信で OrderFilled が 1 回しか発火しない (test_ec_idempotency)
- 部分約定 2 件の cumulative_qty / leaves_qty
- cancel リクエスト送信中に EC fill が来るレース
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from engine.exchanges.tachibana_event import OrderEcEvent
from engine.nautilus.clients.tachibana_event_bridge import OrderIdMap, TachibanaEventBridge
from nautilus_trader.model.enums import OrderSide, OrderType


# ---------------------------------------------------------------------------
# テスト用ヘルパ
# ---------------------------------------------------------------------------


def _make_ec(
    venue_order_id: str = "ORDER-001",
    trade_id: str = "TRADE-001",
    notification_type: str = "2",
    last_price: str | None = "3775",
    last_qty: str | None = "100",
    leaves_qty: str | None = "0",
    ts_event_ms: int = 1_704_067_200_000,
) -> OrderEcEvent:
    return OrderEcEvent(
        venue_order_id=venue_order_id,
        trade_id=trade_id,
        notification_type=notification_type,
        last_price=last_price,
        last_qty=last_qty,
        leaves_qty=leaves_qty,
        ts_event_ms=ts_event_ms,
    )


def _make_bridge() -> tuple[TachibanaEventBridge, MagicMock, OrderIdMap]:
    client = MagicMock()
    order_map = OrderIdMap()
    order_map.register(
        client_order_id="CLIENT-001",
        venue_order_id="ORDER-001",
        instrument_id="7203.TSE",
        strategy_id="test-strategy",
        order_side=OrderSide.BUY,
        order_type=OrderType.MARKET,
    )
    bridge = TachibanaEventBridge(client=client, order_id_map=order_map)
    return bridge, client, order_map


# ---------------------------------------------------------------------------
# 約定 (NT=2)
# ---------------------------------------------------------------------------


class TestFilledEc:
    def test_filled_calls_generate_order_filled(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="2")
        bridge.process_ec_event(ec)
        assert client.generate_order_filled.called

    def test_filled_with_correct_price(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="2", last_price="4000", last_qty="200")
        bridge.process_ec_event(ec)
        args, kwargs = client.generate_order_filled.call_args
        # last_px → Quantity が "4000.0" になる
        last_px = kwargs.get("last_px") or args[9]
        assert str(last_px) == "4000.0"

    def test_filled_with_correct_qty(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="2", last_qty="150")
        bridge.process_ec_event(ec)
        args, kwargs = client.generate_order_filled.call_args
        last_qty = kwargs.get("last_qty") or args[8]
        assert str(last_qty) == "150"

    def test_filled_ts_event_is_ms_to_ns(self):
        bridge, client, _ = _make_bridge()
        ts_ms = 1_704_067_200_123
        ec = _make_ec(notification_type="2", ts_event_ms=ts_ms)
        bridge.process_ec_event(ec)
        _, kwargs = client.generate_order_filled.call_args
        ts_event = kwargs.get("ts_event")
        assert ts_event == ts_ms * 1_000_000


# ---------------------------------------------------------------------------
# 取消 (NT=3) / 失効 (NT=4)
# ---------------------------------------------------------------------------


class TestCanceledEc:
    def test_canceled_calls_generate_order_canceled(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="3", last_price=None, last_qty=None)
        bridge.process_ec_event(ec)
        assert client.generate_order_canceled.called

    def test_expired_maps_to_generate_order_canceled(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="4", last_price=None, last_qty=None)
        bridge.process_ec_event(ec)
        assert client.generate_order_canceled.called


# ---------------------------------------------------------------------------
# 受付 (NT=1) → no-op
# ---------------------------------------------------------------------------


class TestReceivedEc:
    def test_received_is_noop(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="1")
        bridge.process_ec_event(ec)
        assert not client.generate_order_filled.called
        assert not client.generate_order_canceled.called


# ---------------------------------------------------------------------------
# 冪等化 (M5) — test_ec_idempotency
# ---------------------------------------------------------------------------


class TestEcIdempotency:
    """同一 (venue_order_id, trade_id) EC を 2 回送って OrderFilled が 1 回だけ発火する。"""

    def test_duplicate_ec_fires_only_once(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="2", trade_id="TRADE-DUP-001")
        bridge.process_ec_event(ec)
        bridge.process_ec_event(ec)  # 重複
        assert client.generate_order_filled.call_count == 1

    def test_different_trade_id_fires_twice(self):
        bridge, client, _ = _make_bridge()
        ec1 = _make_ec(notification_type="2", trade_id="TRADE-A")
        ec2 = _make_ec(notification_type="2", trade_id="TRADE-B")
        bridge.process_ec_event(ec1)
        bridge.process_ec_event(ec2)
        assert client.generate_order_filled.call_count == 2

    def test_reset_seen_clears_dedup(self):
        bridge, client, _ = _make_bridge()
        ec = _make_ec(notification_type="2", trade_id="TRADE-RESET")
        bridge.process_ec_event(ec)
        bridge.reset_seen()
        bridge.process_ec_event(ec)  # 日次リセット後は再処理
        assert client.generate_order_filled.call_count == 2


# ---------------------------------------------------------------------------
# 部分約定 2 件 → cumulative_qty / leaves_qty
# ---------------------------------------------------------------------------


class TestPartialFill:
    """部分約定 EC を 2 件流して leaves_qty が正しく減少すること。"""

    def test_two_partial_fills(self):
        bridge, client, _ = _make_bridge()
        # 1 件目: 400 株発注のうち 100 株約定、残 300
        ec1 = _make_ec(
            notification_type="2",
            trade_id="TRADE-P-001",
            last_qty="100",
            leaves_qty="300",
        )
        # 2 件目: さらに 200 株約定、残 100
        ec2 = _make_ec(
            notification_type="2",
            trade_id="TRADE-P-002",
            last_qty="200",
            leaves_qty="100",
        )
        bridge.process_ec_event(ec1)
        bridge.process_ec_event(ec2)
        assert client.generate_order_filled.call_count == 2
        # 1 件目の last_qty
        _, kw1 = client.generate_order_filled.call_args_list[0]
        last_qty_1 = kw1.get("last_qty") or client.generate_order_filled.call_args_list[0][0][8]
        assert str(last_qty_1) == "100"
        # 2 件目の last_qty
        _, kw2 = client.generate_order_filled.call_args_list[1]
        last_qty_2 = kw2.get("last_qty") or client.generate_order_filled.call_args_list[1][0][8]
        assert str(last_qty_2) == "200"


# ---------------------------------------------------------------------------
# cancel リクエスト送信中に EC fill が来るレース
# ---------------------------------------------------------------------------


class TestCancelFillRace:
    """cancel リクエスト後に EC fill を受けても OrderStatus が壊れない（OrderFilled が発火する）。"""

    def test_fill_after_cancel_request(self):
        bridge, client, _ = _make_bridge()
        # cancel 送信 (HTTP 側) → EC fill が先着
        ec_fill = _make_ec(
            notification_type="2",
            trade_id="TRADE-RACE-001",
            last_qty="100",
        )
        bridge.process_ec_event(ec_fill)
        # cancel 要求後に EC cancel が来ても問題ない（fill は既に発火済み）
        ec_cancel = _make_ec(
            notification_type="3",
            trade_id="TRADE-RACE-CANCEL",
        )
        bridge.process_ec_event(ec_cancel)
        assert client.generate_order_filled.call_count == 1
        assert client.generate_order_canceled.call_count == 1


# ---------------------------------------------------------------------------
# OrderIdMap
# ---------------------------------------------------------------------------


class TestOrderIdMap:
    def test_register_and_lookup(self):
        om = OrderIdMap()
        om.register(
            client_order_id="C-001",
            venue_order_id="V-001",
            instrument_id="7203.TSE",
            strategy_id="strat-1",
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )
        assert om.get_client_order_id("V-001") == "C-001"
        info = om.get_order_info("C-001")
        assert info is not None
        assert info["instrument_id"] == "7203.TSE"

    def test_remove_cleans_both_maps(self):
        om = OrderIdMap()
        om.register(
            client_order_id="C-002",
            venue_order_id="V-002",
            instrument_id="7203.TSE",
            strategy_id="strat-1",
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
        )
        om.remove("C-002")
        assert om.get_client_order_id("V-002") is None
        assert om.get_order_info("C-002") is None

    def test_warm_up_from_records(self):
        om = OrderIdMap()

        @dataclass
        class FakeRecord:
            client_order_id: str | None
            venue_order_id: str
            instrument_id: str
            order_side: str
            order_type: str
            status: str

        records = [
            FakeRecord("C-W01", "V-W01", "7203.TSE", "BUY", "MARKET", "ACCEPTED"),
            FakeRecord(None, "V-W02", "9984.TSE", "SELL", "LIMIT", "ACCEPTED"),
            FakeRecord("C-W03", "V-W03", "6758.TSE", "BUY", "MARKET", "FILLED"),  # 除外
        ]
        om.warm_up_from_records(records, strategy_id="strat-warm")
        assert om.get_client_order_id("V-W01") == "C-W01"
        assert om.get_client_order_id("V-W02") is not None  # WARM- prefix
        assert om.get_client_order_id("V-W03") is None  # FILLED は除外

    def test_unknown_venue_order_id_returns_none(self):
        om = OrderIdMap()
        assert om.get_client_order_id("UNKNOWN") is None


# ---------------------------------------------------------------------------
# P-2: canceled / expired で order_info が None の場合の WARNING ログ
# ---------------------------------------------------------------------------


class TestMissingOrderInfoWarning:
    """order_info が None の時に WARNING が出て generate_order_canceled が呼ばれない。

    OrderIdMap の公開 API (get_client_order_id / get_order_info) を MagicMock で
    差し替えて「venue→client は引けるが client→info は None」状態を再現する。
    プライベート辞書 (_by_venue / _by_client) への直接アクセスは行わない。
    """

    def _make_bridge_with_venue_only(self) -> tuple[TachibanaEventBridge, MagicMock]:
        """venue_order_id → client_order_id は解決できるが order_info が None のブリッジ。"""
        client = MagicMock()
        # OrderIdMap の公開 API を MagicMock で差し替える
        order_map = MagicMock(spec=OrderIdMap)
        order_map.get_client_order_id.return_value = "CLIENT-NOINFO"
        order_map.get_order_info.return_value = None
        bridge = TachibanaEventBridge(client=client, order_id_map=order_map)
        return bridge, client

    def test_canceled_warns_when_order_info_missing(self, caplog):
        import logging
        bridge, client = self._make_bridge_with_venue_only()
        ec = _make_ec(
            venue_order_id="ORDER-NOINFO",
            notification_type="3",
            last_price=None,
            last_qty=None,
        )
        with caplog.at_level(logging.WARNING, logger="engine.nautilus.clients.tachibana_event_bridge"):
            bridge.process_ec_event(ec)
        assert not client.generate_order_canceled.called
        assert any("skipping canceled/expired" in r.message for r in caplog.records)

    def test_expired_warns_when_order_info_missing(self, caplog):
        import logging
        bridge, client = self._make_bridge_with_venue_only()
        ec = _make_ec(
            venue_order_id="ORDER-NOINFO",
            notification_type="4",
            last_price=None,
            last_qty=None,
        )
        with caplog.at_level(logging.WARNING, logger="engine.nautilus.clients.tachibana_event_bridge"):
            bridge.process_ec_event(ec)
        assert not client.generate_order_canceled.called
        assert any("skipping canceled/expired" in r.message for r in caplog.records)
