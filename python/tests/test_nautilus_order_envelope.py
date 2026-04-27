"""Tpre.3: NautilusOrderEnvelope の形状とバリデーションテスト。
nautilus_trader を import せず、ハードコードした dict で検証 (Tpre.6 受け入れ条件)。
"""

import pytest
from engine.exchanges.tachibana_orders import NautilusOrderEnvelope


# ── nautilus MarketOrder.create() 互換 dict を model_validate できること ────────

MARKET_ORDER_DICT = {
    "client_order_id": "O-20260101-000001",
    "instrument_id": "7203.TSE",
    "order_side": "BUY",
    "order_type": "MARKET",
    "quantity": "100",
    "price": None,
    "trigger_price": None,
    "trigger_type": None,
    "time_in_force": "DAY",
    "expire_time_ns": None,
    "post_only": False,
    "reduce_only": False,
    "tags": ["cash_margin=cash"],
}

LIMIT_ORDER_DICT = {
    "client_order_id": "O-20260101-000002",
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


def test_nautilus_market_order_dict_validates():
    env = NautilusOrderEnvelope.model_validate(MARKET_ORDER_DICT)
    assert env.client_order_id == "O-20260101-000001"
    assert env.instrument_id == "7203.TSE"
    assert env.order_side == "BUY"
    assert env.order_type == "MARKET"
    assert env.quantity == "100"
    assert env.time_in_force == "DAY"
    assert env.post_only is False
    assert env.reduce_only is False
    assert "cash_margin=cash" in env.tags


def test_nautilus_limit_order_dict_validates():
    env = NautilusOrderEnvelope.model_validate(LIMIT_ORDER_DICT)
    assert env.order_side == "SELL"
    assert env.order_type == "LIMIT"
    assert env.price == "3500"


# ── 必須フィールド ────────────────────────────────────────────────────────────

def test_missing_client_order_id_raises():
    data = {**MARKET_ORDER_DICT}
    del data["client_order_id"]
    with pytest.raises(Exception):
        NautilusOrderEnvelope.model_validate(data)


def test_missing_instrument_id_raises():
    data = {**MARKET_ORDER_DICT}
    del data["instrument_id"]
    with pytest.raises(Exception):
        NautilusOrderEnvelope.model_validate(data)


# ── Optional フィールドのデフォルト ──────────────────────────────────────────────

def test_defaults_are_correct():
    minimal = {
        "client_order_id": "O-x",
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
