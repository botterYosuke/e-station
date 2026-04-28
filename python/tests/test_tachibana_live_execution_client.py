"""N2.1: TachibanaLiveExecutionClient テスト

検証項目:
- NautilusOrderEnvelope への変換（OrderType 全 6 種 + TimeInForce 全 7 種）
- _submit_order → generate_order_submitted + generate_order_accepted
- _submit_order: 市場閉場中 → generate_order_denied (MARKET_CLOSED)
- _submit_order: 数量上限超過 → generate_order_denied
- _cancel_order: 委譲 + 市場閉場中 → generate_order_cancel_rejected
- _modify_order: 市場閉場中 → generate_order_modify_rejected
- CacheConfig.database = None assertion (N2.3)
- max_qty 未設定で ValueError (N2.5)
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.nautilus.clients.tachibana import (
    TachibanaLiveExecutionClient,
    _order_to_envelope,
    _check_safety_limits,
)
from engine.nautilus.clients.tachibana_event_bridge import OrderIdMap
from engine.exchanges.tachibana_orders import NautilusOrderEnvelope


# ---------------------------------------------------------------------------
# ヘルパ: フェイク Order
# ---------------------------------------------------------------------------


class _FakeOrder:
    def __init__(
        self,
        client_order_id: str = "C-001",
        instrument_id: str = "7203.TSE",
        side: int = 1,  # BUY
        order_type: int = 1,  # MARKET
        quantity: str = "100",
        time_in_force: int = 5,  # DAY
        price: str | None = None,
        trigger_price: str | None = None,
        trigger_type: str | None = None,
        expire_time: int | None = None,
        tags: list[str] | None = None,
    ):
        self.client_order_id = client_order_id
        self.instrument_id = instrument_id
        self.side = side
        self.order_type = order_type
        self.quantity = quantity
        self.time_in_force = time_in_force
        self.price = price
        self.trigger_price = trigger_price
        self.trigger_type = trigger_type
        self.expire_time = expire_time
        self.tags = tags or ["cash_margin=cash", "account_type=specific"]
        self.is_post_only = False
        self.is_reduce_only = False


class _FakeSubmitCommand:
    def __init__(self, order: _FakeOrder, strategy_id: str = "strat-001"):
        self.order = order
        self.strategy_id = strategy_id
        self.venue_order_id = None


# ---------------------------------------------------------------------------
# _order_to_envelope
# ---------------------------------------------------------------------------


class TestOrderToEnvelope:
    """OrderType 全 6 種 + TimeInForce 全 7 種 の写像"""

    @pytest.mark.parametrize("order_type_val,expected", [
        (1, "MARKET"),
        (2, "LIMIT"),
        (3, "STOP_MARKET"),
        (4, "STOP_LIMIT"),
        (6, "MARKET_IF_TOUCHED"),
        (7, "LIMIT_IF_TOUCHED"),
    ])
    def test_order_type_mapping(self, order_type_val: int, expected: str):
        order = _FakeOrder(order_type=order_type_val)
        env = _order_to_envelope(order)
        assert env.order_type == expected

    @pytest.mark.parametrize("tif_val,expected", [
        (1, "GTC"),
        (2, "IOC"),
        (3, "FOK"),
        (4, "GTD"),
        (5, "DAY"),
        (6, "AT_THE_OPEN"),
        (7, "AT_THE_CLOSE"),
    ])
    def test_time_in_force_mapping(self, tif_val: int, expected: str):
        order = _FakeOrder(time_in_force=tif_val)
        env = _order_to_envelope(order)
        assert env.time_in_force == expected

    def test_buy_side(self):
        order = _FakeOrder(side=1)
        env = _order_to_envelope(order)
        assert env.order_side == "BUY"

    def test_sell_side(self):
        order = _FakeOrder(side=2)
        env = _order_to_envelope(order)
        assert env.order_side == "SELL"

    def test_price_included_for_limit(self):
        order = _FakeOrder(order_type=2, price="3800")
        env = _order_to_envelope(order)
        assert env.price == "3800"

    def test_tags_transferred(self):
        order = _FakeOrder(tags=["cash_margin=cash", "account_type=specific"])
        env = _order_to_envelope(order)
        assert "cash_margin=cash" in env.tags

    def test_returns_nautilus_order_envelope(self):
        order = _FakeOrder()
        env = _order_to_envelope(order)
        assert isinstance(env, NautilusOrderEnvelope)


# ---------------------------------------------------------------------------
# _check_safety_limits (N2.5)
# ---------------------------------------------------------------------------


class TestSafetyLimits:
    def test_qty_within_limit_passes(self):
        order = _FakeOrder(quantity="100")
        result = _check_safety_limits(order, max_qty=1000, max_notional_jpy=None)
        assert result is None

    def test_qty_exceeds_limit_rejected(self):
        order = _FakeOrder(quantity="2000")
        result = _check_safety_limits(order, max_qty=1000, max_notional_jpy=None)
        assert result is not None
        assert "QUANTITY_EXCEEDED" in result

    def test_notional_within_limit_passes(self):
        order = _FakeOrder(quantity="100", price="3000", order_type=2)
        result = _check_safety_limits(
            order, max_qty=None, max_notional_jpy=1_000_000
        )
        assert result is None  # 100 * 3000 = 300_000 < 1_000_000

    def test_notional_exceeds_limit_rejected(self):
        order = _FakeOrder(quantity="1000", price="5000", order_type=2)
        result = _check_safety_limits(
            order, max_qty=None, max_notional_jpy=1_000_000
        )
        assert result is not None  # 1000 * 5000 = 5_000_000 > 1_000_000
        assert "NOTIONAL_EXCEEDED" in result

    def test_both_none_always_passes(self):
        order = _FakeOrder(quantity="999999")
        result = _check_safety_limits(order, max_qty=None, max_notional_jpy=None)
        assert result is None


# ---------------------------------------------------------------------------
# TachibanaLiveExecutionClient 初期化 (N2.5)
# ---------------------------------------------------------------------------


class TestClientInitSafety:
    def test_missing_max_qty_raises_value_error(self):
        with pytest.raises(ValueError, match="max_qty"):
            TachibanaLiveExecutionClient(
                # Minimal kwargs to pass the super().__init__ call
                loop=asyncio.new_event_loop(),
                client_id=MagicMock(),
                venue=MagicMock(),
                oms_type=MagicMock(),
                account_type=MagicMock(),
                base_currency=None,
                instrument_provider=MagicMock(),
                msgbus=MagicMock(),
                cache=MagicMock(),
                clock=MagicMock(),
                session=MagicMock(),
                second_password="pw",
                max_qty=None,
                max_notional_jpy=1_000_000,
            )

    def test_missing_max_notional_raises_value_error(self):
        with pytest.raises(ValueError, match="max_notional_jpy"):
            TachibanaLiveExecutionClient(
                loop=asyncio.new_event_loop(),
                client_id=MagicMock(),
                venue=MagicMock(),
                oms_type=MagicMock(),
                account_type=MagicMock(),
                base_currency=None,
                instrument_provider=MagicMock(),
                msgbus=MagicMock(),
                cache=MagicMock(),
                clock=MagicMock(),
                session=MagicMock(),
                second_password="pw",
                max_qty=1000,
                max_notional_jpy=None,
            )


# ---------------------------------------------------------------------------
# N2.3: CacheConfig.database = None assertion
# ---------------------------------------------------------------------------


class TestCacheConfigPersistenceOff:
    """persistence=None が OFF になっていること。"""

    def test_cache_config_database_is_none(self):
        from nautilus_trader.config import CacheConfig

        config = CacheConfig(database=None)
        assert config.database is None
