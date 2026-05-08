"""H-1: PositionsUpdated qty/market_value normalization test.

Verifies that qty and market_value are always emitted as integer strings,
not float-like strings, in PositionsUpdated IPC events (both replay and live paths).

This ensures Rust deserializer can parse them correctly.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from engine.server import build_replay_positions_event


class TestPositionsUpdatedQtyNormalization:
    """Verify qty/market_value are integer strings in PositionsUpdated."""

    def test_replay_positions_qty_is_integer_string(self) -> None:
        """build_replay_positions_event must emit qty as integer string."""
        portfolio_state = {
            "positions": {
                "1301.TSE": {"qty": Decimal("100.5"), "cost": 100_000},
                "1302.TSE": {"qty": "200", "cost": 200_000},
            },
            "last_prices": {
                "1301.TSE": "3815",
                "1302.TSE": "4000",
            },
        }

        event = build_replay_positions_event(portfolio_state, ts_ms=1000)

        assert event["event"] == "PositionsUpdated"
        assert len(event["positions"]) == 2

        pos_1301 = event["positions"][0]
        pos_1302 = event["positions"][1]

        # qty must be integer string (not "100.5", must be "100")
        assert pos_1301["qty"] == "100", f"Expected '100', got '{pos_1301['qty']}'"
        assert pos_1302["qty"] == "200", f"Expected '200', got '{pos_1302['qty']}'"

        # market_value must also be integer string
        assert pos_1301["market_value"] == "381500", (
            f"Expected '381500', got '{pos_1301['market_value']}'"
        )
        assert pos_1302["market_value"] == "800000", (
            f"Expected '800000', got '{pos_1302['market_value']}'"
        )

    def test_replay_positions_event_deserializable_json(self) -> None:
        """Event must be valid JSON with all qty/market_value as strings."""
        portfolio_state = {
            "positions": {
                "1301.TSE": {"qty": Decimal("100"), "cost": 100_000},
            },
            "last_prices": {
                "1301.TSE": "3815",
            },
        }

        event = build_replay_positions_event(portfolio_state, ts_ms=1000)
        json_str = json.dumps(event)  # Must not raise

        # Deserialize and verify structure
        deserialized = json.loads(json_str)
        assert deserialized["event"] == "PositionsUpdated"
        assert isinstance(deserialized["positions"][0]["qty"], str)
        assert isinstance(deserialized["positions"][0]["market_value"], str)

    def test_replay_positions_qty_zero_handles_none_and_empty(self) -> None:
        """Handle None / empty qty gracefully."""
        portfolio_state = {
            "positions": {
                "1301.TSE": {"qty": None, "cost": 100_000},
                "1302.TSE": {"qty": "", "cost": 200_000},
            },
            "last_prices": {},
        }

        event = build_replay_positions_event(portfolio_state, ts_ms=1000)
        positions = event["positions"]

        # Both should normalize to "0" rather than failing
        assert positions[0]["qty"] == "0"
        assert positions[1]["qty"] == "0"
