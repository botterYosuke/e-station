"""
RED phase tests for examples/wandb/pii_scrub.py.

Tests are written BEFORE the implementation (TDD).
"""
import sys
from pathlib import Path

import pytest

EXAMPLES_WANDB = Path(__file__).resolve().parent.parent
if str(EXAMPLES_WANDB) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_WANDB))

from pii_scrub import (
    EQUITY_ALLOWED_KEYS,
    FILLS_ALLOWED_KEYS,
    NARRATIVE_ALLOWED_KEYS,
    assert_no_forbidden_keys,
    pii_scrub,
)

# Backwards-compat alias for legacy tests in this file.
scrub = pii_scrub


# ---------------------------------------------------------------------------
# 1. scrub removes forbidden keys
# ---------------------------------------------------------------------------

def test_scrub_removes_forbidden_keys():
    event = {
        "symbol": "1301.TSE",
        "side": "BUY",
        "qty": 100,
        "price": 500.0,
        "ts": "2025-01-06T09:00:00Z",
        "pnl": 0.0,
        "account_id": "SECRET-ACCOUNT-123",   # forbidden
        "token": "top-secret-token",           # forbidden
        "venue_raw": {"raw": "payload"},       # forbidden
    }
    result = scrub(event, FILLS_ALLOWED_KEYS)
    assert "account_id" not in result
    assert "token" not in result
    assert "venue_raw" not in result


# ---------------------------------------------------------------------------
# 2. scrub allows permitted keys
# ---------------------------------------------------------------------------

def test_scrub_allows_permitted_keys():
    event = {
        "symbol": "1301.TSE",
        "side": "BUY",
        "qty": 100,
        "price": 500.0,
        "ts": "2025-01-06T09:00:00Z",
        "pnl": 1234.5,
    }
    result = scrub(event, FILLS_ALLOWED_KEYS)
    assert result == event


# ---------------------------------------------------------------------------
# 3. assert_no_forbidden_keys raises ValueError on forbidden keys
# ---------------------------------------------------------------------------

def test_assert_no_forbidden_keys_raises_on_forbidden():
    event = {
        "symbol": "1301.TSE",
        "account_id": "SECRET-123",  # forbidden
    }
    with pytest.raises(ValueError, match="forbidden keys"):
        assert_no_forbidden_keys(event, FILLS_ALLOWED_KEYS)


# ---------------------------------------------------------------------------
# 4. assert_no_forbidden_keys passes on clean event
# ---------------------------------------------------------------------------

def test_assert_no_forbidden_keys_passes_on_clean():
    event = {
        "symbol": "1301.TSE",
        "side": "BUY",
        "qty": 100,
        "price": 500.0,
        "ts": "2025-01-06T09:00:00Z",
        "pnl": 0.0,
    }
    # Must not raise
    assert_no_forbidden_keys(event, FILLS_ALLOWED_KEYS)


# ---------------------------------------------------------------------------
# Extra: allowed key sets contain only expected keys
# ---------------------------------------------------------------------------

def test_fills_allowed_keys_content():
    # Minimum required keys per spec (unified decision 47).
    # The implementation may include additional alias keys (e.g. ts_event_ms).
    spec_minimum = frozenset(["symbol", "side", "qty", "price", "ts", "pnl"])
    assert spec_minimum.issubset(FILLS_ALLOWED_KEYS), (
        f"FILLS_ALLOWED_KEYS is missing required keys: {spec_minimum - FILLS_ALLOWED_KEYS}"
    )


def test_equity_allowed_keys_content():
    # H1: unified allow-list (engine canonical). pnl was intentionally removed
    # from EQUITY (computed downstream from equity/cash deltas, not transmitted).
    spec_minimum = frozenset(["ts", "equity", "cash", "position"])
    assert spec_minimum.issubset(EQUITY_ALLOWED_KEYS), (
        f"EQUITY_ALLOWED_KEYS is missing required keys: {spec_minimum - EQUITY_ALLOWED_KEYS}"
    )


def test_narrative_allowed_keys_content():
    spec_minimum = frozenset(["ts", "message", "tags"])
    assert spec_minimum.issubset(NARRATIVE_ALLOWED_KEYS), (
        f"NARRATIVE_ALLOWED_KEYS is missing required keys: {spec_minimum - NARRATIVE_ALLOWED_KEYS}"
    )


def test_scrub_equity_event():
    event = {
        "ts": "2025-01-06T09:00:00Z",
        "equity": 1_000_000.0,
        "cash": 800_000.0,
        "position": 200,
        "pnl": 5000.0,
        "venue_raw": "should be removed",
    }
    result = scrub(event, EQUITY_ALLOWED_KEYS)
    assert "venue_raw" not in result
    assert result["equity"] == 1_000_000.0


def test_scrub_narrative_event():
    event = {
        "ts": "2025-01-06T09:00:00Z",
        "message": "Bought 100 shares",
        "tags": ["buy", "momentum"],
        "account_id": "FORBIDDEN",
    }
    result = scrub(event, NARRATIVE_ALLOWED_KEYS)
    assert "account_id" not in result
    assert result["message"] == "Bought 100 shares"
