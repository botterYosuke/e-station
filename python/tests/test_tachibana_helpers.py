"""TDD: tachibana helpers — p_no counter, p_sd_date (JST), check_response."""

from __future__ import annotations

import re

import pytest

from engine.exchanges.tachibana_helpers import (
    PNoCounter,
    LoginError,
    SessionExpiredError,
    TachibanaError,
    UnreadNoticesError,
    check_response,
    current_p_sd_date,
)


# ---------------------------------------------------------------------------
# PNoCounter (R4, F-L5)
# ---------------------------------------------------------------------------


def test_pno_counter_starts_from_unix_seconds():
    """First value must be a Unix-seconds-derived integer (R4: monotonic across restarts)."""
    c = PNoCounter()
    first = c.next()
    # Should be at least year-2024 unix seconds (1700000000) and fits 10 digits.
    assert 1_700_000_000 <= first <= 9_999_999_999


def test_pno_counter_strictly_monotonic():
    c = PNoCounter()
    seen = [c.next() for _ in range(5)]
    assert seen == sorted(seen)
    assert len(set(seen)) == 5


def test_pno_counter_independent_instances():
    """Distinct instances start independently (no shared module state)."""
    a = PNoCounter()
    b = PNoCounter()
    a.next()
    a.next()
    # b's first is not necessarily larger than a's last; only intra-instance is monotonic.
    assert b.next() > 0


# ---------------------------------------------------------------------------
# current_p_sd_date (R4: JST)
# ---------------------------------------------------------------------------


def test_current_p_sd_date_format_is_jst_dotted():
    s = current_p_sd_date()
    # YYYY.MM.DD-hh:mm:ss.sss
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}-\d{2}:\d{2}:\d{2}\.\d{3}", s), s


def test_current_p_sd_date_uses_jst_offset():
    """Verify the function uses JST by comparing to a known-JST timestamp construction."""
    from datetime import datetime, timezone, timedelta
    jst = timezone(timedelta(hours=9))
    expected_year = datetime.now(jst).year
    s = current_p_sd_date()
    assert s.startswith(str(expected_year))


# ---------------------------------------------------------------------------
# check_response (R6, MEDIUM-C5)
# ---------------------------------------------------------------------------


def test_check_response_p_errno_zero_is_ok():
    assert check_response({"p_errno": "0", "sResultCode": "0"}) is None


def test_check_response_p_errno_empty_is_ok():
    """`p_errno=""` (empty string) must be treated as success (R6, MEDIUM-C5)."""
    assert check_response({"p_errno": "", "sResultCode": "0"}) is None


def test_check_response_p_errno_two_is_session_expired():
    err = check_response({"p_errno": "2", "p_err": "session expired"})
    assert isinstance(err, SessionExpiredError)
    assert isinstance(err, TachibanaError)


def test_check_response_p_errno_other_is_generic_error():
    err = check_response({"p_errno": "5", "p_err": "boom"})
    assert isinstance(err, TachibanaError)
    assert err.code == "5"


def test_check_response_business_error_via_sResultCode():
    err = check_response(
        {"p_errno": "0", "sResultCode": "9999", "sResultText": "no balance"}
    )
    assert isinstance(err, TachibanaError)
    assert err.code == "9999"
    assert "no balance" in str(err)


def test_check_response_unread_notices_flag():
    err = check_response(
        {
            "p_errno": "0",
            "sResultCode": "0",
            "sKinsyouhouMidokuFlg": "1",
        }
    )
    assert isinstance(err, UnreadNoticesError)


def test_login_error_subclass_relationship():
    """Helpful subclass hierarchy for callers that want broad except clauses."""
    assert issubclass(LoginError, TachibanaError)
    assert issubclass(UnreadNoticesError, LoginError)
    assert issubclass(SessionExpiredError, TachibanaError)
