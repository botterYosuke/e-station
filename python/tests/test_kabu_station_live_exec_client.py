"""issue #42 Phase 4: KabuStationLiveExecutionClient unit tests.

テスト対象 (`python/engine/nautilus/clients/kabu_station/kabu_station_exec_client.py`):

- ``warm_up()`` 成功時 ``True`` 返り（kabu REST `/orders` 取得成功）
- ``warm_up()`` 失敗時 ``False`` 返り（例外 catch、Phase 1 の判定経路と互換）
- ``close()`` で session / subscription 解放
- ``max_qty`` / ``max_notional_jpy`` 未設定で起動拒否（tachibana 同等）
- ``_submit_order`` の安全装置: 数量超過 / 金額超過時に ``OrderDenied`` 経路

KabuStationVenue / KabuOrderClient / KabuRestClient は MagicMock で置換し、
ネットワーク依存を排除する。
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_kabu_venue_with_orders(records: list[dict] | None = None, raise_exc: Exception | None = None):
    """KabuStationVenue mock を作る。

    `_get_order_client().poll_fills` ではなく `KabuRestClient.fetch_orders` 互換 API を期待する。
    """
    venue = MagicMock()
    if raise_exc is not None:
        venue.fetch_orders = AsyncMock(side_effect=raise_exc)
    else:
        venue.fetch_orders = AsyncMock(return_value=records or [])
    venue.close = AsyncMock(return_value=None)
    return venue


# ---------------------------------------------------------------------------
# warm_up: 成功 / 失敗
# ---------------------------------------------------------------------------


class TestWarmUpReturnsTrueOnSuccess:
    def test_warm_up_returns_true_when_fetch_orders_succeeds(self):
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            KabuStationLiveExecutionClient,
        )

        venue = _make_kabu_venue_with_orders([])  # 空でも成功なら True
        client = KabuStationLiveExecutionClient(
            kabu_venue=venue,
            strategy_id="kabu-test",
            max_qty=100,
            max_notional_jpy=500_000,
        )

        ok = asyncio.run(client.warm_up())
        assert ok is True
        venue.fetch_orders.assert_awaited_once()


class TestWarmUpReturnsFalseOnException:
    def test_warm_up_returns_false_when_fetch_orders_raises(self):
        """warm_up は例外を catch して False を返す（Phase 1 判定経路と互換）。"""
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            KabuStationLiveExecutionClient,
        )

        venue = _make_kabu_venue_with_orders(raise_exc=RuntimeError("kabu /orders 503"))
        client = KabuStationLiveExecutionClient(
            kabu_venue=venue,
            strategy_id="kabu-test",
            max_qty=100,
            max_notional_jpy=500_000,
        )

        ok = asyncio.run(client.warm_up())
        assert ok is False, "warm_up must return False on exception (Phase 1 contract)"


# ---------------------------------------------------------------------------
# close: session / subscription 解放
# ---------------------------------------------------------------------------


class TestCloseReleasesSession:
    def test_close_invokes_kabu_venue_clear(self):
        """close() は KabuStationVenue.clear() 経由で session / subscription を解放する。"""
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            KabuStationLiveExecutionClient,
        )

        venue = MagicMock()
        venue.clear = MagicMock()
        venue.fetch_orders = AsyncMock(return_value=[])
        client = KabuStationLiveExecutionClient(
            kabu_venue=venue,
            strategy_id="kabu-test",
            max_qty=100,
            max_notional_jpy=500_000,
        )

        asyncio.run(client.close())

        venue.clear.assert_called_once()


# ---------------------------------------------------------------------------
# max_qty / max_notional_jpy 未設定で起動拒否（tachibana 同等）
# ---------------------------------------------------------------------------


class TestSafetyGuardConstruction:
    def test_construct_rejects_when_max_qty_missing(self):
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            KabuStationLiveExecutionClient,
        )

        venue = _make_kabu_venue_with_orders([])
        with pytest.raises(ValueError, match="max_qty"):
            KabuStationLiveExecutionClient(
                kabu_venue=venue,
                strategy_id="kabu-test",
                max_qty=None,
                max_notional_jpy=500_000,
            )

    def test_construct_rejects_when_max_notional_jpy_missing(self):
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            KabuStationLiveExecutionClient,
        )

        venue = _make_kabu_venue_with_orders([])
        with pytest.raises(ValueError, match="max_notional_jpy"):
            KabuStationLiveExecutionClient(
                kabu_venue=venue,
                strategy_id="kabu-test",
                max_qty=100,
                max_notional_jpy=None,
            )


# ---------------------------------------------------------------------------
# _submit_order の安全装置（数量超過 / 金額超過 → reject）
# ---------------------------------------------------------------------------


class _FakeOrder:
    """Nautilus Order 互換の最小 fake。"""

    def __init__(self, *, qty, price=None, side="BUY", order_type="MARKET"):
        from nautilus_trader.model.enums import OrderSide, OrderType

        self.quantity = qty
        self.price = price
        self.side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
        self.order_type = OrderType.MARKET if order_type == "MARKET" else OrderType.LIMIT
        self.client_order_id = "C-1"
        self.instrument_id = "9433.KabuStation Stock"

    @property
    def is_post_only(self):
        return False

    @property
    def is_reduce_only(self):
        return False


def _make_safety_test_client(*, max_qty=100, max_notional_jpy=500_000):
    from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
        KabuStationLiveExecutionClient,
    )

    venue = _make_kabu_venue_with_orders([])
    client = KabuStationLiveExecutionClient(
        kabu_venue=venue,
        strategy_id="kabu-test",
        max_qty=max_qty,
        max_notional_jpy=max_notional_jpy,
    )
    # generate_order_denied は LiveExecutionClient を継承していない fake のためカスタムで記録する
    client.denied_log = []

    def _denied(reason: str, **_):
        client.denied_log.append(reason)

    client._record_order_denied = _denied  # type: ignore[attr-defined]
    return client, venue


class TestSafetyLimitsBlockSubmit:
    def test_safety_check_rejects_quantity_over_max_qty(self):
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            _check_safety_limits,
        )

        order = _FakeOrder(qty=1_000)
        reason = _check_safety_limits(order, max_qty=100, max_notional_jpy=500_000)
        assert reason is not None and "QUANTITY_EXCEEDED" in reason

    def test_safety_check_rejects_notional_over_max_notional_jpy(self):
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            _check_safety_limits,
        )

        # 1000 株 × 1_000 円 = 1_000_000 > 500_000
        order = _FakeOrder(qty=1_000, price=1_000)
        reason = _check_safety_limits(order, max_qty=100_000, max_notional_jpy=500_000)
        assert reason is not None and "NOTIONAL_EXCEEDED" in reason

    def test_safety_check_passes_within_limits(self):
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            _check_safety_limits,
        )

        order = _FakeOrder(qty=10, price=1_000)  # 10_000 < 500_000
        reason = _check_safety_limits(order, max_qty=100, max_notional_jpy=500_000)
        assert reason is None
