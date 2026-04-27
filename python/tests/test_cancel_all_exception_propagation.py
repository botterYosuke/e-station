"""Group E: cancel_all_orders での例外伝播テスト。

E-1: SecondPasswordInvalidError / SessionExpiredError が
     cancel_all_orders ループ外に propagate されること。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana_helpers import SecondPasswordInvalidError, SessionExpiredError
from engine.exchanges.tachibana_orders import OrderRecordWire


def _make_records() -> list[OrderRecordWire]:
    """取消可能な注文レコード 1 件を返す。"""
    return [
        OrderRecordWire(
            client_order_id=None,
            venue_order_id="V001",
            instrument_id="7203.TSE",
            order_side="BUY",
            order_type="LIMIT",
            quantity="100",
            filled_qty="0",
            leaves_qty="100",
            price=None,
            time_in_force="DAY",
            status="ACCEPTED",
            ts_event_ms=1700000000000,
        )
    ]


@pytest.mark.asyncio
async def test_second_password_invalid_propagates():
    """cancel_order が SecondPasswordInvalidError を raise したとき、
    cancel_all_orders から SecondPasswordInvalidError が propagate する。"""
    from engine.exchanges.tachibana_orders import cancel_all_orders

    session = MagicMock()

    with patch(
        "engine.exchanges.tachibana_orders.fetch_order_list",
        new_callable=AsyncMock,
        return_value=_make_records(),
    ), patch(
        "engine.exchanges.tachibana_orders.cancel_order",
        new_callable=AsyncMock,
        side_effect=SecondPasswordInvalidError("invalid"),
    ):
        with pytest.raises(SecondPasswordInvalidError):
            await cancel_all_orders(
                session=session,
                second_password="wrong",
            )


@pytest.mark.asyncio
async def test_session_expired_propagates():
    """cancel_order が SessionExpiredError を raise したとき、
    cancel_all_orders から SessionExpiredError が propagate する。"""
    from engine.exchanges.tachibana_orders import cancel_all_orders

    session = MagicMock()

    with patch(
        "engine.exchanges.tachibana_orders.fetch_order_list",
        new_callable=AsyncMock,
        return_value=_make_records(),
    ), patch(
        "engine.exchanges.tachibana_orders.cancel_order",
        new_callable=AsyncMock,
        side_effect=SessionExpiredError("expired"),
    ):
        with pytest.raises(SessionExpiredError):
            await cancel_all_orders(
                session=session,
                second_password="pass",
            )
