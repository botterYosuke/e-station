"""Tpre.2: Python → Rust IPC schema 1.3 ラウンドトリップテスト。
Python pydantic モデルが Rust serde と同じ shape を出力することを確認。
"""

import json

import pytest
from engine.schemas import (
    SCHEMA_MINOR,
    CancelAllOrders,
    CancelOrder,
    ForgetSecondPassword,
    GetOrderList,
    ModifyOrder,
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderListFilter,
    OrderModifyChange,
    OrderPendingCancel,
    OrderPendingUpdate,
    OrderRejected,
    OrderSubmitted,
    SecondPasswordRequired,
    SetSecondPassword,
    SubmitOrder,
    SubmitOrderRequest,
)


# ── Schema version ────────────────────────────────────────────────────────────

def test_schema_minor_is_at_least_3():
    """SCHEMA_MINOR was 3 at schema 1.3; bumped to 4 in schema 1.4."""
    assert SCHEMA_MINOR >= 3


# ── Commands ──────────────────────────────────────────────────────────────────

def test_set_second_password_serializes():
    cmd = SetSecondPassword(request_id="req-001", value="secret")
    d = json.loads(cmd.model_dump_json())
    assert d["op"] == "SetSecondPassword"
    assert d["value"] == "secret"
    assert d["request_id"] == "req-001"


def test_forget_second_password_serializes():
    cmd = ForgetSecondPassword()
    d = json.loads(cmd.model_dump_json())
    assert d["op"] == "ForgetSecondPassword"


def test_submit_order_market_buy_serializes():
    req = SubmitOrderRequest(
        client_order_id="cid-001",
        instrument_id="7203.TSE",
        order_side="BUY",
        order_type="MARKET",
        quantity="100",
        price=None,
        trigger_price=None,
        trigger_type=None,
        time_in_force="DAY",
        expire_time_ns=None,
        post_only=False,
        reduce_only=False,
        tags=["cash_margin=cash"],
    )
    cmd = SubmitOrder(request_id="req-002", venue="tachibana", order=req)
    d = json.loads(cmd.model_dump_json())
    assert d["op"] == "SubmitOrder"
    assert d["venue"] == "tachibana"
    assert d["order"]["order_side"] == "BUY"
    assert d["order"]["order_type"] == "MARKET"
    assert d["order"]["time_in_force"] == "DAY"
    assert d["order"]["post_only"] is False
    assert "cash_margin=cash" in d["order"]["tags"]


def test_submit_order_limit_sell_serializes():
    req = SubmitOrderRequest(
        client_order_id="cid-002",
        instrument_id="9984.TSE",
        order_side="SELL",
        order_type="LIMIT",
        quantity="50",
        price="3500",
        trigger_price=None,
        trigger_type=None,
        time_in_force="DAY",
        expire_time_ns=None,
        post_only=False,
        reduce_only=False,
        tags=[],
    )
    cmd = SubmitOrder(request_id="req-003", venue="tachibana", order=req)
    d = json.loads(cmd.model_dump_json())
    assert d["order"]["order_side"] == "SELL"
    assert d["order"]["order_type"] == "LIMIT"
    assert d["order"]["price"] == "3500"


def test_modify_order_serializes():
    change = OrderModifyChange(new_price="3600")
    cmd = ModifyOrder(
        request_id="req-004",
        venue="tachibana",
        client_order_id="cid-001",
        change=change,
    )
    d = json.loads(cmd.model_dump_json())
    assert d["op"] == "ModifyOrder"
    assert d["change"]["new_price"] == "3600"


def test_cancel_order_serializes():
    cmd = CancelOrder(
        request_id="req-005",
        venue="tachibana",
        client_order_id="cid-001",
        venue_order_id="V123",
    )
    d = json.loads(cmd.model_dump_json())
    assert d["op"] == "CancelOrder"
    assert d["venue_order_id"] == "V123"


def test_cancel_all_orders_serializes():
    cmd = CancelAllOrders(
        request_id="req-006",
        venue="tachibana",
        instrument_id="7203.TSE",
        order_side="BUY",
    )
    d = json.loads(cmd.model_dump_json())
    assert d["op"] == "CancelAllOrders"
    assert d["order_side"] == "BUY"


