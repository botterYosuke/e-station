"""M-6: SubmitOrderRequest enum field validation via Literal types."""
import pytest
from pydantic import ValidationError
from engine.schemas import SubmitOrderRequest

_BASE = dict(
    client_order_id="ord-001",
    instrument_id="7203.TSE",
    order_side="BUY",
    order_type="MARKET",
    quantity="100",
    time_in_force="DAY",
    post_only=False,
    reduce_only=False,
)


def test_valid_order_side_buy():
    req = SubmitOrderRequest.model_validate({**_BASE, "order_side": "BUY"})
    assert req.order_side == "BUY"


def test_valid_order_side_sell():
    req = SubmitOrderRequest.model_validate({**_BASE, "order_side": "SELL"})
    assert req.order_side == "SELL"


def test_invalid_order_side_raises():
    with pytest.raises(ValidationError):
        SubmitOrderRequest.model_validate({**_BASE, "order_side": "buy"})


def test_invalid_order_side_unknown_raises():
    with pytest.raises(ValidationError):
        SubmitOrderRequest.model_validate({**_BASE, "order_side": "LONG"})


def test_valid_order_types():
    for ot in ("MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT", "MARKET_IF_TOUCHED", "LIMIT_IF_TOUCHED"):
        req = SubmitOrderRequest.model_validate({**_BASE, "order_type": ot})
        assert req.order_type == ot


def test_invalid_order_type_raises():
    with pytest.raises(ValidationError):
        SubmitOrderRequest.model_validate({**_BASE, "order_type": "market"})


def test_valid_time_in_force_values():
    for tif in ("DAY", "GTC", "GTD", "IOC", "FOK", "AT_THE_OPEN", "AT_THE_CLOSE"):
        req = SubmitOrderRequest.model_validate({**_BASE, "time_in_force": tif})
        assert req.time_in_force == tif


def test_invalid_time_in_force_raises():
    with pytest.raises(ValidationError):
        SubmitOrderRequest.model_validate({**_BASE, "time_in_force": "day"})


def test_invalid_time_in_force_garbage_raises():
    with pytest.raises(ValidationError):
        SubmitOrderRequest.model_validate({**_BASE, "time_in_force": "FOREVER"})
