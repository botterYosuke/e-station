"""TDD: tachibana_master — CLMYobine decoder + tick_size_for_price (B1).

Per-stock tick size lookup uses ``CLMIssueSizyouMstKabu.sYobineTaniNumber``
which references a ``CLMYobine`` row whose 20 ``(sKizunPrice_N,
sYobineTanka_N, sDecimal_N)`` triples define the tick band table. The PDF
reference is api_request_if_master_v4r5.pdf §2-12 — the §2-12 section is a
*structural description* (it points at the runtime "資料_呼値" table), not
a single hardcodeable price → tick map. So fixtures here use the example
rows captured from the PDF screenshot only.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.exchanges.tachibana_master import (
    YobineBand,
    decode_clm_yobine_record,
    tick_size_for_price,
)


# ---------------------------------------------------------------------------
# CLMYobine record decoder
# ---------------------------------------------------------------------------


def _build_yobine_record(yobine_code: str, bands: list[tuple[str, str, str]]) -> dict:
    """Build a CLMYobine dict with 20 _N slots, sentinel-padded.

    ``bands`` is the meaningful prefix; we pad to 20 with the
    ``("999999999", "0", "0")`` sentinel that the spec says is always present
    in some column of the row.
    """
    record: dict[str, str] = {
        "sCLMID": "CLMYobine",
        "sYobineTaniNumber": yobine_code,
    }
    sentinel = ("999999999", "0", "0")
    padded = list(bands) + [sentinel] * (20 - len(bands))
    for i, (kizun, tanka, decimals) in enumerate(padded, start=1):
        record[f"sKizunPrice_{i}"] = kizun
        record[f"sYobineTanka_{i}"] = tanka
        record[f"sDecimal_{i}"] = decimals
    return record


def test_clm_yobine_decoder_collects_20_bands():
    """All 20 slots are read and parsed into ``YobineBand`` triples."""
    bands = [(str(1000 * i), "1", "0") for i in range(1, 21)]
    record = _build_yobine_record("999", bands)

    decoded = decode_clm_yobine_record(record)

    assert decoded.sYobineTaniNumber == "999"
    # No sentinel (999999999) in this fixture, so all 20 bands survive.
    assert len(decoded.bands) == 20
    assert decoded.bands[0] == YobineBand(
        kizun_price=Decimal("1000"),
        yobine_tanka=Decimal("1"),
        decimals=0,
    )


def test_clm_yobine_decoder_truncates_at_999999999_sentinel():
    """Bands at/after the 999999999 sentinel are kept exactly once as the cap.

    The spec says some column of the row holds 999999999; this is the
    table-end cap. We keep the 999999999 row itself (so ``price <= cap`` is
    always satisfied for legal prices) but drop trailing sentinel rows.
    """
    record = _build_yobine_record(
        "103",
        [
            ("1000", "0.1", "1"),
            ("5000", "0.5", "1"),
            ("999999999", "10", "0"),
        ],
    )

    decoded = decode_clm_yobine_record(record)

    # 3 meaningful rows + sentinel cap; trailing duplicate 999999999 sentinels
    # padded to 20 are dropped.
    assert len(decoded.bands) == 3
    assert decoded.bands[-1].kizun_price == Decimal("999999999")
    assert decoded.bands[-1].yobine_tanka == Decimal("10")


# ---------------------------------------------------------------------------
# tick_size_for_price
# ---------------------------------------------------------------------------


# PDF screenshot example rows — fixture only, not a hardcode source of truth.
_FIXTURE_TABLE: dict[str, list[YobineBand]] = {
    "101": [
        YobineBand(Decimal("3000"), Decimal("1"), 0),
        YobineBand(Decimal("5000"), Decimal("5"), 0),
        YobineBand(Decimal("999999999"), Decimal("10"), 0),
    ],
    "103": [
        YobineBand(Decimal("1000"), Decimal("0.1"), 1),
        YobineBand(Decimal("5000"), Decimal("0.5"), 1),
        YobineBand(Decimal("999999999"), Decimal("10"), 0),
    ],
    "418": [
        YobineBand(Decimal("50"), Decimal("1"), 0),
        YobineBand(Decimal("1000"), Decimal("5"), 0),
        YobineBand(Decimal("999999999"), Decimal("10"), 0),
    ],
}


@pytest.mark.parametrize(
    "code,price,expected",
    [
        # 103: boundary at 1000 (≤ → 0.1) and 5000 (≤ → 0.5)
        ("103", Decimal("999.9"), Decimal("0.1")),
        ("103", Decimal("1000"), Decimal("0.1")),
        ("103", Decimal("1000.01"), Decimal("0.5")),
        ("103", Decimal("5000"), Decimal("0.5")),
        ("103", Decimal("5000.01"), Decimal("10")),
        # 101: boundaries at 3000 / 5000
        ("101", Decimal("2999.99"), Decimal("1")),
        ("101", Decimal("3000"), Decimal("1")),
        ("101", Decimal("3000.01"), Decimal("5")),
        ("101", Decimal("5000"), Decimal("5")),
        ("101", Decimal("5000.01"), Decimal("10")),
        # 418: boundaries at 50 / 1000
        ("418", Decimal("49.99"), Decimal("1")),
        ("418", Decimal("50"), Decimal("1")),
        ("418", Decimal("50.01"), Decimal("5")),
        ("418", Decimal("1000"), Decimal("5")),
        ("418", Decimal("1000.01"), Decimal("10")),
    ],
)
def test_tick_size_for_price_uses_first_band_le_price(code, price, expected):
    assert tick_size_for_price(price, code, _FIXTURE_TABLE) == expected


def test_tick_size_for_price_unknown_yobine_code_raises_keyerror():
    with pytest.raises(KeyError):
        tick_size_for_price(Decimal("100"), "missing", _FIXTURE_TABLE)


def test_tick_size_for_price_decimal_only():
    """``price`` must be ``Decimal`` — float/int are rejected to keep the
    quantization deterministic (no binary-float drift in tick boundaries)."""
    with pytest.raises(TypeError):
        tick_size_for_price(1000.0, "103", _FIXTURE_TABLE)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tick_size_for_price(1000, "103", _FIXTURE_TABLE)  # type: ignore[arg-type]
