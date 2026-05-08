"""H-1: build_replay_positions_event qty and market_value validation.

Tests that qty and market_value conversion handles malformed inputs gracefully.
Regression guard for silent failure path where fill emit stops if qty is invalid.
"""

from __future__ import annotations

from engine.server import build_replay_positions_event


def test_build_replay_positions_event_handles_normal_qty() -> None:
    """qty='100' converts to '100' correctly."""
    portfolio_state = {
        "cash": "10000",
        "positions": {
            "INSTR001": {"qty": "100", "cost": "50"}
        },
        "last_prices": {
            "INSTR001": "60"
        }
    }
    result = build_replay_positions_event(portfolio_state, ts_ms=1000)
    assert result["event"] == "PositionsUpdated"
    assert len(result["positions"]) == 1
    pos = result["positions"][0]
    assert pos["qty"] == "100"
    assert pos["market_value"] == "6000"  # 100 * 60


def test_build_replay_positions_event_handles_decimal_qty_truncates() -> None:
    """qty='100.5' truncates to '100'."""
    portfolio_state = {
        "cash": "10000",
        "positions": {
            "INSTR002": {"qty": "100.5", "cost": "50"}
        },
        "last_prices": {
            "INSTR002": "60"
        }
    }
    result = build_replay_positions_event(portfolio_state, ts_ms=1000)
    pos = result["positions"][0]
    assert pos["qty"] == "100"
    assert pos["market_value"] == "6000"


def test_build_replay_positions_event_handles_malformed_qty() -> None:
    """qty='invalid' / 'N/A' fallback to '0'; empty string qty stays '0'."""
    test_cases = [
        ("invalid", "0", "0"),  # qty, expected_qty, expected_market_value
        ("N/A", "0", "0"),       # malformed qty → qty="0" → market_value=0*60="0"
    ]
    for bad_qty, expected_qty, expected_mv in test_cases:
        portfolio_state = {
            "cash": "10000",
            "positions": {
                "INSTR003": {"qty": bad_qty, "cost": "50"}
            },
            "last_prices": {
                "INSTR003": "60"
            }
        }
        result = build_replay_positions_event(portfolio_state, ts_ms=1000)
        pos = result["positions"][0]
        assert pos["qty"] == expected_qty, f"Failed for qty={bad_qty}"
        assert pos["market_value"] == expected_mv, f"market_value mismatch for qty={bad_qty}"

    # Special case: empty string qty (handled by the "qty not in ("", None)" check)
    portfolio_state = {
        "cash": "10000",
        "positions": {
            "INSTR003": {"qty": "", "cost": "50"}
        },
        "last_prices": {
            "INSTR003": "60"
        }
    }
    result = build_replay_positions_event(portfolio_state, ts_ms=1000)
    pos = result["positions"][0]
    assert pos["qty"] == "0"  # Empty string → "0"
    assert pos["market_value"] == "0"  # 0 * 60 = 0


def test_build_replay_positions_event_handles_malformed_market_value() -> None:
    """market_value conversion handles None and invalid price."""
    # Case 1: market_value = None (existing behavior)
    portfolio_state = {
        "cash": "10000",
        "positions": {
            "INSTR004": {"qty": "100", "cost": "50"}
        },
        "last_prices": {}  # No last_price for INSTR004
    }
    result = build_replay_positions_event(portfolio_state, ts_ms=1000)
    pos = result["positions"][0]
    assert pos["qty"] == "100"
    assert pos["market_value"] == ""

    # Case 2: market_value = "invalid" (new protection)
    portfolio_state = {
        "cash": "10000",
        "positions": {
            "INSTR005": {"qty": "100", "cost": "50"}
        },
        "last_prices": {
            "INSTR005": "invalid_price"
        }
    }
    result = build_replay_positions_event(portfolio_state, ts_ms=1000)
    pos = result["positions"][0]
    assert pos["qty"] == "100"
    assert pos["market_value"] == "0"  # Fallback to "0" on invalid price


__all__ = [
    "test_build_replay_positions_event_handles_normal_qty",
    "test_build_replay_positions_event_handles_decimal_qty_truncates",
    "test_build_replay_positions_event_handles_malformed_qty",
    "test_build_replay_positions_event_handles_malformed_market_value",
]
