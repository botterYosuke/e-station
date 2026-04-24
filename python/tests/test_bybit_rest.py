"""TDD Red: BybitWorker REST method tests using pytest-httpx."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from engine.exchanges.base import WsNativeResyncTriggered
from engine.exchanges.bybit import BybitWorker

_REST = "https://api.bybit.com"


@pytest.fixture
def worker():
    return BybitWorker()


# ---------------------------------------------------------------------------
# list_tickers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tickers_linear_perp(worker: BybitWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/instruments-info?category=linear&limit=1000",
        json={
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "contractType": "LinearPerpetual",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                        "lotSizeFilter": {"minOrderQty": "0.001"},
                        "priceFilter": {"tickSize": "0.10"},
                    },
                    {
                        "symbol": "ETHUSDT",
                        "contractType": "LinearPerpetual",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                        "lotSizeFilter": {"minOrderQty": "0.01"},
                        "priceFilter": {"tickSize": "0.01"},
                    },
                    {
                        "symbol": "BTCUSD",
                        "contractType": "InversePerpetual",
                        "quoteCoin": "USD",
                        "status": "Trading",
                        "lotSizeFilter": {"minOrderQty": "1"},
                        "priceFilter": {"tickSize": "0.50"},
                    },
                ]
            },
        },
    )

    tickers = await worker.list_tickers("linear_perp")

    symbols = [t["symbol"] for t in tickers]
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
    # Inverse should be excluded from linear_perp
    assert "BTCUSD" not in symbols

    btc = next(t for t in tickers if t["symbol"] == "BTCUSDT")
    assert btc["min_ticksize"] == pytest.approx(0.10, rel=1e-4)
    assert btc["min_qty"] == pytest.approx(0.001, rel=1e-4)


@pytest.mark.asyncio
async def test_list_tickers_excludes_non_trading_status(worker: BybitWorker, httpx_mock: HTTPXMock):
    """Bug #2: non-Trading symbols must be excluded from list_tickers."""
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/instruments-info?category=linear&limit=1000",
        json={
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "contractType": "LinearPerpetual",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                        "lotSizeFilter": {"minOrderQty": "0.001"},
                        "priceFilter": {"tickSize": "0.10"},
                    },
                    {
                        "symbol": "PRELAUNCHUSDT",
                        "contractType": "LinearPerpetual",
                        "quoteCoin": "USDT",
                        "status": "PreLaunch",
                        "lotSizeFilter": {"minOrderQty": "1"},
                        "priceFilter": {"tickSize": "0.01"},
                    },
                    {
                        "symbol": "SETTLEDUSDT",
                        "contractType": "LinearPerpetual",
                        "quoteCoin": "USDT",
                        "status": "Settling",
                        "lotSizeFilter": {"minOrderQty": "1"},
                        "priceFilter": {"tickSize": "0.01"},
                    },
                ]
            },
        },
    )

    tickers = await worker.list_tickers("linear_perp")
    symbols = [t["symbol"] for t in tickers]
    assert "BTCUSDT" in symbols
    assert "PRELAUNCHUSDT" not in symbols
    assert "SETTLEDUSDT" not in symbols


@pytest.mark.asyncio
async def test_list_tickers_spot(worker: BybitWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/instruments-info?category=spot&limit=1000",
        json={
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                        "lotSizeFilter": {"minOrderQty": "0.00001"},
                        "priceFilter": {"tickSize": "0.01"},
                    },
                ]
            },
        },
    )

    tickers = await worker.list_tickers("spot")
    assert len(tickers) == 1
    assert tickers[0]["symbol"] == "BTCUSDT"


# ---------------------------------------------------------------------------
# fetch_klines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_linear(worker: BybitWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&limit=3",
        json={
            "retCode": 0,
            "result": {
                "symbol": "BTCUSDT",
                "category": "linear",
                "list": [
                    ["1700000060000", "68000.0", "68100.0", "67900.0", "68050.0", "12.5", "850000.0"],
                    ["1700000000000", "67900.0", "68000.0", "67800.0", "68000.0", "10.0", "680000.0"],
                ],
            },
        },
    )

    klines = await worker.fetch_klines("BTCUSDT", "linear_perp", "1m", limit=3)

    assert len(klines) == 2
    k0 = klines[0]
    assert k0["open_time_ms"] == 1700000060000
    assert k0["open"] == "68000.0"
    assert k0["high"] == "68100.0"
    assert k0["low"] == "67900.0"
    assert k0["close"] == "68050.0"
    assert k0["volume"] == "12.5"
    assert k0["is_closed"] is True


@pytest.mark.asyncio
async def test_fetch_klines_daily(worker: BybitWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/kline?category=linear&symbol=BTCUSDT&interval=D&limit=10",
        json={
            "retCode": 0,
            "result": {
                "symbol": "BTCUSDT",
                "category": "linear",
                "list": [
                    ["1700000000000", "67000.0", "69000.0", "66000.0", "68000.0", "1000.0", "68000000.0"],
                ],
            },
        },
    )

    klines = await worker.fetch_klines("BTCUSDT", "linear_perp", "1d", limit=10)
    assert len(klines) == 1


