"""TDD Red: HyperliquidWorker REST method tests using pytest-httpx."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from engine.exchanges.hyperliquid import HyperliquidWorker

_API_INFO = "https://api.hyperliquid.xyz/info"


@pytest.fixture
def worker():
    return HyperliquidWorker()


# ---------------------------------------------------------------------------
# Helpers: canned response fixtures
# ---------------------------------------------------------------------------


def _perp_dexs_response():
    """perpDexs returns list with null (main DEX) and optionally named DEXs."""
    return [None]


def _perp_meta_response(assets=None, ctxs=None):
    """metaAndAssetCtxs: [metadata, assetContexts]"""
    if assets is None:
        assets = [
            {"name": "BTC", "szDecimals": 5, "index": 0},
            {"name": "ETH", "szDecimals": 4, "index": 1},
        ]
    if ctxs is None:
        ctxs = [
            {
                "dayNtlVlm": "1234567.89",
                "markPx": "68000.0",
                "midPx": "68000.5",
                "prevDayPx": "66500.0",
                "openInterest": "100.0",
            },
            {
                "dayNtlVlm": "234567.89",
                "markPx": "3500.0",
                "midPx": "3500.5",
                "prevDayPx": "3400.0",
                "openInterest": "200.0",
            },
        ]
    return [{"universe": assets}, ctxs]


def _spot_meta_response():
    tokens = [
        {"name": "USDC", "szDecimals": 8, "index": 0},
        {"name": "BTC", "szDecimals": 8, "index": 1},
    ]
    pairs = [
        {"name": "BTC/USDC", "tokens": [1, 0], "index": 0},
    ]
    ctxs = [
        {
            "dayNtlVlm": "500000.0",
            "markPx": "68000.0",
            "midPx": "68000.0",
            "prevDayPx": "66000.0",
            "openInterest": "0",
        }
    ]
    return [{"tokens": tokens, "universe": pairs}, ctxs]


# ---------------------------------------------------------------------------
# list_tickers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tickers_linear_perp(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    # First request: perpDexs
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_dexs_response())
    # Second request: metaAndAssetCtxs
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_meta_response())

    tickers = await worker.list_tickers("linear_perp")

    symbols = [t["symbol"] for t in tickers]
    assert "BTC" in symbols
    assert "ETH" in symbols

    btc = next(t for t in tickers if t["symbol"] == "BTC")
    assert btc["min_ticksize"] > 0
    assert btc["min_qty"] > 0
    assert btc["contract_size"] is None


@pytest.mark.asyncio
async def test_list_tickers_excludes_zero_price(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    """Assets with price=0 (both midPx and markPx) must be excluded."""
    assets = [
        {"name": "BTC", "szDecimals": 5, "index": 0},
        {"name": "DEAD", "szDecimals": 6, "index": 1},
    ]
    ctxs = [
        {"dayNtlVlm": "1000.0", "markPx": "68000.0", "midPx": "68000.0", "prevDayPx": "66000.0"},
        {"dayNtlVlm": "0", "markPx": "0", "midPx": "0", "prevDayPx": "0"},
    ]
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_dexs_response())
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_meta_response(assets, ctxs))

    tickers = await worker.list_tickers("linear_perp")
    symbols = [t["symbol"] for t in tickers]
    assert "BTC" in symbols
    assert "DEAD" not in symbols


@pytest.mark.asyncio
async def test_list_tickers_uses_mid_price_for_tick_size(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    """midPx > 0 should be used for tick size calculation; markPx is fallback."""
    assets = [{"name": "SOL", "szDecimals": 2, "index": 0}]
    ctxs = [
        {"dayNtlVlm": "5000.0", "markPx": "148.5", "midPx": "149.0", "prevDayPx": "140.0"}
    ]
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_dexs_response())
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_meta_response(assets, ctxs))

    tickers = await worker.list_tickers("linear_perp")
    assert len(tickers) == 1
    sol = tickers[0]
    assert sol["symbol"] == "SOL"
    assert sol["min_ticksize"] > 0


@pytest.mark.asyncio
async def test_list_tickers_spot(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_spot_meta_response())

    tickers = await worker.list_tickers("spot")
    assert len(tickers) == 1
    assert tickers[0]["symbol"] == "BTCUSDC"


@pytest.mark.asyncio
async def test_list_tickers_spot_excludes_zero_price(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    tokens = [
        {"name": "USDC", "szDecimals": 8, "index": 0},
        {"name": "BTC", "szDecimals": 8, "index": 1},
        {"name": "DEAD", "szDecimals": 8, "index": 2},
    ]
    pairs = [
        {"name": "BTC/USDC", "tokens": [1, 0], "index": 0},
        {"name": "DEAD/USDC", "tokens": [2, 0], "index": 1},
    ]
    ctxs = [
        {"dayNtlVlm": "5000.0", "markPx": "68000.0", "midPx": "68000.0", "prevDayPx": "66000.0"},
        {"dayNtlVlm": "0", "markPx": "0", "midPx": "0", "prevDayPx": "0"},
    ]
    httpx_mock.add_response(
        url=_API_INFO,
        method="POST",
        json=[{"tokens": tokens, "universe": pairs}, ctxs],
    )

    tickers = await worker.list_tickers("spot")
    symbols = [t["symbol"] for t in tickers]
    assert "BTCUSDC" in symbols
    assert "DEADUSDC" not in symbols


# ---------------------------------------------------------------------------
# fetch_klines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_with_start_end(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    klines_data = [
        {
            "t": 1700000000000,
            "T": 1700000060000,
            "s": "BTC",
            "i": "1m",
            "o": "68000.0",
            "h": "68100.0",
            "l": "67900.0",
            "c": "68050.0",
            "v": "5.5",
            "n": 120,
        }
    ]
    httpx_mock.add_response(url=_API_INFO, method="POST", json=klines_data)

    klines = await worker.fetch_klines(
        "BTC", "linear_perp", "1m", start_ms=1700000000000, end_ms=1700000060000
    )

    assert len(klines) == 1
    k = klines[0]
    assert k["open_time_ms"] == 1700000000000
    assert k["open"] == "68000.0"
    assert k["high"] == "68100.0"
    assert k["low"] == "67900.0"
    assert k["close"] == "68050.0"
    assert k["volume"] == "5.5"
    assert k["is_closed"] is True


@pytest.mark.asyncio
async def test_fetch_klines_limit_calculates_start_time(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    """When start_ms not provided, start = end - limit * interval_ms."""
    httpx_mock.add_response(url=_API_INFO, method="POST", json=[])

    # Should not raise; empty response is OK
    klines = await worker.fetch_klines("BTC", "linear_perp", "1m", limit=100)
    assert klines == []


@pytest.mark.asyncio
async def test_fetch_klines_multiple_candles(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    klines_data = [
        {"t": 1700000000000, "T": 1700000060000, "s": "BTC", "i": "1m",
         "o": "67900.0", "h": "68000.0", "l": "67800.0", "c": "68000.0", "v": "10.0", "n": 50},
        {"t": 1700000060000, "T": 1700000120000, "s": "BTC", "i": "1m",
         "o": "68000.0", "h": "68100.0", "l": "67900.0", "c": "68050.0", "v": "5.5", "n": 120},
    ]
    httpx_mock.add_response(url=_API_INFO, method="POST", json=klines_data)

    klines = await worker.fetch_klines("BTC", "linear_perp", "1m", limit=2)
    assert len(klines) == 2


# ---------------------------------------------------------------------------
# fetch_open_interest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_interest_returns_empty_for_all_markets(worker: HyperliquidWorker):
    """Hyperliquid does not provide historical OI series; always returns []."""
    for market in ("linear_perp", "spot", "inverse_perp"):
        result = await worker.fetch_open_interest("BTC", market, "1h", limit=10)
        assert result == [], f"Expected empty list for market={market}"


# ---------------------------------------------------------------------------
# fetch_ticker_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ticker_stats_single_perp(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_dexs_response())
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_meta_response())

    stats = await worker.fetch_ticker_stats("BTC", "linear_perp")

    assert "mark_price" in stats
    assert "daily_price_chg" in stats
    assert "daily_volume" in stats
    assert float(stats["mark_price"]) > 0
    # daily_price_chg = (68000.5 - 66500) / 66500 * 100 ≈ 2.26%
    assert float(stats["daily_price_chg"]) == pytest.approx(
        (68000.5 - 66500.0) / 66500.0 * 100.0, rel=1e-3
    )
    assert float(stats["daily_volume"]) == pytest.approx(1234567.89, rel=1e-3)


@pytest.mark.asyncio
async def test_fetch_ticker_stats_uses_mid_price_when_available(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    """If midPx > 0, use midPx for price; otherwise fall back to markPx."""
    assets = [{"name": "ETH", "szDecimals": 4, "index": 0}]
    # midPx is 0; should fall back to markPx
    ctxs = [
        {
            "dayNtlVlm": "9999.0",
            "markPx": "3500.0",
            "midPx": "0",
            "prevDayPx": "3400.0",
        }
    ]
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_dexs_response())
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_meta_response(assets, ctxs))

    stats = await worker.fetch_ticker_stats("ETH", "linear_perp")
    # With midPx=0, falls back to markPx=3500. price_chg = (3500-3400)/3400*100 ≈ 2.94%
    assert float(stats["mark_price"]) == pytest.approx(3500.0, rel=1e-4)
    assert float(stats["daily_price_chg"]) == pytest.approx(
        (3500.0 - 3400.0) / 3400.0 * 100.0, rel=1e-3
    )


@pytest.mark.asyncio
async def test_fetch_ticker_stats_not_found(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_dexs_response())
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_meta_response())

    with pytest.raises(ValueError, match="XYZTOKEN"):
        await worker.fetch_ticker_stats("XYZTOKEN", "linear_perp")


@pytest.mark.asyncio
async def test_fetch_ticker_stats_all(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    """ticker='__all__' returns dict keyed by symbol."""
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_dexs_response())
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_perp_meta_response())

    stats = await worker.fetch_ticker_stats("__all__", "linear_perp")

    assert isinstance(stats, dict)
    assert "BTC" in stats
    assert "ETH" in stats
    assert "mark_price" in stats["BTC"]


@pytest.mark.asyncio
async def test_fetch_ticker_stats_spot(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=_API_INFO, method="POST", json=_spot_meta_response())

    stats = await worker.fetch_ticker_stats("BTCUSDC", "spot")
    assert float(stats["mark_price"]) > 0


# ---------------------------------------------------------------------------
# fetch_depth_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_perp(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    depth_data = {
        "levels": [
            [{"px": "68000.0", "sz": "1.5"}, {"px": "67999.0", "sz": "2.0"}],
            [{"px": "68001.0", "sz": "0.5"}, {"px": "68002.0", "sz": "1.0"}],
        ],
        "time": 1700000000000,
    }
    httpx_mock.add_response(url=_API_INFO, method="POST", json=depth_data)

    snap = await worker.fetch_depth_snapshot("BTC", "linear_perp")

    assert snap["last_update_id"] == 1700000000000
    assert len(snap["bids"]) == 2
    assert len(snap["asks"]) == 2
    assert snap["bids"][0] == {"price": "68000.0", "qty": "1.5"}
    assert snap["asks"][0] == {"price": "68001.0", "qty": "0.5"}


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_spot(worker: HyperliquidWorker, httpx_mock: HTTPXMock):
    depth_data = {
        "levels": [
            [{"px": "68000.0", "sz": "0.1"}],
            [{"px": "68010.0", "sz": "0.2"}],
        ],
        "time": 1700000005000,
    }
    httpx_mock.add_response(url=_API_INFO, method="POST", json=depth_data)

    snap = await worker.fetch_depth_snapshot("BTC/USDC", "spot")
    assert snap["last_update_id"] == 1700000005000
    assert snap["bids"][0]["price"] == "68000.0"
