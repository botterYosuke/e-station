"""TDD Red → Green: T0.4 — _compose_request_payload() テスト。

CLMKabuNewOrder ペイロードに p_no / p_sd_date / sCLMID / sJsonOfmt /
逆指値デフォルトが正しく付与されることを確認する。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from engine.exchanges.tachibana_helpers import PNoCounter
from engine.exchanges.tachibana_orders import (
    TachibanaWireOrderRequest,
    _compose_request_payload,
)


def _wire() -> TachibanaWireOrderRequest:
    return TachibanaWireOrderRequest(
        account_type="1",
        issue_code="7203",
        market_code="00",
        side="3",
        condition="0",
        price="0",
        qty="100",
        cash_margin="0",
        expire_day="0",
        second_password="secret",
    )


def test_compose_payload_has_clmid():
    counter = PNoCounter()
    payload = _compose_request_payload(_wire(), counter)
    assert payload["sCLMID"] == "CLMKabuNewOrder"


def test_compose_payload_has_sjsonofmt_5():
    counter = PNoCounter()
    payload = _compose_request_payload(_wire(), counter)
    assert payload["sJsonOfmt"] == "5"


def test_compose_payload_p_no_is_string():
    counter = PNoCounter()
    payload = _compose_request_payload(_wire(), counter)
    assert isinstance(payload["p_no"], str)
    assert payload["p_no"].isdigit()


def test_compose_payload_p_no_increments():
    counter = PNoCounter()
    p1 = _compose_request_payload(_wire(), counter)["p_no"]
    p2 = _compose_request_payload(_wire(), counter)["p_no"]
    assert int(p2) == int(p1) + 1


def test_compose_payload_p_sd_date_format():
    counter = PNoCounter()
    payload = _compose_request_payload(_wire(), counter)
    p_sd = payload["p_sd_date"]
    # 形式: YYYY.MM.DD-HH:MM:SS.mmm
    assert len(p_sd) == 23, f"p_sd_date format wrong: {p_sd!r}"
    assert p_sd[4] == "." and p_sd[7] == "." and p_sd[10] == "-"


def test_compose_payload_has_default_gyakusasi_fields():
    counter = PNoCounter()
    payload = _compose_request_payload(_wire(), counter)
    assert payload["sGyakusasiOrderType"] == "0"
    assert payload["sGyakusasiZyouken"] == "0"
    assert payload["sGyakusasiPrice"] == "*"
    assert payload["sTatebiType"] == "*"
    assert payload["sTategyokuZyoutoekiKazeiC"] == "*"


def test_compose_payload_wire_fields_present():
    counter = PNoCounter()
    payload = _compose_request_payload(_wire(), counter)
    assert payload["sIssueCode"] == "7203"
    assert payload["sSizyouC"] == "00"
    assert payload["sBaibaiKubun"] == "3"
    assert payload["sOrderPrice"] == "0"
    assert payload["sOrderSuryou"] == "100"
    assert payload["sGenkinShinyouKubun"] == "0"
    assert payload["sOrderExpireDay"] == "0"
    assert payload["sZyoutoekiKazeiC"] == "1"
    assert payload["sCondition"] == "0"


def test_compose_payload_second_password_present():
    counter = PNoCounter()
    payload = _compose_request_payload(_wire(), counter)
    assert payload["sSecondPassword"] == "secret"
