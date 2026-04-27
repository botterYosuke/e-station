"""TDD: JST market-hours boundary tests (T5, plan §HIGH-D5).

Seven boundary cases at the exact transition points.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.exchanges.tachibana_ws import is_market_open

JST = timezone(timedelta(hours=9))


def _jst(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2024, 1, 5, h, m, s, tzinfo=JST)  # 2024-01-05 is a Friday


@pytest.mark.parametrize(
    "dt, expected_open",
    [
        (_jst(8, 59, 59), False),   # before open
        (_jst(9, 0, 0),   True),    # 前場 open
        (_jst(11, 30, 0), False),   # 前場 close (exclusive end)
        (_jst(12, 30, 0), True),    # 後場 open
        (_jst(15, 25, 0), True),    # クロージング・オークション start (still open)
        (_jst(15, 29, 59), True),   # last second before close
        (_jst(15, 30, 0), False),   # after close (exclusive end)
    ],
    ids=[
        "before_open",
        "morning_session_start",
        "morning_session_end",
        "afternoon_session_start",
        "closing_auction_start",
        "last_second_before_close",
        "after_close",
    ],
)
def test_market_hours_boundary(dt: datetime, expected_open: bool) -> None:
    assert is_market_open(dt) == expected_open


def test_midday_break_is_closed() -> None:
    """11:30–12:30 昼休 is outside trading hours."""
    assert not is_market_open(_jst(12, 0, 0))


def test_utc_input_is_converted_to_jst() -> None:
    """An UTC datetime equivalent to JST 09:00 should return True."""
    utc_dt = datetime(2024, 1, 5, 0, 0, 0, tzinfo=timezone.utc)  # 09:00 JST
    assert is_market_open(utc_dt)


def test_naive_datetime_treated_as_utc() -> None:
    """A naive datetime is treated as UTC (astimezone(JST) adds 9h)."""
    # 00:00 UTC naive → 09:00 JST → open
    naive = datetime(2024, 1, 5, 0, 0, 0)
    assert is_market_open(naive)
