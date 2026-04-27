"""OKX REST API tests — Phase 3."""

from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from engine.exchanges.okex import OkexWorker


_REST = "https://www.okx.com/api/v5"


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def worker():
    return OkexWorker()


def _instruments_spot(*items):
    return {"code": "0", "data": list(items)}


def _instruments_swap(*items):
    return {"code": "0", "data": list(items)}


def _tickers(*items):
    return {"code": "0", "data": list(items)}


def _candles(*rows):
    return {"code": "0", "data": list(rows)}


def _oi_history(*rows):
    return {"code": "0", "data": list(rows)}


def _books(bids, asks, seq_id=1000, ts="1700000000000"):
    return {
        "code": "0",
        "data": [
            {
                "bids": [[b[0], b[1], "0", "1"] for b in bids],
                "asks": [[a[0], a[1], "0", "1"] for a in asks],
                "seqId": seq_id,
                "ts": ts,
            }
        ],
    }


def _spot_item(inst_id="BTC-USDT", state="live", quote_ccy="USDT",
               tick_sz="0.1", lot_sz="0.0001"):
    return {
        "instId": inst_id,
        "state": state,
        "quoteCcy": quote_ccy,
        "tickSz": tick_sz,
        "lotSz": lot_sz,
    }


def _swap_item(inst_id="BTC-USDT-SWAP", state="live", ct_type="linear",
               settle_ccy="USDT", tick_sz="0.1", lot_sz="1", ct_val="0.001"):
    return {
        "instId": inst_id,
        "state": state,
        "ctType": ct_type,
        "settleCcy": settle_ccy,
        "tickSz": tick_sz,
        "lotSz": lot_sz,
        "ctVal": ct_val,
    }


# ---------------------------------------------------------------------------
# list_tickers — spot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tickers_spot(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SPOT",
        json=_instruments_spot(
            _spot_item("BTC-USDT"),
            _spot_item("ETH-USDT", tick_sz="0.01", lot_sz="0.0001"),
        ),
    )
    result = await worker.list_tickers("spot")
    assert len(result) == 2
    btc = next(r for r in result if r["symbol"] == "BTC-USDT")
    assert btc["min_ticksize"] == pytest.approx(0.1)
    assert btc["min_qty"] == pytest.approx(0.0001)


@pytest.mark.asyncio
async def test_list_tickers_spot_excludes_non_usdt(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SPOT",
        json=_instruments_spot(
            _spot_item("BTC-USDT"),
            _spot_item("BTC-BTC", quote_ccy="BTC"),
        ),
    )
    result = await worker.list_tickers("spot")
    assert len(result) == 1
    assert result[0]["symbol"] == "BTC-USDT"


@pytest.mark.asyncio
async def test_list_tickers_spot_excludes_non_live(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SPOT",
        json=_instruments_spot(
            _spot_item("BTC-USDT"),
            _spot_item("NEW-USDT", state="suspend"),
        ),
    )
    result = await worker.list_tickers("spot")
    assert len(result) == 1
    assert result[0]["symbol"] == "BTC-USDT"


@pytest.mark.asyncio
async def test_list_tickers_linear_perp(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SWAP",
        json=_instruments_swap(
            _swap_item("BTC-USDT-SWAP", ct_type="linear", settle_ccy="USDT", ct_val="0.001"),
            _swap_item("ETH-USDT-SWAP", ct_type="linear", settle_ccy="USDT", ct_val="0.01"),
            _swap_item("BTC-USD-SWAP", ct_type="inverse", settle_ccy="BTC"),
        ),
    )
    result = await worker.list_tickers("linear_perp")
    assert len(result) == 2
    symbols = {r["symbol"] for r in result}
    assert "BTC-USDT-SWAP" in symbols
    assert "BTC-USD-SWAP" not in symbols


@pytest.mark.asyncio
async def test_list_tickers_inverse_perp(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SWAP",
        json=_instruments_swap(
            _swap_item("BTC-USD-SWAP", ct_type="inverse", settle_ccy="BTC"),
            _swap_item("BTC-USDT-SWAP", ct_type="linear", settle_ccy="USDT"),
        ),
    )
    result = await worker.list_tickers("inverse_perp")
    assert len(result) == 1
    assert result[0]["symbol"] == "BTC-USD-SWAP"


