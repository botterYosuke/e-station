"""TDD Red → Green: Phase O0 / O3 order restriction guard.

T0.3 受け入れテスト D3-2 (Phase O0/O3 更新版):
check_phase_o0_order() の境界値テスト。

Phase O3 で解禁された種別:
  - order_type: STOP_MARKET, STOP_LIMIT（逆指値）
  - order_side: SELL（売注文）
  - time_in_force: GTD（期日指定）
  - tags: 全 cash_margin 値（cash / margin_credit_new 等）

引き続き拒否（立花未対応）:
  - MARKET_IF_TOUCHED / LIMIT_IF_TOUCHED
  - GTC / IOC / FOK
  - trigger_type != LAST / 逆指値以外で trigger_type 設定
  - post_only=True / reduce_only=True

条件:
  (a) order_type   — MARKET/LIMIT/STOP_MARKET/STOP_LIMIT 通過
                   / MARKET_IF_TOUCHED/LIMIT_IF_TOUCHED 拒否
  (b) order_side   — BUY/SELL 通過
  (c) time_in_force — DAY/GTD 通過 / IOC/GTC/FOK 拒否
  (d) tags         — 有効 cash_margin 通過 / tags 無し拒否
  (e) trigger_type — null/LAST 通過（逆指値時） / BID_ASK 拒否
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
# (a) order_type — Phase O3 解禁: LIMIT / STOP_MARKET / STOP_LIMIT が通過
# ---------------------------------------------------------------------------


def test_order_type_market_passes():
    assert check_phase_o0_order(_order(order_type="MARKET")) is None


def test_order_type_limit_passes():
    """Phase O3: LIMIT は通過（_envelope_to_wire で写像）。"""
    assert check_phase_o0_order(_order(order_type="LIMIT", price="2000")) is None


def test_order_type_stop_market_passes():
    """Phase O3: STOP_MARKET は通過（_envelope_to_wire で逆指値写像）。"""
    assert (
        check_phase_o0_order(
            _order(order_type="STOP_MARKET", trigger_price="2400", trigger_type="LAST")
        )
        is None
    )


def test_order_type_stop_limit_passes():
    """Phase O3: STOP_LIMIT は通過。"""
    assert (
        check_phase_o0_order(
            _order(
                order_type="STOP_LIMIT",
                price="2500",
                trigger_price="2400",
                trigger_type="LAST",
            )
        )
        is None
    )


@pytest.mark.parametrize("order_type", ["MARKET_IF_TOUCHED", "LIMIT_IF_TOUCHED"])
def test_order_type_if_touched_rejected(order_type: str):
    """立花未対応の IF_TOUCHED 種は Phase O3 でも拒否。"""
    result = check_phase_o0_order(_order(order_type=order_type))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# (b) order_side — Phase O3 解禁: SELL が通過
# ---------------------------------------------------------------------------


def test_order_side_buy_passes():
    assert check_phase_o0_order(_order(order_side="BUY")) is None


def test_order_side_sell_passes():
    """Phase O3: SELL は通過。"""
    assert check_phase_o0_order(_order(order_side="SELL")) is None


# ---------------------------------------------------------------------------
# (c) time_in_force — Phase O3 解禁: GTD が通過
# ---------------------------------------------------------------------------


def test_time_in_force_day_passes():
    assert check_phase_o0_order(_order(time_in_force="DAY")) is None


def test_time_in_force_gtd_passes():
    """Phase O3: GTD は通過（_envelope_to_wire で expire_day 変換）。"""
    assert (
        check_phase_o0_order(_order(time_in_force="GTD", expire_time_ns=1_000_000_000_000_000_000))
        is None
    )


@pytest.mark.parametrize("tif", ["IOC", "GTC", "FOK"])
def test_time_in_force_unsupported_rejected(tif: str):
    """立花未対応の TimeInForce は Phase O3 でも拒否。"""
    result = check_phase_o0_order(_order(time_in_force=tif))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


# ---------------------------------------------------------------------------
# (d) tags — Phase O3 解禁: 全 cash_margin 値が通過
# ---------------------------------------------------------------------------


def test_tags_cash_margin_cash_passes():
    assert check_phase_o0_order(_order(tags=["cash_margin=cash"])) is None


@pytest.mark.parametrize("cash_margin", [
    "cash_margin=margin_credit_new",
    "cash_margin=margin_credit_repay",
    "cash_margin=margin_general_new",
    "cash_margin=margin_general_repay",
])
def test_tags_cash_margin_credit_passes(cash_margin: str):
    """Phase O3: 信用取引の cash_margin 値はすべて通過。"""
    result = check_phase_o0_order(_order(tags=[cash_margin]))
    assert result is None


def test_tags_cash_margin_unknown_rejected():
    """未知の cash_margin 値は拒否。"""
    result = check_phase_o0_order(_order(tags=["cash_margin=unknown_value"]))
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
# (e) trigger_type — LAST は逆指値時のみ通過、BID_ASK は拒否
# ---------------------------------------------------------------------------


def test_trigger_type_none_passes():
    assert check_phase_o0_order(_order(trigger_type=None)) is None


def test_trigger_type_last_with_stop_market_passes():
    """Phase O3: LAST は STOP_MARKET と組み合わせた場合のみ通過。"""
    assert (
        check_phase_o0_order(
            _order(order_type="STOP_MARKET", trigger_price="2400", trigger_type="LAST")
        )
        is None
    )


def test_trigger_type_last_without_stop_rejected():
    """LAST を通常注文（MARKET）に設定するのは拒否。"""
    result = check_phase_o0_order(_order(order_type="MARKET", trigger_type="LAST"))
    assert result == "UNSUPPORTED_IN_PHASE_O0"


@pytest.mark.parametrize("trigger_type", ["BID_ASK", "INDEX"])
def test_trigger_type_non_last_rejected(trigger_type: str):
    """LAST 以外の trigger_type は拒否（立花は LAST のみ対応）。"""
    result = check_phase_o0_order(
        _order(order_type="STOP_MARKET", trigger_price="2400", trigger_type=trigger_type)
    )
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