def test_get_order_list_serializes():
    f = OrderListFilter(status="ACCEPTED")
    cmd = GetOrderList(request_id="req-007", venue="tachibana", filter=f)
    d = json.loads(cmd.model_dump_json())
    assert d["op"] == "GetOrderList"
    assert d["filter"]["status"] == "ACCEPTED"


# ── Events ────────────────────────────────────────────────────────────────────

def test_second_password_required_event():
    ev = SecondPasswordRequired(request_id="req-xyz")
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "SecondPasswordRequired"
    assert d["request_id"] == "req-xyz"


def test_order_submitted_event():
    ev = OrderSubmitted(client_order_id="cid-001", ts_event_ms=1_700_000_000_000)
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderSubmitted"
    assert d["ts_event_ms"] == 1_700_000_000_000


def test_order_accepted_event():
    ev = OrderAccepted(
        client_order_id="cid-001",
        venue_order_id="V123",
        ts_event_ms=1_700_000_000_001,
    )
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderAccepted"
    assert d["venue_order_id"] == "V123"


def test_order_rejected_event():
    ev = OrderRejected(
        client_order_id="cid-001",
        reason_code="SECOND_PASSWORD_REQUIRED",
        reason_text="",
        ts_event_ms=1_700_000_000_002,
    )
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderRejected"
    assert d["reason_code"] == "SECOND_PASSWORD_REQUIRED"


def test_order_filled_event():
    ev = OrderFilled(
        client_order_id="cid-001",
        venue_order_id="V123",
        trade_id="T001",
        last_qty="100",
        last_price="3000",
        cumulative_qty="100",
        leaves_qty="0",
        ts_event_ms=1_700_000_000_010,
    )
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderFilled"
    assert d["leaves_qty"] == "0"
    assert d["trade_id"] == "T001"


def test_order_canceled_event():
    ev = OrderCanceled(
        client_order_id="cid-001",
        venue_order_id="V123",
        ts_event_ms=1_700_000_000_020,
    )
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderCanceled"


def test_order_pending_update_event():
    ev = OrderPendingUpdate(client_order_id="cid-001", ts_event_ms=1_700_000_000_030)
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderPendingUpdate"


def test_order_pending_cancel_event():
    ev = OrderPendingCancel(client_order_id="cid-001", ts_event_ms=1_700_000_000_040)
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderPendingCancel"


def test_order_expired_event():
    ev = OrderExpired(
        client_order_id="cid-001",
        venue_order_id="V123",
        ts_event_ms=1_700_000_000_050,
    )
    d = json.loads(ev.model_dump_json())
    assert d["event"] == "OrderExpired"


# ── SubmitOrderRequest validation ─────────────────────────────────────────────

def test_submit_order_request_rejects_unknown_fields():
    """deny_unknown_fields 相当の検証 — Python 側では extra='forbid' で実現。"""
    with pytest.raises(Exception):
        SubmitOrderRequest.model_validate(
            {
                "client_order_id": "cid-x",
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
                "tags": [],
                "second_password": "must_be_rejected",
            }
        )


# ── Enum string values (SCREAMING_SNAKE_CASE) ─────────────────────────────────

@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("order_side", "BUY", "BUY"),
        ("order_side", "SELL", "SELL"),
        ("order_type", "MARKET", "MARKET"),
        ("order_type", "STOP_LIMIT", "STOP_LIMIT"),
        ("order_type", "MARKET_IF_TOUCHED", "MARKET_IF_TOUCHED"),
        ("time_in_force", "DAY", "DAY"),
        ("time_in_force", "AT_THE_OPEN", "AT_THE_OPEN"),
        ("time_in_force", "AT_THE_CLOSE", "AT_THE_CLOSE"),
    ],
)
def test_submit_request_enum_passthrough(field, value, expected):
    data = {
        "client_order_id": "cid-x",
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
        "tags": [],
    }
    data[field] = value
    req = SubmitOrderRequest.model_validate(data)
    dumped = json.loads(req.model_dump_json())
    assert dumped[field] == expected
