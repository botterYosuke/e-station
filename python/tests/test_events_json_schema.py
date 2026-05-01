"""test_events_json_schema.py — JSON Schema validation tests for events.json (Phase A / A5).

Tests verify:
- events.json is a valid JSON Schema
- StockTicker / CryptoTicker / TickerEntry discriminated union behaviour
- Each adapter's sample dict validates (including the 'kind' field added in A4)

These are pure unit tests. No network access required.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import jsonschema
import jsonschema.validators
import pytest
import warnings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SCHEMA_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / "docs"
    / "✅python-data-engine"
    / "schemas"
    / "events.json"
)


@pytest.fixture(scope="module")
def events_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _make_validator(
    schema: dict[str, Any], full_schema: dict[str, Any]
) -> jsonschema.Validator:
    """Return a validator with $defs available for $ref resolution.

    Embeds the root $defs into the schema being validated so that nested
    $ref chains (e.g. TickerEntry → StockTicker → VenueCaps) resolve
    correctly without URL-doubling issues from the old RefResolver.
    """
    # Embed $defs at the root of the schema under test so that $ref fragments
    # like "#/$defs/VenueCaps" resolve relative to this synthetic root.
    schema_with_defs = {**schema, "$defs": full_schema.get("$defs", {})}
    return jsonschema.Draft202012Validator(schema_with_defs)


# ---------------------------------------------------------------------------
# A5-1: events.json が valid な JSON Schema である
# ---------------------------------------------------------------------------


def test_events_schema_is_valid_json_schema(events_schema: dict[str, Any]) -> None:
    """events.json must be a meta-schema-valid JSON Schema (Draft 2020-12)."""
    # jsonschema.check_schema raises SchemaError if the schema is malformed.
    jsonschema.Draft202012Validator.check_schema(events_schema)


# ---------------------------------------------------------------------------
# A5-2: StockTicker — kind="stock" が通る
# ---------------------------------------------------------------------------


def test_stock_ticker_with_kind_validates(events_schema: dict[str, Any]) -> None:
    """A minimal StockTicker with all required fields must validate."""
    instance = {
        "kind": "stock",
        "symbol": "7203",
        "min_ticksize": 1.0,
        "venue_caps": {"client_aggr_depth": True, "supports_spread_display": False},
    }
    validator = _make_validator(
        events_schema["$defs"]["StockTicker"], events_schema
    )
    validator.validate(instance)


# ---------------------------------------------------------------------------
# A5-3: CryptoTicker — kind="crypto" が通る
# ---------------------------------------------------------------------------


def test_crypto_ticker_with_kind_validates(events_schema: dict[str, Any]) -> None:
    """A minimal CryptoTicker with required fields must validate."""
    instance = {
        "kind": "crypto",
        "symbol": "BTCUSDT",
        "min_ticksize": 0.1,
        "min_qty": 0.001,
        "venue_caps": {"client_aggr_depth": False, "supports_spread_display": False},
    }
    validator = _make_validator(
        events_schema["$defs"]["CryptoTicker"], events_schema
    )
    validator.validate(instance)


# ---------------------------------------------------------------------------
# A5-4: kind なしは TickerEntry として reject される
# ---------------------------------------------------------------------------


def test_ticker_without_kind_fails(events_schema: dict[str, Any]) -> None:
    """A dict without 'kind' must fail TickerEntry validation."""
    instance = {"symbol": "7203"}
    validator = _make_validator(
        events_schema["$defs"]["TickerEntry"], events_schema
    )
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(instance)


# ---------------------------------------------------------------------------
# A5-5: tachibana list_tickers サンプル dict に kind=="stock" が含まれる
#        (adapter を mock せず dict を直接構築して検証)
# ---------------------------------------------------------------------------


def test_tachibana_list_tickers_output_has_kind(events_schema: dict[str, Any]) -> None:
    """Tachibana sample entry dict must have kind='stock' and validate as StockTicker."""
    # Replicate the dict structure that tachibana.py list_tickers builds (after A4).
    entry: dict[str, Any] = {
        "kind": "stock",
        "symbol": "7203",
        "display_name_ja": "トヨタ自動車",
        "display_symbol": "TOYOTA MOTOR",
        "min_ticksize": 1.0,
        "lot_size": 100,
        "min_qty": 100,
        "quote_currency": "JPY",
        "yobine_code": "7",
        "sizyou_c": "00",
        "venue_caps": {"client_aggr_depth": True, "supports_spread_display": False},
    }
    assert entry["kind"] == "stock"
    validator = _make_validator(
        events_schema["$defs"]["StockTicker"], events_schema
    )
    validator.validate(entry)


# ---------------------------------------------------------------------------
# A5-6: parametrize — 複数 adapter のサンプル dict が TickerEntry を通る
# ---------------------------------------------------------------------------

_VENUE_CAPS_STOCK = {"client_aggr_depth": True, "supports_spread_display": False}
_VENUE_CAPS_CRYPTO = {"client_aggr_depth": False, "supports_spread_display": False}

_VALID_TICKER_ENTRIES: list[tuple[str, dict[str, Any]]] = [
    (
        "tachibana_stock",
        {
            "kind": "stock",
            "symbol": "7203",
            "display_name_ja": "トヨタ自動車",
            "display_symbol": "TOYOTA",
            "min_ticksize": 1.0,
            "lot_size": 100,
            "min_qty": 100,
            "quote_currency": "JPY",
            "yobine_code": "7",
            "sizyou_c": "00",
            "venue_caps": _VENUE_CAPS_STOCK,
        },
    ),
    (
        "hyperliquid_perp",
        {
            "kind": "crypto",
            "symbol": "BTC",
            "min_ticksize": 0.1,
            "min_qty": 0.001,
            "venue_caps": _VENUE_CAPS_CRYPTO,
        },
    ),
    (
        "hyperliquid_spot",
        {
            "kind": "crypto",
            "symbol": "BTC/USDC:USDC",
            "display_symbol": "BTC/USDC",
            "min_ticksize": 1.0,
            "min_qty": 1.0,
            "venue_caps": _VENUE_CAPS_CRYPTO,
        },
    ),
    (
        "binance_linear_perp",
        {
            "kind": "crypto",
            "symbol": "BTCUSDT",
            "min_ticksize": 0.10,
            "min_qty": 0.001,
            "venue_caps": _VENUE_CAPS_CRYPTO,
        },
    ),
    (
        "bybit_linear_perp",
        {
            "kind": "crypto",
            "symbol": "BTCUSDT",
            "min_ticksize": 0.10,
            "min_qty": 0.001,
            "venue_caps": _VENUE_CAPS_CRYPTO,
        },
    ),
    (
        "mexc_spot",
        {
            "kind": "crypto",
            "symbol": "BTCUSDT",
            "min_ticksize": 0.01,
            "min_qty": 0.0001,
            "venue_caps": _VENUE_CAPS_CRYPTO,
        },
    ),
    (
        "okex_spot",
        {
            "kind": "crypto",
            "symbol": "BTC-USDT",
            "min_ticksize": 0.1,
            "min_qty": 0.00001,
            "venue_caps": _VENUE_CAPS_CRYPTO,
        },
    ),
]


@pytest.mark.parametrize(
    "adapter_name,entry",
    _VALID_TICKER_ENTRIES,
    ids=[x[0] for x in _VALID_TICKER_ENTRIES],
)
def test_adapter_sample_entry_validates_as_ticker_entry(
    adapter_name: str,
    entry: dict[str, Any],
    events_schema: dict[str, Any],
) -> None:
    """Each adapter's sample dict must validate against TickerEntry schema."""
    validator = _make_validator(
        events_schema["$defs"]["TickerEntry"], events_schema
    )
    validator.validate(entry)


# ---------------------------------------------------------------------------
# A5-7: CryptoTicker — min_ticksize=0 (exclusiveMinimum) は reject される
# ---------------------------------------------------------------------------


def test_crypto_ticker_zero_min_ticksize_fails(events_schema: dict[str, Any]) -> None:
    """min_ticksize must be > 0 (exclusiveMinimum); 0 must be rejected."""
    instance = {
        "kind": "crypto",
        "symbol": "BTCUSDT",
        "min_ticksize": 0,
        "min_qty": 0.001,
    }
    validator = _make_validator(
        events_schema["$defs"]["CryptoTicker"], events_schema
    )
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(instance)
