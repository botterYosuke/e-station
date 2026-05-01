"""C3: resolve_min_ticksize_for_issue — comprehensive yobine band coverage.

Tests every band boundary of a representative yobine table modelled after the
actual TOPIX100 yobine_code='103' table observed in production:

  band[1]  kizun_price <=  1000   yobine_tanka = 0.1 yen   (decimals=1)
  band[2]  kizun_price <=  3000   yobine_tanka = 0.5 yen   (decimals=1)
  band[3]  kizun_price <= 10000   yobine_tanka = 1.0 yen   (decimals=0)
  band[4]  kizun_price <= 30000   yobine_tanka = 5.0 yen   (decimals=0)
  cap      kizun_price <= 999999999  yobine_tanka = 50.0 yen

Acceptance criteria (Phase C §6.2 / §6.4):
- All yobine bands produce the correct min_ticksize for in-band prices
- Boundary prices (exactly at kizun_price) use that band's tick
- snapshot_price=None returns bands[0].yobine_tanka (finest-tick fallback)
- The "5379 yen SoftBank" scenario resolves to 1.0 yen (band[3])
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.exchanges.tachibana_master import (
    CLMYobineRecord,
    YobineBand,
    resolve_min_ticksize_for_issue,
    tick_size_for_price,
)


# ---------------------------------------------------------------------------
# Fixtures — representative production-like yobine table
# ---------------------------------------------------------------------------


def _band(kizun: str, tanka: str, decimals: int = 0) -> YobineBand:
    return YobineBand(
        kizun_price=Decimal(kizun),
        yobine_tanka=Decimal(tanka),
        decimals=decimals,
    )


@pytest.fixture
def topix100_yobine_table() -> dict[str, list[YobineBand]]:
    """Production-like yobine table for TOPIX100 (yobine_code='103')."""
    return {
        "103": [
            _band("1000", "0.1", decimals=1),
            _band("3000", "0.5", decimals=1),
            _band("10000", "1.0", decimals=0),
            _band("30000", "5.0", decimals=0),
            _band("999999999", "50.0", decimals=0),
        ],
    }


@pytest.fixture
def topix100_issue() -> dict:
    return {"sIssueCode": "9984", "sYobineTaniNumber": "103"}


# ---------------------------------------------------------------------------
# Band boundary tests — each band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("price_str,expected_tick", [
    # band[1]: price <= 1000 → 0.1 yen
    ("100", "0.1"),
    ("500", "0.1"),
    ("1000", "0.1"),   # exact boundary → uses this band
    # band[2]: 1000 < price <= 3000 → 0.5 yen
    ("1001", "0.5"),
    ("1500", "0.5"),
    ("2998", "0.5"),
    ("3000", "0.5"),   # exact boundary → uses this band
    # band[3]: 3000 < price <= 10000 → 1.0 yen  (SoftBank ~5379 yen)
    ("3001", "1.0"),
    ("5379", "1.0"),   # SoftBank acceptance scenario
    ("5380", "1.0"),
    ("6705", "1.0"),   # みずほ acceptance scenario
    ("10000", "1.0"),  # exact boundary → uses this band
    # band[4]: 10000 < price <= 30000 → 5.0 yen
    ("10001", "5.0"),
    ("15000", "5.0"),
    ("30000", "5.0"),
    # cap: price > 30000 → 50.0 yen
    ("30001", "50.0"),
    ("100000", "50.0"),
    ("999999999", "50.0"),
])
def test_tick_size_for_price_all_bands(
    price_str: str,
    expected_tick: str,
    topix100_yobine_table: dict,
) -> None:
    """tick_size_for_price returns correct tick for all yobine bands."""
    price = Decimal(price_str)
    result = tick_size_for_price(price, "103", topix100_yobine_table)
    assert result == Decimal(expected_tick), (
        f"price={price_str}: expected tick {expected_tick}, got {result}"
    )


# ---------------------------------------------------------------------------
# resolve_min_ticksize_for_issue — with and without snapshot price
# ---------------------------------------------------------------------------


def test_resolve_with_snapshot_price_softbank(topix100_yobine_table, topix100_issue):
    """5379 yen → band[3] → 1.0 yen tick (Phase C acceptance scenario)."""
    tick = resolve_min_ticksize_for_issue(
        topix100_issue, topix100_yobine_table, snapshot_price=Decimal("5379")
    )
    assert tick == Decimal("1.0")


def test_resolve_with_snapshot_price_mizuho(topix100_yobine_table, topix100_issue):
    """6705 yen → band[3] → 1.0 yen tick."""
    tick = resolve_min_ticksize_for_issue(
        topix100_issue, topix100_yobine_table, snapshot_price=Decimal("6705")
    )
    assert tick == Decimal("1.0")


def test_resolve_with_snapshot_price_ntt(topix100_yobine_table, topix100_issue):
    """151.9 yen NTT → band[1] → 0.1 yen tick."""
    tick = resolve_min_ticksize_for_issue(
        topix100_issue, topix100_yobine_table, snapshot_price=Decimal("151.9")
    )
    assert tick == Decimal("0.1")


def test_resolve_with_snapshot_price_toyota(topix100_yobine_table, topix100_issue):
    """2998 yen Toyota → band[2] → 0.5 yen tick."""
    tick = resolve_min_ticksize_for_issue(
        topix100_issue, topix100_yobine_table, snapshot_price=Decimal("2998")
    )
    assert tick == Decimal("0.5")


def test_resolve_none_snapshot_returns_finest_tick(topix100_yobine_table, topix100_issue):
    """snapshot_price=None → bands[0].yobine_tanka (finest tick = 0.1)."""
    tick = resolve_min_ticksize_for_issue(
        topix100_issue, topix100_yobine_table, snapshot_price=None
    )
    assert tick == Decimal("0.1")


def test_resolve_unknown_yobine_code_raises_keyerror(topix100_yobine_table):
    issue = {"sIssueCode": "9999", "sYobineTaniNumber": "999"}
    with pytest.raises(KeyError):
        resolve_min_ticksize_for_issue(
            issue, topix100_yobine_table, snapshot_price=Decimal("5000")
        )


def test_resolve_empty_yobine_table():
    issue = {"sIssueCode": "9984", "sYobineTaniNumber": "103"}
    with pytest.raises(KeyError):
        resolve_min_ticksize_for_issue(issue, {}, snapshot_price=Decimal("5000"))


# ---------------------------------------------------------------------------
# Multi-code table — different yobine codes resolve independently
# ---------------------------------------------------------------------------


@pytest.fixture
def multi_code_table() -> dict[str, list[YobineBand]]:
    return {
        "00": [
            _band("3000", "1"),
            _band("5000", "5"),
            _band("999999999", "10"),
        ],
        "10": [
            _band("3000", "10"),
            _band("999999999", "100"),
        ],
    }


def test_resolve_selects_correct_code_00(multi_code_table):
    issue = {"sYobineTaniNumber": "00"}
    tick = resolve_min_ticksize_for_issue(issue, multi_code_table, snapshot_price=Decimal("4500"))
    assert tick == Decimal("5")


def test_resolve_selects_correct_code_10(multi_code_table):
    issue = {"sYobineTaniNumber": "10"}
    # 2000 <= 3000 → band[0] tanka=10
    tick = resolve_min_ticksize_for_issue(issue, multi_code_table, snapshot_price=Decimal("2000"))
    assert tick == Decimal("10")


# ---------------------------------------------------------------------------
# Acceptance: Python output is positive after normalize + resolve
# ---------------------------------------------------------------------------


def test_softbank_5379_normalize_output_positive(topix100_yobine_table, topix100_issue):
    """End-to-end: resolve tick → normalize depth → all prices positive.

    This is the 'alternating zeros' acceptance scenario from Phase C §6.2.
    """
    from engine.exchanges.normalize import normalize_depth_levels

    # Resolve correct tick for 5379 yen stock
    snapshot_price = Decimal("5379")
    tick = resolve_min_ticksize_for_issue(
        topix100_issue, topix100_yobine_table, snapshot_price=snapshot_price
    )
    assert tick == Decimal("1.0")

    # Normalize typical 10-level depth for SoftBank ~5379 yen
    bids = [{"price": str(5379 - i), "qty": str(6200)} for i in range(10)]
    asks = [{"price": str(5380 + i), "qty": str(2900)} for i in range(10)]

    norm_bids = normalize_depth_levels(bids, tick)
    norm_asks = normalize_depth_levels(asks, tick)

    for lv in norm_bids + norm_asks:
        price = Decimal(lv["price"])
        assert price > 0, f"price {price} is not positive (alternating-zeros regression)"
        assert price % tick == 0, f"price {price} not on tick={tick} grid"
