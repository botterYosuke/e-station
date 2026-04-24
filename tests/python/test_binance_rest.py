"""TDD Red: BinanceWorker REST method tests using pytest-httpx."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from flowsurface_data.exchanges.binance import BinanceWorker


@pytest.fixture
def worker():
    return BinanceWorker()


# ---------------------------------------------------------------------------
# list_tickers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tickers_linear_perp(worker: BinanceWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://fapi.binance.com/fapi/v1/exchangeInfo",
        json={
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "minQty": "0.001"},
                    ],
                },
                {
                    "symbol": "ETHBTC",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "BTC",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.00001"},
                        {"filterType": "LOT_SIZE", "minQty": "0.01"},
                    ],
                },
            ]
        },
    )

    tickers = await worker.list_tickers("linear_perp")

    assert len(tickers) == 1
    assert tickers[0]["symbol"] == "BTCUSDT"
    assert tickers[0]["min_ticksize"] == pytest.approx(0.10, rel=1e-4)


@pytest.mark.asyncio
async def test_list_tickers_filters_non_usdt(worker: BinanceWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://fapi.binance.com/fapi/v1/exchangeInfo",
        json={
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "minQty": "0.001"},
                    ],
                },
                {
                    "symbol": "BTCUSD_PERP",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USD",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "minQty": "1"},
                    ],
                },
            ]
        },
    )

    tickers = await worker.list_tickers("linear_perp")
    symbols = [t["symbol"] for t in tickers]
    assert "BTCUSDT" in symbols


# ---------------------------------------------------------------------------
# fetch_klines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_returns_list(worker: BinanceWorker, httpx_mock: HTTPXMock):
    kline_row = [
        1700000000000,  # open_time
        "67000.0",      # open
        "68000.0",      # high
        "66500.0",      # low
        "67800.0",      # close
        "100.5",        # volume (base)
        1700000059999,  # close_time
        "6780900.0",    # quote asset volume
        1500,           # num trades
        "60.3",         # taker buy base
        "4088340.0",    # taker buy quote
        "0",            # ignore
    ]
    httpx_mock.add_response(
        url="https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1",
        json=[kline_row],
    )

    klines = await worker.fetch_klines("BTCUSDT", "linear_perp", "1m", limit=1)

    assert len(klines) == 1
    k = klines[0]
    assert k["open_time_ms"] == 1700000000000
    assert k["open"] == "67000.0"
    assert k["close"] == "67800.0"
    assert k["is_closed"] is True
    assert "taker_buy_base" not in k


# ---------------------------------------------------------------------------
# fetch_open_interest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_interest(worker: BinanceWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=2",
        json=[
            {"timestamp": 1700000000000, "sumOpenInterest": "12345.67"},
            {"timestamp": 1700003600000, "sumOpenInterest": "12500.00"},
        ],
    )

    oi_list = await worker.fetch_open_interest("BTCUSDT", "linear_perp", "1h", limit=2)

    assert len(oi_list) == 2
    assert oi_list[0]["ts_ms"] == 1700000000000
    assert oi_list[0]["open_interest"] == "12345.67"


# ---------------------------------------------------------------------------
# fetch_ticker_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ticker_stats(worker: BinanceWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://fapi.binance.com/fapi/v1/ticker/24hr",
        json=[
            {
                "symbol": "BTCUSDT",
                "lastPrice": "68000.0",
                "priceChangePercent": "2.5",
                "quoteVolume": "1500000000.0",
            }
        ],
    )

    stats = await worker.fetch_ticker_stats("BTCUSDT", "linear_perp")

    assert stats["mark_price"] == "68000.0"
    assert float(stats["daily_price_chg"]) == pytest.approx(2.5, rel=1e-3)


# ---------------------------------------------------------------------------
# fetch_depth_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_depth_snapshot(worker: BinanceWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000",
        json={
            "lastUpdateId": 12345,
            "T": 1700000000000,
            "bids": [["67990.0", "1.5"], ["67980.0", "2.0"]],
            "asks": [["68000.0", "0.5"], ["68010.0", "1.0"]],
        },
    )

    snap = await worker.fetch_depth_snapshot("BTCUSDT", "linear_perp")

    assert snap["last_update_id"] == 12345
    assert len(snap["bids"]) == 2
    assert snap["bids"][0] == {"price": "67990.0", "qty": "1.5"}
    assert len(snap["asks"]) == 2
    assert snap["asks"][0] == {"price": "68000.0", "qty": "0.5"}
