"""TDD: build_request_url enforces sCLMID → URL-type pairing (B1, MEDIUM-C7).

* MASTER_CLMIDS sCLMIDs require ``MasterUrl``
* PRICE_CLMIDS sCLMIDs require ``PriceUrl``
* Mismatches raise ``TypeError``.
"""

from __future__ import annotations

import pytest

from engine.exchanges.tachibana_master import MASTER_CLMIDS, PRICE_CLMIDS
from engine.exchanges.tachibana_url import (
    MasterUrl,
    PriceUrl,
    RequestUrl,
    build_request_url,
)


# ---------------------------------------------------------------------------
# Set membership invariants
# ---------------------------------------------------------------------------


def test_master_clmids_contains_clm_yobine():
    """CLMYobine is a CLMEventDownload sub-stream, so it lives in MASTER_CLMIDS."""
    assert "CLMYobine" in MASTER_CLMIDS


def test_price_clmids_contains_market_price_endpoints():
    assert "CLMMfdsGetMarketPrice" in PRICE_CLMIDS
    assert "CLMMfdsGetMarketPriceHistory" in PRICE_CLMIDS


def test_price_clmids_is_frozen():
    assert isinstance(PRICE_CLMIDS, frozenset)


# ---------------------------------------------------------------------------
# Type guard: MASTER_CLMIDS must use MasterUrl
# ---------------------------------------------------------------------------


def test_master_clmid_with_master_url_ok():
    url = build_request_url(
        MasterUrl("https://example.invalid/master/"),
        {"sCLMID": "CLMEventDownload"},
        sJsonOfmt="4",
    )
    assert url.startswith("https://example.invalid/master/?")


def test_master_clmid_with_request_url_raises_typeerror():
    with pytest.raises(TypeError):
        build_request_url(
            RequestUrl("https://example.invalid/req/"),
            {"sCLMID": "CLMEventDownload"},
            sJsonOfmt="4",
        )


# ---------------------------------------------------------------------------
# Type guard: PRICE_CLMIDS must use PriceUrl
# ---------------------------------------------------------------------------


def test_price_clmid_with_price_url_ok():
    url = build_request_url(
        PriceUrl("https://example.invalid/price/"),
        {"sCLMID": "CLMMfdsGetMarketPrice", "sIssueCode": "7203"},
        sJsonOfmt="5",
    )
    assert url.startswith("https://example.invalid/price/?")


def test_price_clmid_with_request_url_raises_typeerror():
    with pytest.raises(TypeError):
        build_request_url(
            RequestUrl("https://example.invalid/req/"),
            {"sCLMID": "CLMMfdsGetMarketPrice", "sIssueCode": "7203"},
            sJsonOfmt="5",
        )


def test_price_clmid_with_master_url_raises_typeerror():
    with pytest.raises(TypeError):
        build_request_url(
            MasterUrl("https://example.invalid/master/"),
            {"sCLMID": "CLMMfdsGetMarketPriceHistory", "sIssueCode": "7203"},
            sJsonOfmt="5",
        )
