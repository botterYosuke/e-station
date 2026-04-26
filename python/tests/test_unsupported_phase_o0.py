"""TDD Red → Green: Phase O0 order restriction guard.

T0.3 受け入れテスト D3-2: check_phase_o0_order() が 7 条件すべてで
UNSUPPORTED_IN_PHASE_O0 を返すこと、および通過条件で None を返すこと。

条件:
  (a) order_type   — MARKET 通過 / LIMIT 拒否 / STOP_MARKET 拒否
  (b) order_side   — BUY 通過  / SELL 拒否
  (c) time_in_force — DAY 通過  / IOC 拒否 / GTC 拒否
  (d) tags         — cash_margin=cash 通過 / cash_margin=margin 拒否 / tags 無し拒否
  (e) trigger_type — null 通過  / LAST 拒否 / BID_ASK 拒否
  (f) post_only    — false 通過 / true 拒否
  (g) reduce_only  — false 通過 / true 拒否
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.exchanges.tachibana_orders import (
    UnsupportedOrderError,
    check_phase_o0_order,
)
from engine.schemas import SubmitOrderRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_VALID: dict[str, Any] = {
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


def _order(**overrides: Any) -> SubmitOrderRequest:
    return SubmitOrderRequest(**{**_BASE_VALID, **overrides})


# ---------------------------------------------------------------------------
# (a) order_type
# ---------------------------------------------------------------------------


def test_order_type_market_passes():
    assert check_phase_o0_order(_order(order_type="MARKET")) is None


@pytest.mark.parametrize("order_type", ["LIMIT", "STOP_MARKET", "STOP_LIMIT"])
def test_order_type_non_market_rejected(order_type: str):
    result = check_phase_o0_order(_order(order_type=order_type))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# (b) order_side
# ---------------------------------------------------------------------------


def test_order_side_buy_passes():
    assert check_phase_o0_order(_order(order_side="BUY")) is None


def test_order_side_sell_rejected():
    result = check_phase_o0_order(_order(order_side="SELL"))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# (c) time_in_force
# ---------------------------------------------------------------------------


def test_time_in_force_day_passes():
    assert check_phase_o0_order(_order(time_in_force="DAY")) is None


@pytest.mark.parametrize("tif", ["IOC", "GTC", "GTD", "FOK"])
def test_time_in_force_non_day_rejected(tif: str):
    result = check_phase_o0_order(_order(time_in_force=tif))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# (d) tags — cash_margin
# ---------------------------------------------------------------------------


def test_tags_cash_margin_cash_passes():
    assert check_phase_o0_order(_order(tags=["cash_margin=cash"])) is None


def test_tags_cash_margin_margin_rejected():
    result = check_phase_o0_order(_order(tags=["cash_margin=margin"]))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


def test_tags_no_cash_margin_rejected():
    result = check_phase_o0_order(_order(tags=[]))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


def test_tags_extra_tags_with_cash_passes():
    assert (
        check_phase_o0_order(_order(tags=["cash_margin=cash", "account_type=specific"]))
        is None
    )


# ---------------------------------------------------------------------------
# (e) trigger_type
# ---------------------------------------------------------------------------


def test_trigger_type_none_passes():
    assert check_phase_o0_order(_order(trigger_type=None)) is None


@pytest.mark.parametrize("trigger_type", ["LAST", "BID_ASK", "INDEX"])
def test_trigger_type_non_null_rejected(trigger_type: str):
    result = check_phase_o0_order(_order(trigger_type=trigger_type))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# (f) post_only
# ---------------------------------------------------------------------------


def test_post_only_false_passes():
    assert check_phase_o0_order(_order(post_only=False)) is None


def test_post_only_true_rejected():
    result = check_phase_o0_order(_order(post_only=True))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# (g) reduce_only
# ---------------------------------------------------------------------------


def test_reduce_only_false_passes():
    assert check_phase_o0_order(_order(reduce_only=False)) is None


def test_reduce_only_true_rejected():
    result = check_phase_o0_order(_order(reduce_only=True))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# Combined valid order passes all checks
# ---------------------------------------------------------------------------


def test_valid_cash_market_buy_passes_all():
    """全条件を満たす基準発注がすべてのチェックをパスすること。"""
    assert check_phase_o0_order(_order()) is None