@pytest.mark.asyncio
async def test_list_tickers_linear_excludes_non_usdt_settle(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SWAP",
        json=_instruments_swap(
            _swap_item("BTC-USDT-SWAP", ct_type="linear", settle_ccy="USDT"),
            _swap_item("XYZ-ETH-SWAP", ct_type="linear", settle_ccy="ETH"),
        ),
    )
    result = await worker.list_tickers("linear_perp")
    assert len(result) == 1
    assert result[0]["symbol"] == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_list_tickers_contract_size_set_for_perps(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SWAP",
        json=_instruments_swap(_swap_item("BTC-USDT-SWAP", ct_val="0.001")),
    )
    result = await worker.list_tickers("linear_perp")
    assert len(result) == 1
    assert result[0]["contract_size"] == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_list_tickers_spot_contract_size_is_none(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/public/instruments?instType=SPOT",
        json=_instruments_spot(_spot_item()),
    )
    result = await worker.list_tickers("spot")
    assert result[0]["contract_size"] is None


# ---------------------------------------------------------------------------
# fetch_klines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_spot(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r".*/market/history-candles.*"),
        json=_candles(
            ["1700000060000", "30000", "30100", "29900", "30050", "10.5", "0", "0", "1"],
            ["1700000000000", "29900", "30000", "29800", "30000", "8.2", "0", "0", "1"],
        ),
    )
    result = await worker.fetch_klines("BTC-USDT", "spot", "1m", limit=2)
    assert len(result) == 2
    # Sorted ascending
    assert result[0]["open_time_ms"] <= result[1]["open_time_ms"]
    assert result[0]["open"] == "29900"
    assert result[0]["close"] == "30000"
    assert result[0]["volume"] == "8.2"


@pytest.mark.asyncio
async def test_fetch_klines_with_range(worker, httpx_mock: HTTPXMock):
    start_ms = 1700000000000
    end_ms = 1700003600000
    httpx_mock.add_response(
        url=re.compile(r".*/market/history-candles.*"),
        json=_candles(
            ["1700003600000", "30100", "30200", "30000", "30150", "5.0", "0", "0", "1"],
        ),
    )
    await worker.fetch_klines("BTC-USDT", "spot", "1h", start_ms=start_ms, end_ms=end_ms)
    # Verify URL had before/after parameters
    req_url = str(httpx_mock.get_requests()[0].url)
    assert "before=" in req_url
    assert "after=" in req_url


@pytest.mark.asyncio
async def test_fetch_klines_unknown_timeframe_raises(worker):
    with pytest.raises(ValueError, match="unsupported.*timeframe"):
        await worker.fetch_klines("BTC-USDT", "spot", "9m")


@pytest.mark.asyncio
async def test_fetch_klines_clamps_limit_to_okx_max(worker, httpx_mock: HTTPXMock):
    """Bug #1: limit > 300 must be clamped to 300 (OKX max for /market/history-candles)."""
    httpx_mock.add_response(
        url=re.compile(r".*/market/history-candles.*"),
        json=_candles(
            ["1700000000000", "30000", "30100", "29900", "30050", "10.5", "0", "0", "1"],
        ),
    )
    await worker.fetch_klines("BTC-USDT", "spot", "1m", limit=400)
    req_url = str(httpx_mock.get_requests()[0].url)
    assert "limit=300" in req_url, f"Expected limit=300 in URL, got: {req_url}"
    assert "limit=400" not in req_url


@pytest.mark.asyncio
async def test_fetch_klines_is_closed_from_confirm_field(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r".*/market/history-candles.*"),
        json=_candles(
            # confirm=0 → is_closed=False, newer time
            ["1700000060000", "30000", "30100", "29900", "30050", "10.5", "0", "0", "0"],
            # confirm=1 → is_closed=True, older time
            ["1699999940000", "29900", "30000", "29800", "30000", "8.2", "0", "0", "1"],
        ),
    )
    result = await worker.fetch_klines("BTC-USDT", "spot", "1m", limit=2)
    # sorted ascending: older first → closed first
    assert result[0]["is_closed"] is True
    assert result[1]["is_closed"] is False


# ---------------------------------------------------------------------------
# fetch_open_interest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_interest_linear(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r".*/rubik/stat/contracts/open-interest-history.*"),
        json=_oi_history(
            ["1700000000000", "1000", "500"],
            ["1699996400000", "1100", "550"],
        ),
    )
    result = await worker.fetch_open_interest("BTC-USDT-SWAP", "linear_perp", "1h")
    assert len(result) == 2
    assert result[0]["ts_ms"] == 1700000000000
    assert result[0]["open_interest"] == "500"


@pytest.mark.asyncio
async def test_fetch_open_interest_spot_returns_empty(worker):
    result = await worker.fetch_open_interest("BTC-USDT", "spot", "1h")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_open_interest_unknown_timeframe_raises(worker):
    with pytest.raises(ValueError, match="unsupported.*timeframe"):
        await worker.fetch_open_interest("BTC-USDT-SWAP", "linear_perp", "2m")


