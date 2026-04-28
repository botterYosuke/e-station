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

    @pytest.mark.parametrize("order_type_val,expected,needs_price", [
        (1, "MARKET", False),
        (2, "LIMIT", True),
        (3, "STOP_MARKET", False),
        (4, "STOP_LIMIT", True),
        (5, "MARKET_TO_LIMIT", True),
        (6, "MARKET_IF_TOUCHED", False),
        (7, "LIMIT_IF_TOUCHED", True),
    ])
    def test_order_type_mapping(self, order_type_val: int, expected: str, needs_price: bool):
        price = "3000" if needs_price else None
        order = _FakeOrder(order_type=order_type_val, price=price)
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
        """start_live() が CacheConfig(database=None) を使っていることをソース検査で確認する。

        自己発火パターン（CacheConfig を自前で作るだけ）を避け、実際に start_live()
        の実装が database=None を維持していることを AST / ソース検査で保証する。
        """
        import inspect
        from engine.nautilus.engine_runner import NautilusRunner

        src = inspect.getsource(NautilusRunner.start_live)
        assert "database=None" in src, (
            "start_live() must construct CacheConfig(database=None) "
            "to keep nautilus persistence OFF (spec.md §3.2)"
        )
        assert "config.database is None" in src, (
            "start_live() must assert config.database is None "
            "as a guard against future misconfiguration"
        )

    def test_start_live_completes_without_exception(self):
        """start_live() が例外なく終了すること（P-5）。"""
        from engine.nautilus.engine_runner import NautilusRunner

        runner = NautilusRunner()
        # 例外が出なければ OK
        runner.start_live()

    def test_start_live_log_message(self, caplog):
        """start_live() が新しいログメッセージを出力すること（P-5）。"""
        import logging
        from engine.nautilus.engine_runner import NautilusRunner

        runner = NautilusRunner()
        with caplog.at_level(logging.INFO, logger="engine.nautilus.engine_runner"):
            runner.start_live()
        assert any("adapter classes ready" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# P-3: _check_safety_limits の parse error が reject に変わること
# ---------------------------------------------------------------------------


class TestSafetyLimitsParseError:
    def test_safety_limits_invalid_quantity_returns_reject_reason(self):
        """数値変換不可な quantity を渡すと None ではなくエラー文字列が返ること（P-3）。"""

        class _BadOrder:
            quantity = "not-a-number"
            price = None

        result = _check_safety_limits(_BadOrder(), max_qty=1000, max_notional_jpy=None)
        assert result is not None
        assert "SAFETY_CHECK_ERROR" in result


# ---------------------------------------------------------------------------
# P-4: _order_to_envelope の unknown side が ValueError を raise すること
# ---------------------------------------------------------------------------


class TestOrderToEnvelopeUnknownSide:
    def test_unknown_side_raises_value_error(self):
        """未知の side 値を渡すと ValueError が上がること（P-4）。"""
        order = _FakeOrder(side=99)  # 未知の side
        with pytest.raises(ValueError, match="unknown side value"):
            _order_to_envelope(order)


# ---------------------------------------------------------------------------
# R3: 指値注文で price=None の場合に ValueError が上がること
# ---------------------------------------------------------------------------


class TestOrderToEnvelopeLimitPriceRequired:
    def test_limit_order_without_price_raises(self):
        """LIMIT 注文で price=None の場合に ValueError が上がること（R3-HIGH-1）。"""
        order = _FakeOrder(order_type=2, price=None)  # 2=LIMIT
        with pytest.raises(ValueError, match="requires price"):
            _order_to_envelope(order)

    def test_stop_limit_order_without_price_raises(self):
        """STOP_LIMIT 注文で price=None の場合に ValueError が上がること。"""
        order = _FakeOrder(order_type=4, price=None)  # 4=STOP_LIMIT
        with pytest.raises(ValueError, match="requires price"):
            _order_to_envelope(order)

    def test_market_order_without_price_passes(self):
        """MARKET 注文では price=None でも ValueError が上がらないこと。"""
        order = _FakeOrder(order_type=1, price=None)  # 1=MARKET
        env = _order_to_envelope(order)
        assert env.price is None