# ---------------------------------------------------------------------------
# fetch_open_interest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_interest_linear(worker: BybitWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=1h&limit=3",
        json={
            "retCode": 0,
            "result": {
                "symbol": "BTCUSDT",
                "category": "linear",
                "list": [
                    {"openInterest": "12500.00", "timestamp": "1700003600000"},
                    {"openInterest": "12345.67", "timestamp": "1700000000000"},
                ],
            },
        },
    )

    oi_list = await worker.fetch_open_interest("BTCUSDT", "linear_perp", "1h", limit=3)

    assert len(oi_list) == 2
    assert oi_list[0]["ts_ms"] == 1700003600000
    assert oi_list[0]["open_interest"] == "12500.00"


@pytest.mark.asyncio
async def test_fetch_open_interest_inverse_uses_inverse_category(
    worker: BybitWorker, httpx_mock: HTTPXMock
):
    """Bug #1: inverse_perp OI must use category=inverse, not category=linear."""
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/open-interest?category=inverse&symbol=BTCUSD&intervalTime=1h&limit=2",
        json={
            "retCode": 0,
            "result": {
                "symbol": "BTCUSD",
                "category": "inverse",
                "list": [
                    {"openInterest": "500.00", "timestamp": "1700003600000"},
                    {"openInterest": "480.00", "timestamp": "1700000000000"},
                ],
            },
        },
    )

    oi_list = await worker.fetch_open_interest("BTCUSD", "inverse_perp", "1h", limit=2)

    assert len(oi_list) == 2
    assert oi_list[0]["ts_ms"] == 1700003600000
    assert oi_list[0]["open_interest"] == "500.00"


@pytest.mark.asyncio
async def test_fetch_open_interest_spot_returns_empty(worker: BybitWorker, httpx_mock: HTTPXMock):
    oi_list = await worker.fetch_open_interest("BTCUSDT", "spot", "1h", limit=10)
    assert oi_list == []


# ---------------------------------------------------------------------------
# fetch_ticker_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ticker_stats(worker: BybitWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/tickers?category=linear",
        json={
            "retCode": 0,
            "result": {
                "category": "linear",
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "68000.0",
                        "price24hPcnt": "0.025",
                        "volume24h": "10000.0",
                    },
                    {
                        "symbol": "ETHUSDT",
                        "lastPrice": "3500.0",
                        "price24hPcnt": "-0.01",
                        "volume24h": "50000.0",
                    },
                ],
            },
        },
    )

    stats = await worker.fetch_ticker_stats("BTCUSDT", "linear_perp")

    assert stats["mark_price"] == "68000.0"
    assert float(stats["daily_price_chg"]) == pytest.approx(2.5, rel=1e-3)
    # linear: daily_volume = volume24h * mark_price (matches Rust fetch.rs)
    assert float(stats["daily_volume"]) == pytest.approx(10000.0 * 68000.0, rel=1e-6)


@pytest.mark.asyncio
async def test_fetch_ticker_stats_inverse_uses_volume24h(
    worker: BybitWorker, httpx_mock: HTTPXMock
):
    """Inverse perpetuals use volume24h directly (USD-denominated), not turnover24h (BTC)."""
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/tickers?category=inverse",
        json={
            "retCode": 0,
            "result": {
                "category": "inverse",
                "list": [
                    {
                        "symbol": "BTCUSD",
                        "lastPrice": "68000.0",
                        "price24hPcnt": "0.01",
                        "volume24h": "500000000.0",
                        "turnover24h": "7352.94",
                    },
                ],
            },
        },
    )

    stats = await worker.fetch_ticker_stats("BTCUSD", "inverse_perp")

    assert stats["mark_price"] == "68000.0"
    # inverse: daily_volume = volume24h as-is (USD contracts), not turnover24h (BTC)
    assert float(stats["daily_volume"]) == pytest.approx(500_000_000.0, rel=1e-6)


@pytest.mark.asyncio
async def test_fetch_ticker_stats_not_found(worker: BybitWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST}/v5/market/tickers?category=linear",
        json={"retCode": 0, "result": {"list": []}},
    )

    with pytest.raises(ValueError, match="XYZUSDT"):
        await worker.fetch_ticker_stats("XYZUSDT", "linear_perp")


# ---------------------------------------------------------------------------
# fetch_depth_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_triggers_reconnect(worker: BybitWorker) -> None:
    # REST snapshot is incompatible with orderbook.200 namespace.
    # The worker must set the reconnect trigger AND raise WsNativeResyncTriggered.
    with pytest.raises(WsNativeResyncTriggered, match="orderbook.200"):
        await worker.fetch_depth_snapshot("BTCUSDT", "linear_perp")

    assert worker._reconnect_trigger("BTCUSDT", "linear_perp").is_set(), (
        "reconnect trigger must be set so the active WS stream reconnects"
    )


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_trigger_independent_per_key(worker: BybitWorker) -> None:
    # Trigger for BTCUSDT must not bleed into ETHUSDT.
    try:
        await worker.fetch_depth_snapshot("BTCUSDT", "linear_perp")
    except WsNativeResyncTriggered:
        pass

    assert not worker._reconnect_trigger("ETHUSDT", "linear_perp").is_set()