# ---------------------------------------------------------------------------
# fetch_ticker_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ticker_stats_spot(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/market/tickers?instType=SPOT",
        json=_tickers(
            {"instId": "BTC-USDT", "last": "30000", "open24h": "29000", "volCcy24h": "500000"}
        ),
    )
    result = await worker.fetch_ticker_stats("BTC-USDT", "spot")
    assert result["mark_price"] == "30000"
    # daily_price_chg = (30000 - 29000) / 29000 * 100
    assert float(result["daily_price_chg"]) == pytest.approx(3.4482758, rel=1e-3)
    # spot: daily_volume = volCcy24h (already USD)
    assert float(result["daily_volume"]) == pytest.approx(500000.0)


@pytest.mark.asyncio
async def test_fetch_ticker_stats_linear_perp(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/market/tickers?instType=SWAP",
        json=_tickers(
            {"instId": "BTC-USDT-SWAP", "last": "30000", "open24h": "30000", "volCcy24h": "100"}
        ),
    )
    result = await worker.fetch_ticker_stats("BTC-USDT-SWAP", "linear_perp")
    assert result["mark_price"] == "30000"
    assert float(result["daily_price_chg"]) == pytest.approx(0.0)
    # linear perp: daily_volume = volCcy24h * last_price
    assert float(result["daily_volume"]) == pytest.approx(100 * 30000)


@pytest.mark.asyncio
async def test_fetch_ticker_stats_all(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/market/tickers?instType=SPOT",
        json=_tickers(
            {"instId": "BTC-USDT", "last": "30000", "open24h": "29000", "volCcy24h": "500000"},
            {"instId": "ETH-USDT", "last": "2000", "open24h": "1900", "volCcy24h": "100000"},
        ),
    )
    result = await worker.fetch_ticker_stats("__all__", "spot")
    assert "BTC-USDT" in result
    assert "ETH-USDT" in result


@pytest.mark.asyncio
async def test_fetch_ticker_stats_not_found_raises(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/market/tickers?instType=SPOT",
        json=_tickers({"instId": "ETH-USDT", "last": "2000", "open24h": "1900", "volCcy24h": "100"}),
    )
    with pytest.raises(ValueError, match="not found"):
        await worker.fetch_ticker_stats("BTC-USDT", "spot")


@pytest.mark.asyncio
async def test_fetch_ticker_stats_all_linear_excludes_inverse(worker, httpx_mock: HTTPXMock):
    """Bug #2: __all__ for linear_perp must not include inverse contracts."""
    httpx_mock.add_response(
        url=f"{_REST}/market/tickers?instType=SWAP",
        json=_tickers(
            {"instId": "BTC-USDT-SWAP", "last": "30000", "open24h": "29000", "volCcy24h": "100"},
            {"instId": "BTC-USD-SWAP", "last": "30000", "open24h": "29000", "volCcy24h": "50"},
        ),
    )
    result = await worker.fetch_ticker_stats("__all__", "linear_perp")
    assert "BTC-USDT-SWAP" in result
    assert "BTC-USD-SWAP" not in result, "inverse contract must not appear in linear_perp __all__"


@pytest.mark.asyncio
async def test_fetch_ticker_stats_all_inverse_excludes_linear(worker, httpx_mock: HTTPXMock):
    """Bug #2: __all__ for inverse_perp must not include linear contracts."""
    httpx_mock.add_response(
        url=f"{_REST}/market/tickers?instType=SWAP",
        json=_tickers(
            {"instId": "BTC-USDT-SWAP", "last": "30000", "open24h": "29000", "volCcy24h": "100"},
            {"instId": "BTC-USD-SWAP", "last": "30000", "open24h": "29000", "volCcy24h": "50"},
        ),
    )
    result = await worker.fetch_ticker_stats("__all__", "inverse_perp")
    assert "BTC-USD-SWAP" in result
    assert "BTC-USDT-SWAP" not in result, "linear contract must not appear in inverse_perp __all__"


# ---------------------------------------------------------------------------
# fetch_depth_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_depth_snapshot(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=re.compile(r".*/market/books.*"),
        json=_books(
            bids=[["29900", "1.5"], ["29800", "2.0"]],
            asks=[["30000", "0.5"], ["30100", "1.0"]],
            seq_id=9999,
        ),
    )
    result = await worker.fetch_depth_snapshot("BTC-USDT", "spot")
    assert result["last_update_id"] == 9999
    assert len(result["bids"]) == 2
    assert len(result["asks"]) == 2
    assert result["bids"][0]["price"] == "29900"
    assert result["bids"][0]["qty"] == "1.5"
