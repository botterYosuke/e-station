"""Group A: extra="forbid" テスト — OrderModifyChange / OrderRecordWire / ForgetSecondPassword"""

import pytest
from pydantic import ValidationError

from engine.schemas import ForgetSecondPassword, OrderModifyChange, OrderRecordWire


class TestOrderModifyChangeExtraForbid:
    """A-1: OrderModifyChange に未知フィールドを渡すと ValidationError が発生する。"""

    def test_unknown_field_raises_validation_error(self):
        with pytest.raises(ValidationError):
            OrderModifyChange(
                new_quantity="100",
                unknown_field="injected",  # type: ignore[call-arg]
            )

    def test_known_fields_pass(self):
        obj = OrderModifyChange(
            new_quantity="100",
            new_price="3500",
        )
        assert obj.new_quantity == "100"
        assert obj.new_price == "3500"


class TestOrderRecordWireExtraForbid:
    """A-2: OrderRecordWire に未知フィールドを渡すと ValidationError が発生する。"""

    _base = {
        "venue_order_id": "V001",
        "instrument_id": "7203.TSE",
        "order_side": "BUY",
        "order_type": "LIMIT",
        "quantity": "100",
        "filled_qty": "0",
        "leaves_qty": "100",
        "time_in_force": "DAY",
        "status": "ACCEPTED",
        "ts_event_ms": 1700000000000,
    }

    def test_unknown_field_raises_validation_error(self):
        data = {**self._base, "injected_field": "evil"}
        with pytest.raises(ValidationError):
            OrderRecordWire(**data)

    def test_known_fields_pass(self):
        obj = OrderRecordWire(**self._base)
        assert obj.venue_order_id == "V001"


class TestForgetSecondPasswordExtraForbid:
    """A-3: ForgetSecondPassword に未知フィールドを渡すと ValidationError が発生する。"""

    def test_unknown_field_raises_validation_error(self):
        with pytest.raises(ValidationError):
            ForgetSecondPassword(
                op="ForgetSecondPassword",
                unknown_field="injected",  # type: ignore[call-arg]
            )

    def test_no_extra_fields_passes(self):
        obj = ForgetSecondPassword()
        assert obj.op == "ForgetSecondPassword"
