"""H-2: PortfolioView.to_position_records() test.

Verifies that portfolio_view.to_position_records() returns PositionRecord-compatible
list that can be used to build PositionsUpdated IPC events in replay mode.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.nautilus.portfolio_view import PortfolioView


class TestPortfolioViewPositionRecords:
    """Verify to_position_records() returns valid PositionRecord dicts."""

    def test_to_position_records_returns_empty_when_no_positions(self) -> None:
        """Empty portfolio should return empty list."""
        pv = PortfolioView(Decimal("1000000"))
        records = pv.to_position_records()
        assert records == []

    def test_to_position_records_single_position(self) -> None:
        """Single position after buy should be in records."""
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3815"))

        records = pv.to_position_records(
            last_prices={"1301.TSE": Decimal("3815")}
        )

        assert len(records) == 1
        record = records[0]

        assert record["instrument_id"] == "1301.TSE"
        assert record["qty"] == "100"
        assert record["market_value"] == "381500"
        assert record["position_type"] == "cash"
        assert record["venue"] == "replay"
        assert record["tategyoku_id"] is None

    def test_to_position_records_multiple_positions_sorted(self) -> None:
        """Multiple positions should be sorted by instrument_id."""
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("9984.TSE", "BUY", Decimal("50"), Decimal("100"))  # Z comes later
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3815"))  # A comes first

        records = pv.to_position_records(
            last_prices={
                "1301.TSE": Decimal("3815"),
                "9984.TSE": Decimal("100"),
            }
        )

        assert len(records) == 2
        # Must be sorted by instrument_id
        assert records[0]["instrument_id"] == "1301.TSE"
        assert records[1]["instrument_id"] == "9984.TSE"

    def test_to_position_records_qty_is_integer_string(self) -> None:
        """qty must be integer string even for fractional Decimal fills."""
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100.5"), Decimal("3815"))

        records = pv.to_position_records(
            last_prices={"1301.TSE": Decimal("3815")}
        )

        # qty should be truncated to 100, not rounded/kept as float
        assert records[0]["qty"] == "100"

    def test_to_position_records_market_value_without_price(self) -> None:
        """market_value should be empty string when price is unavailable."""
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3815"))

        records = pv.to_position_records(last_prices={})

        assert records[0]["market_value"] == ""

    def test_to_position_records_fallback_to_internal_prices(self) -> None:
        """Should use internal last_prices when external prices not provided."""
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3815"))
        # on_fill updates internal _last_prices

        records = pv.to_position_records()  # No last_prices arg

        # Should use the 3815 from on_fill
        assert records[0]["market_value"] == "381500"

    def test_to_position_records_zero_qty_position_excluded(self) -> None:
        """Zero-qty positions (after sell) should not appear in records."""
        pv = PortfolioView(Decimal("1000000"))
        pv.on_fill("1301.TSE", "BUY", Decimal("100"), Decimal("3815"))
        pv.on_fill("1301.TSE", "SELL", Decimal("100"), Decimal("3825"))

        records = pv.to_position_records()

        assert records == []
