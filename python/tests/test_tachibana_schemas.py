"""TDD: MarketPrice / MarketPriceHistory response normalize empty array → [].

Tachibana returns ``""`` for empty list-shaped fields (R8); the
``deserialize_tachibana_list`` path must normalize this to ``[]`` before
downstream consumers iterate. (Deferred from T1 §MEDIUM-C2-1 — see
implementation-plan.md §T4.)
"""

from __future__ import annotations

from engine.schemas import MarketPriceHistoryResponse, MarketPriceResponse


def test_market_price_response_normalizes_empty_array_field():
    parsed = MarketPriceResponse.model_validate(
        {
            "sCLMID": "CLMMfdsGetMarketPrice",
            "sResultCode": "0",
            "aCLMMfdsMarketPrice": "",
        }
    )
    assert parsed.aCLMMfdsMarketPrice == []


def test_market_price_response_keeps_real_array_field():
    parsed = MarketPriceResponse.model_validate(
        {
            "sCLMID": "CLMMfdsGetMarketPrice",
            "sResultCode": "0",
            "aCLMMfdsMarketPrice": [{"sIssueCode": "7203"}],
        }
    )
    assert parsed.aCLMMfdsMarketPrice == [{"sIssueCode": "7203"}]


def test_market_price_history_response_normalizes_empty_array_field():
    parsed = MarketPriceHistoryResponse.model_validate(
        {
            "sCLMID": "CLMMfdsGetMarketPriceHistory",
            "sResultCode": "0",
            "aCLMMfdsMarketPriceHistoryData": "",
        }
    )
    assert parsed.aCLMMfdsMarketPriceHistoryData == []


def test_market_price_history_response_keeps_real_array_field():
    parsed = MarketPriceHistoryResponse.model_validate(
        {
            "sCLMID": "CLMMfdsGetMarketPriceHistory",
            "sResultCode": "0",
            "aCLMMfdsMarketPriceHistoryData": [{"sHizukeJikoku": "20260425"}],
        }
    )
    assert len(parsed.aCLMMfdsMarketPriceHistoryData) == 1
