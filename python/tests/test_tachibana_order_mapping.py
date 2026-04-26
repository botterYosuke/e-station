"""TDD Red → Green: T0.4 — _envelope_to_wire() フィールド写像テスト。

architecture.md §10.1〜§10.4 の写像表を検証する。
HTTP 送信は mock しない（単純写像の単体テスト）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_orders import (
    NautilusOrderEnvelope,
    TachibanaWireOrderRequest,
    UnsupportedOrderError,
    _envelope_to_wire,
)
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session(zyoutoeki_kazei_c: str = "1") -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://demo.example/request/"),
        url_master=MasterUrl("https://demo.example/master/"),
        url_price=PriceUrl("https://demo.example/price/"),
        url_event=EventUrl("https://demo.example/event/"),
        url_event_ws="wss://demo.example/event/",
        zyoutoeki_kazei_c=zyoutoeki_kazei_c,
    )


def _envelope(**overrides) -> NautilusOrderEnvelope:
    base = {
        "client_order_id": "cid-001",
        "instrument_id": "7203.T/TSE",
        "order_side": "BUY",
        "order_type": "MARKET",
        "quantity": "100",
        "time_in_force": "DAY",
        "post_only": False,
        "reduce_only": False,
        "tags": ["cash_margin=cash"],
    }
    return NautilusOrderEnvelope(**{**base, **overrides})


# ---------------------------------------------------------------------------
# instrument_id → sIssueCode / sSizyouC
# ---------------------------------------------------------------------------


def test_instrument_id_tse_splits_correctly():
    wire = _envelope_to_wire(_envelope(instrument_id="7203.T/TSE"), _session(), "pw")
    assert wire.issue_code == "7203"
    assert wire.market_code == "00"


def test_instrument_id_numeric_only():
    wire = _envelope_to_wire(_envelope(instrument_id="8411.T/TSE"), _session(), "pw")
    assert wire.issue_code == "8411"


# ---------------------------------------------------------------------------
# order_side → sBaibaiKubun
# ---------------------------------------------------------------------------


def test_order_side_buy_maps_to_3():
    wire = _envelope_to_wire(_envelope(order_side="BUY"), _session(), "pw")
    assert wire.side == "3"


def test_order_side_sell_maps_to_1():
    wire = _envelope_to_wire(_envelope(order_side="SELL"), _session(), "pw")
    assert wire.side == "1"


# ---------------------------------------------------------------------------
# order_type → sOrderPrice / sCondition
# ---------------------------------------------------------------------------


def test_order_type_market_sets_price_to_zero():
    wire = _envelope_to_wire(_envelope(order_type="MARKET"), _session(), "pw")
    assert wire.price == "0"
    assert wire.condition == "0"


def test_order_type_limit_uses_price_value():
    wire = _envelope_to_wire(
        _envelope(order_type="LIMIT", price="2500"), _session(), "pw"
    )
    assert wire.price == "2500"
    assert wire.condition == "0"


def test_order_type_limit_missing_price_raises():
    with pytest.raises(UnsupportedOrderError) as exc_info:
        _envelope_to_wire(_envelope(order_type="LIMIT", price=None), _session(), "pw")
    assert exc_info.value.reason_code == "VENUE_UNSUPPORTED"


# ---------------------------------------------------------------------------
# time_in_force → sCondition / sOrderExpireDay
# ---------------------------------------------------------------------------


def test_time_in_force_day_sets_expire_day_zero():
    wire = _envelope_to_wire(_envelope(time_in_force="DAY"), _session(), "pw")
    assert wire.condition == "0"
    assert wire.expire_day == "0"


# ---------------------------------------------------------------------------
# tags: cash_margin → sGenkinShinyouKubun
# ---------------------------------------------------------------------------


def test_tags_cash_maps_to_0():
    wire = _envelope_to_wire(_envelope(tags=["cash_margin=cash"]), _session(), "pw")
    assert wire.cash_margin == "0"


# ---------------------------------------------------------------------------
# account_type: session fallback
# ---------------------------------------------------------------------------


def test_account_type_uses_session_zyoutoeki_when_no_tag():
    wire = _envelope_to_wire(
        _envelope(tags=["cash_margin=cash"]), _session(zyoutoeki_kazei_c="3"), "pw"
    )
    assert wire.account_type == "3"


# ---------------------------------------------------------------------------
# quantity → sOrderSuryou
# ---------------------------------------------------------------------------


def test_quantity_passes_through():
    wire = _envelope_to_wire(_envelope(quantity="500"), _session(), "pw")
    assert wire.qty == "500"


# ---------------------------------------------------------------------------
# second_password → sSecondPassword
# ---------------------------------------------------------------------------


def test_second_password_is_in_wire():
    wire = _envelope_to_wire(_envelope(), _session(), "secret123")
    assert wire.second_password == "secret123"


def test_second_password_not_in_repr():
    wire = _envelope_to_wire(_envelope(), _session(), "secret123")
    assert "secret123" not in repr(wire)


# ---------------------------------------------------------------------------
# C-3: __str__ / model_dump() / model_dump_json() でも second_password がマスクされる
# ---------------------------------------------------------------------------


def test_second_password_not_in_str():
    """__str__ でも second_password が平文で出ないこと（C-3）。"""
    wire = _envelope_to_wire(_envelope(), _session(), "secret")
    assert "secret" not in str(wire)


def test_second_password_not_in_model_dump_json():
    """model_dump_json() でも second_password が平文で出ないこと（C-3）。"""
    wire = _envelope_to_wire(_envelope(), _session(), "secret")
    assert "secret" not in wire.model_dump_json()


def test_second_password_masked_in_model_dump():
    """model_dump()["second_password"] が '[REDACTED]' であること（C-3）。"""
    wire = _envelope_to_wire(_envelope(), _session(), "secret")
    assert wire.model_dump()["second_password"] == "[REDACTED]"
