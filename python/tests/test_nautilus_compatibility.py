"""T0.8: nautilus_trader 互換性テスト。

nautilus_trader を import しない状態で、nautilus_trader.model.orders.MarketOrder.create(...)
互換の dict を `NautilusOrderEnvelope.model_validate(...)` で読み込み可能なことを検証する。

検証対象:
  - field 名の一致（client_order_id / instrument_id / order_side / order_type 等）
  - enum 文字列の一致（SCREAMING_SNAKE_CASE — nautilus 規約）
  - 余分なフィールドは extra="ignore" で許容されること
  - 欠損フィールドはデフォルト値で補完されること
"""

from __future__ import annotations

import pytest

from engine.exchanges.tachibana_orders import NautilusOrderEnvelope


# ---------------------------------------------------------------------------
# nautilus_trader.model.orders.MarketOrder.create(...) 互換 dict
# field 名・enum 文字列は nautilus 1.211.x の型定義に合わせる
# ---------------------------------------------------------------------------

NAUTILUS_MARKET_ORDER_DICT = {
    # nautilus ClientOrderId: 1–36 ASCII printable
    "client_order_id": "O-20260426-000001",
    # nautilus InstrumentId: "<symbol>.<venue>"
    "instrument_id": "7203.TSE",
    # nautilus OrderSide enum (SCREAMING_SNAKE_CASE)
    "order_side": "BUY",
    # nautilus OrderType enum
    "order_type": "MARKET",
    # nautilus Quantity: str or int (NautilusOrderEnvelope は str 受入)
    "quantity": "100",
    # nautilus TimeInForce enum
    "time_in_force": "DAY",
    # nautilus optional fields
    "price": None,
    "trigger_price": None,
    "trigger_type": None,
    "expire_time_ns": None,
    "post_only": False,
    "reduce_only": False,
    # venue extension tags
    "tags": ["cash_margin=cash"],
}

NAUTILUS_LIMIT_ORDER_DICT = {
    "client_order_id": "O-20260426-000002",
    "instrument_id": "9984.TSE",
    "order_side": "SELL",
    "order_type": "LIMIT",
    "quantity": "50",
    "price": "3500",
    "trigger_price": None,
    "trigger_type": None,
    "time_in_force": "DAY",
    "expire_time_ns": None,
    "post_only": False,
    "reduce_only": False,
    "tags": [],
}

# nautilus MarketOrder には extra fields が含まれる場合がある
# (e.g., status, venue_order_id, filled_qty, etc.)
NAUTILUS_MARKET_ORDER_WITH_EXTRAS = {
    **NAUTILUS_MARKET_ORDER_DICT,
    # nautilus から来る追加フィールド（無視されること）
    "status": "INITIALIZED",
    "filled_qty": "0",
    "avg_px": None,
    "slippage": None,
    "init_id": "event-id-001",
    "ts_init": 1714123456789000000,
    "ts_last": 1714123456789000000,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_nautilus_market_order_dict_validates():
    """nautilus MarketOrder.create() 互換 dict が model_validate できること（Tpre.6 N2 条件）。"""
    env = NautilusOrderEnvelope.model_validate(NAUTILUS_MARKET_ORDER_DICT)

    assert env.client_order_id == "O-20260426-000001"
    assert env.instrument_id == "7203.TSE"
    assert env.order_side == "BUY"
    assert env.order_type == "MARKET"
    assert env.quantity == "100"
    assert env.time_in_force == "DAY"
    assert env.price is None
    assert env.trigger_price is None
    assert env.trigger_type is None
    assert env.expire_time_ns is None
    assert env.post_only is False
    assert env.reduce_only is False
    assert "cash_margin=cash" in env.tags


def test_nautilus_limit_order_dict_validates():
    """nautilus LimitOrder.create() 互換 dict が model_validate できること。"""
    env = NautilusOrderEnvelope.model_validate(NAUTILUS_LIMIT_ORDER_DICT)

    assert env.order_side == "SELL"
    assert env.order_type == "LIMIT"
    assert env.price == "3500"
    assert env.quantity == "50"
    assert env.instrument_id == "9984.TSE"


def test_nautilus_order_dict_with_extra_fields_validates():
    """nautilus Order には追加フィールドがあっても extra='ignore' で受け入れること。"""
    env = NautilusOrderEnvelope.model_validate(NAUTILUS_MARKET_ORDER_WITH_EXTRAS)

    # 基本フィールドは正しく読み込まれること
    assert env.client_order_id == "O-20260426-000001"
    assert env.order_type == "MARKET"
    # 追加フィールドはモデルに含まれない（hasattr で確認）
    assert not hasattr(env, "status")
    assert not hasattr(env, "filled_qty")
    assert not hasattr(env, "ts_init")


def test_enum_string_case_matches_nautilus():
    """enum 値が SCREAMING_SNAKE_CASE で正しく受け取れること（nautilus 互換）。"""
    # nautilus OrderSide.BUY → "BUY", OrderSide.SELL → "SELL"
    for side in ("BUY", "SELL"):
        env = NautilusOrderEnvelope.model_validate({**NAUTILUS_MARKET_ORDER_DICT, "order_side": side})
        assert env.order_side == side

    # nautilus OrderType 各値
    for order_type in ("MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT"):
        env = NautilusOrderEnvelope.model_validate({**NAUTILUS_MARKET_ORDER_DICT, "order_type": order_type})
        assert env.order_type == order_type

    # nautilus TimeInForce 各値（Phase O0 では DAY のみ有効だが型レベルでは受け入れる）
    for tif in ("DAY", "GTC", "GTD", "IOC", "FOK", "AT_THE_OPEN", "AT_THE_CLOSE"):
        env = NautilusOrderEnvelope.model_validate({**NAUTILUS_MARKET_ORDER_DICT, "time_in_force": tif})
        assert env.time_in_force == tif


def test_tags_field_defaults_to_empty_list():
    """tags フィールドが省略された場合は空リストになること。"""
    data = {k: v for k, v in NAUTILUS_MARKET_ORDER_DICT.items() if k != "tags"}
    env = NautilusOrderEnvelope.model_validate(data)
    assert env.tags == []


def test_optional_fields_default_to_none():
    """省略可能フィールドが省略された場合は None になること。"""
    minimal = {
        "client_order_id": "min-order-001",
        "instrument_id": "7203.TSE",
        "order_side": "BUY",
        "order_type": "MARKET",
        "quantity": "100",
        "time_in_force": "DAY",
        "post_only": False,
        "reduce_only": False,
    }
    env = NautilusOrderEnvelope.model_validate(minimal)
    assert env.price is None
    assert env.trigger_price is None
    assert env.trigger_type is None
    assert env.expire_time_ns is None
    assert env.tags == []
