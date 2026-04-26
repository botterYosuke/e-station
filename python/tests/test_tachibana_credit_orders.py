"""T3.5 Phase O3 — 信用取引・逆指値・期日・建玉テスト。

flowsurface tachibana.rs:4014-4350 の信用テストを Python に移植。
architecture.md §10.1〜§10.4 の写像表を検証する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
# T3.1-1 信用取引 cash_margin 写像 (architecture.md §10.4)
# ---------------------------------------------------------------------------


def test_cash_margin_credit_new_mapping():
    """margin_credit_new → sGenkinShinyouKubun="2" (制度信用 新規)。"""
    wire = _envelope_to_wire(
        _envelope(tags=["cash_margin=margin_credit_new"]), _session(), "pw"
    )
    assert wire.cash_margin == "2"


def test_cash_margin_credit_repay_mapping():
    """margin_credit_repay → sGenkinShinyouKubun="4" (制度信用 返済)。"""
    wire = _envelope_to_wire(
        _envelope(
            tags=["cash_margin=margin_credit_repay"],
            order_side="SELL",
        ),
        _session(),
        "pw",
    )
    assert wire.cash_margin == "4"


def test_cash_margin_general_new_mapping():
    """margin_general_new → sGenkinShinyouKubun="6" (一般信用 新規)。"""
    wire = _envelope_to_wire(
        _envelope(tags=["cash_margin=margin_general_new"]), _session(), "pw"
    )
    assert wire.cash_margin == "6"


def test_cash_margin_general_repay_mapping():
    """margin_general_repay → sGenkinShinyouKubun="8" (一般信用 返済)。"""
    wire = _envelope_to_wire(
        _envelope(
            tags=["cash_margin=margin_general_repay"],
            order_side="SELL",
        ),
        _session(),
        "pw",
    )
    assert wire.cash_margin == "8"


def test_cash_margin_cash_still_maps_to_0():
    """既存の cash → "0" 写像が引き続き動くこと（リグレッション）。"""
    wire = _envelope_to_wire(
        _envelope(tags=["cash_margin=cash"]), _session(), "pw"
    )
    assert wire.cash_margin == "0"


# ---------------------------------------------------------------------------
# T3.1-2 逆指値注文 (architecture.md §10.1)
# ---------------------------------------------------------------------------


def test_stop_market_order_mapping():
    """STOP_MARKET → sOrderPrice="*" + sGyakusasiPrice="0"。"""
    wire = _envelope_to_wire(
        _envelope(
            order_type="STOP_MARKET",
            trigger_price="2400",
            trigger_type="LAST",
        ),
        _session(),
        "pw",
    )
    assert wire.price == "*"
    assert wire.gyakusasi_price == "0"
    assert wire.gyakusasi_zyouken == "2400"


def test_stop_limit_order_mapping():
    """STOP_LIMIT → sOrderPrice=<price> + sGyakusasiPrice=<price>。"""
    wire = _envelope_to_wire(
        _envelope(
            order_type="STOP_LIMIT",
            price="2500",
            trigger_price="2400",
            trigger_type="LAST",
        ),
        _session(),
        "pw",
    )
    assert wire.price == "2500"
    assert wire.gyakusasi_price == "2500"
    assert wire.gyakusasi_zyouken == "2400"


def test_stop_market_requires_trigger_price():
    """STOP_MARKET で trigger_price なし → UnsupportedOrderError。"""
    with pytest.raises(UnsupportedOrderError) as exc_info:
        _envelope_to_wire(
            _envelope(
                order_type="STOP_MARKET",
                trigger_price=None,
                trigger_type="LAST",
            ),
            _session(),
            "pw",
        )
    assert exc_info.value.reason_code == "VENUE_UNSUPPORTED"


def test_stop_market_requires_last_trigger_type():
    """立花は LAST 以外の trigger_type を未対応として reject。"""
    with pytest.raises(UnsupportedOrderError) as exc_info:
        _envelope_to_wire(
            _envelope(
                order_type="STOP_MARKET",
                trigger_price="2400",
                trigger_type="BID_ASK",
            ),
            _session(),
            "pw",
        )
    assert exc_info.value.reason_code == "VENUE_UNSUPPORTED"


def test_stop_limit_requires_price():
    """STOP_LIMIT で price なし → UnsupportedOrderError。"""
    with pytest.raises(UnsupportedOrderError) as exc_info:
        _envelope_to_wire(
            _envelope(
                order_type="STOP_LIMIT",
                price=None,
                trigger_price="2400",
                trigger_type="LAST",
            ),
            _session(),
            "pw",
        )
    assert exc_info.value.reason_code == "VENUE_UNSUPPORTED"


# ---------------------------------------------------------------------------
# T3.1-3 期日指定 (architecture.md §10.2 GTD)
# ---------------------------------------------------------------------------


def _ns_from_jst_date(yyyymmdd: str) -> int:
    """YYYYMMDD (JST) → UTC nanoseconds。"""
    jst = timezone(timedelta(hours=9))
    dt = datetime.strptime(yyyymmdd, "%Y%m%d").replace(hour=0, minute=0, second=0, tzinfo=jst)
    return int(dt.timestamp() * 1_000_000_000)


def test_gtd_order_expire_day_mapping():
    """GTD + expire_time_ns → sOrderExpireDay=YYYYMMDD (JST) に変換される。"""
    expire_ns = _ns_from_jst_date("20261231")
    wire = _envelope_to_wire(
        _envelope(
            time_in_force="GTD",
            expire_time_ns=expire_ns,
        ),
        _session(),
        "pw",
    )
    assert wire.expire_day == "20261231"


def test_gtd_without_expire_time_raises():
    """GTD で expire_time_ns なし → UnsupportedOrderError。"""
    with pytest.raises(UnsupportedOrderError) as exc_info:
        _envelope_to_wire(
            _envelope(time_in_force="GTD", expire_time_ns=None),
            _session(),
            "pw",
        )
    assert exc_info.value.reason_code == "VENUE_UNSUPPORTED"


# ---------------------------------------------------------------------------
# T3.1-4 建玉個別指定 (architecture.md §10.4 tategyoku)
# ---------------------------------------------------------------------------


def test_tategyoku_tag_maps_to_tatebi_data():
    """tategyoku=<id> tag → sTatebiType="1" + tategyoku_id が設定される。"""
    wire = _envelope_to_wire(
        _envelope(
            tags=["cash_margin=margin_credit_repay", "tategyoku=12345"],
            order_side="SELL",
        ),
        _session(),
        "pw",
    )
    assert wire.tatebi_type == "1"
    assert wire.tategyoku_id == "12345"


def test_tategyoku_absent_gives_star():
    """tategyoku tag なし → sTatebiType="*" (立花デフォルト)。"""
    wire = _envelope_to_wire(
        _envelope(tags=["cash_margin=cash"]),
        _session(),
        "pw",
    )
    assert wire.tatebi_type == "*"
    assert wire.tategyoku_id is None


# ---------------------------------------------------------------------------
# T3.1-5 不支持 order_type は引き続き reject
# ---------------------------------------------------------------------------


def test_market_if_touched_still_rejected():
    """MARKET_IF_TOUCHED → UnsupportedOrderError (Phase O3 でも立花未対応)。"""
    with pytest.raises(UnsupportedOrderError) as exc_info:
        _envelope_to_wire(
            _envelope(order_type="MARKET_IF_TOUCHED"),
            _session(),
            "pw",
        )
    assert exc_info.value.reason_code == "VENUE_UNSUPPORTED"


def test_limit_if_touched_still_rejected():
    """LIMIT_IF_TOUCHED → UnsupportedOrderError (Phase O3 でも立花未対応)。"""
    with pytest.raises(UnsupportedOrderError) as exc_info:
        _envelope_to_wire(
            _envelope(order_type="LIMIT_IF_TOUCHED"),
            _session(),
            "pw",
        )
    assert exc_info.value.reason_code == "VENUE_UNSUPPORTED"
