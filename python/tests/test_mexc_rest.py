"""MEXC REST API tests — Phase 3."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from engine.exchanges.mexc import MexcWorker

_REST_V3 = "https://api.mexc.com/api/v3"
_REST_V1 = "https://api.mexc.com/api/v1/contract"


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def worker():
    return MexcWorker()


# --- spot exchange info ---

def _exchange_info_spot(*symbols):
    return {"symbols": list(symbols)}


def _symbol_spot(
    symbol="BTCUSDT",
    status="1",
    quote_asset="USDT",
    base_size_precision="0.00001",
    quote_asset_precision=2,
):
    return {
        "symbol": symbol,
        "status": status,
        "quoteAsset": quote_asset,
        "baseSizePrecision": base_size_precision,
        "quoteAssetPrecision": quote_asset_precision,
    }


# --- futures contract detail ---

def _contract_detail(*contracts):
    return {"data": list(contracts)}


def _contract_item(
    symbol="BTC_USDT",
    state=0,
    quote_coin="USDT",
    settle_coin="USDT",
    base_coin="BTC",
    min_vol=1.0,
    price_unit=0.1,
    contract_size=0.0001,
):
    return {
        "symbol": symbol,
        "state": state,
        "quoteCoin": quote_coin,
        "settleCoin": settle_coin,
        "baseCoin": base_coin,
        "minVol": min_vol,
        "priceUnit": price_unit,
        "contractSize": contract_size,
    }


# --- spot 24hr ticker ---

def _spot_tickers(*items):
    return list(items)


def _spot_ticker(
    symbol="BTCUSDT",
    last_price="30000.5",
    price_change_percent="0.005",
    volume="500.0",
    quote_volume="15000000.0",
):
    return {
        "symbol": symbol,
        "lastPrice": last_price,
        "priceChangePercent": price_change_percent,
        "volume": volume,
        "quoteVolume": quote_volume,
    }


# --- futures ticker ---

def _futures_tickers(*items):
    return {"data": list(items)}


def _futures_ticker(
    symbol="BTC_USDT",
    last_price="30000.5",
    rise_fall_rate="0.005",
    volume24="1000000.0",
):
    return {
        "symbol": symbol,
        "lastPrice": last_price,
        "riseFallRate": rise_fall_rate,
        "volume24": volume24,
    }


# --- depth snapshot ---

def _depth_snapshot(version=1000, timestamp=1700000000000, bids=None, asks=None):
    # MEXC futures depth API returns levels as [price, vol, order_count] arrays.
    return {
        "code": 0,
        "data": {
            "version": version,
            "timestamp": timestamp,
            "bids": bids or [[29999.0, 1.0, 1]],
            "asks": asks or [[30001.0, 0.5, 1]],
        },
    }


# ---------------------------------------------------------------------------
# list_tickers — spot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tickers_spot_returns_usdt_pairs(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/exchangeInfo",
        json=_exchange_info_spot(
            _symbol_spot("BTCUSDT"),
            _symbol_spot("ETHUSDT", base_size_precision="0.0001", quote_asset_precision=2),
            _symbol_spot("BTCBTC", quote_asset="BTC"),  # excluded: non-USDT
        ),
    )
    result = await worker.list_tickers("spot")
    symbols = [r["symbol"] for r in result]
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
    assert "BTCBTC" not in symbols


@pytest.mark.asyncio
async def test_list_tickers_spot_ticksize_from_precision(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/exchangeInfo",
        json=_exchange_info_spot(_symbol_spot("BTCUSDT", quote_asset_precision=2)),
    )
    result = await worker.list_tickers("spot")
    assert len(result) == 1
    item = result[0]
    assert abs(item["min_ticksize"] - 0.01) < 1e-6
    assert item["contract_size"] is None


@pytest.mark.asyncio
async def test_list_tickers_spot_excludes_inactive(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/exchangeInfo",
        json=_exchange_info_spot(
            _symbol_spot("BTCUSDT", status="1"),
            _symbol_spot("ETHUSDT", status="3"),  # inactive
        ),
    )
    result = await worker.list_tickers("spot")
    symbols = [r["symbol"] for r in result]
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" not in symbols


# ---------------------------------------------------------------------------
# list_tickers — linear_perp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tickers_linear_perp(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_contract_detail(
            _contract_item("BTC_USDT", settle_coin="USDT", base_coin="BTC"),
            _contract_item("BTC_USD", quote_coin="USD", settle_coin="BTC", base_coin="BTC"),
        ),
    )
    result = await worker.list_tickers("linear_perp")
    symbols = [r["symbol"] for r in result]
    assert "BTC_USDT" in symbols
    assert "BTC_USD" not in symbols  # inverse, excluded


@pytest.mark.asyncio
async def test_list_tickers_linear_perp_contract_size(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_contract_detail(
            _contract_item("BTC_USDT", min_vol=1.0, price_unit=0.5, contract_size=0.0001),
        ),
    )
    result = await worker.list_tickers("linear_perp")
    assert len(result) == 1
    item = result[0]
    assert abs(item["min_ticksize"] - 0.5) < 1e-6
    assert abs(item["contract_size"] - 0.0001) < 1e-6


@pytest.mark.asyncio
async def test_list_tickers_linear_perp_excludes_inactive(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_contract_detail(
            _contract_item("BTC_USDT", state=0),
            _contract_item("ETH_USDT", state=1),  # inactive
        ),
    )
    result = await worker.list_tickers("linear_perp")
    symbols = [r["symbol"] for r in result]
    assert "BTC_USDT" in symbols
    assert "ETH_USDT" not in symbols


# ---------------------------------------------------------------------------
# fetch_klines — spot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_spot_basic(worker, httpx_mock: HTTPXMock):
    # MEXC spot klines: [[open_ts_ms, open, high, low, close, vol, close_ts_ms, asset_vol]]
    httpx_mock.add_response(
        url=f"{_REST_V3}/klines?symbol=BTCUSDT&interval=1m&limit=300",
        json=[
            [1700000000000, "30000.0", "30100.0", "29900.0", "30050.0", "500.0", 1700000059999, "15025000.0"],
            [1700000060000, "30050.0", "30200.0", "30000.0", "30150.0", "600.0", 1700000119999, "18090000.0"],
        ],
    )
    result = await worker.fetch_klines("BTCUSDT", "spot", "1m", limit=300)
    assert len(result) == 2
    k = result[0]
    assert k["open_time_ms"] == 1700000000000
    assert k["open"] == "30000.0"
    assert k["high"] == "30100.0"
    assert k["low"] == "29900.0"
    assert k["close"] == "30050.0"
    assert "volume" in k
    assert "is_closed" in k


@pytest.mark.asyncio
async def test_fetch_klines_spot_with_time_range(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/klines?symbol=BTCUSDT&interval=1m&limit=300&startTime=1700000000000&endTime=1700003600000",
        json=[],
    )
    result = await worker.fetch_klines(
        "BTCUSDT", "spot", "1m", limit=300,
        start_ms=1700000000000, end_ms=1700003600000
    )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_klines_spot_sorted_ascending(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/klines?symbol=BTCUSDT&interval=1m&limit=300",
        json=[
            [1700000060000, "30050.0", "30200.0", "30000.0", "30150.0", "600.0", 1700000119999, "18090000.0"],
            [1700000000000, "30000.0", "30100.0", "29900.0", "30050.0", "500.0", 1700000059999, "15025000.0"],
        ],
    )
    result = await worker.fetch_klines("BTCUSDT", "spot", "1m", limit=300)
    assert result[0]["open_time_ms"] < result[1]["open_time_ms"]


# ---------------------------------------------------------------------------
# fetch_klines — futures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_futures_basic(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/kline/BTC_USDT?interval=Min1&limit=300",
        json={
            "success": True,
            "code": 0,
            "data": {
                "time": [1700000, 1700060],
                "open": [30000.0, 30050.0],
                "high": [30100.0, 30200.0],
                "low": [29900.0, 30000.0],
                "close": [30050.0, 30150.0],
                "amount": [15025000.0, 18090000.0],
                "vol": [500.0, 600.0],
            },
        },
    )
    result = await worker.fetch_klines("BTC_USDT", "linear_perp", "1m", limit=300)
    assert len(result) == 2
    k = result[0]
    assert k["open_time_ms"] == 1700000 * 1000
    assert k["open"] == "30000.0"
    assert k["close"] == "30050.0"


@pytest.mark.asyncio
async def test_fetch_klines_futures_with_time_range(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/kline/BTC_USDT?interval=Min1&limit=300&start=1700000&end=1703600",
        json={"success": True, "code": 0, "data": {"time": [], "open": [], "high": [], "low": [], "close": [], "amount": [], "vol": []}},
    )
    result = await worker.fetch_klines(
        "BTC_USDT", "linear_perp", "1m", limit=300,
        start_ms=1700000000, end_ms=1703600000
    )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_klines_futures_timeframe_mapping(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/kline/BTC_USDT?interval=Hour4&limit=300",
        json={"success": True, "code": 0, "data": {"time": [], "open": [], "high": [], "low": [], "close": [], "amount": [], "vol": []}},
    )
    result = await worker.fetch_klines("BTC_USDT", "linear_perp", "4h", limit=300)
    assert result == []


# ---------------------------------------------------------------------------
# fetch_open_interest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_open_interest_returns_empty_list(worker):
    """MEXC does not support OI history; always returns empty list."""
    result = await worker.fetch_open_interest("BTC_USDT", "linear_perp", "1h")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_open_interest_spot_returns_empty_list(worker):
    result = await worker.fetch_open_interest("BTCUSDT", "spot", "1h")
    assert result == []


# ---------------------------------------------------------------------------
# fetch_ticker_stats — spot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ticker_stats_spot_single(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/ticker/24hr",
        json=_spot_tickers(
            _spot_ticker("BTCUSDT", last_price="30000.5", price_change_percent="0.005",
                         volume="500.0", quote_volume="15000000.0"),
            _spot_ticker("ETHUSDT", last_price="2000.0", price_change_percent="-0.01"),
        ),
    )
    result = await worker.fetch_ticker_stats("BTCUSDT", "spot")
    assert result["mark_price"] == "30000.5"
    assert abs(float(result["daily_price_chg"]) - 0.5) < 0.01
    assert abs(float(result["daily_volume"]) - 15000000.0) < 1.0


@pytest.mark.asyncio
async def test_fetch_ticker_stats_spot_all(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/ticker/24hr",
        json=_spot_tickers(
            _spot_ticker("BTCUSDT"),
            _spot_ticker("ETHUSDT"),
            _spot_ticker("XRPBTC"),  # excluded: non-USDT
        ),
    )
    result = await worker.fetch_ticker_stats("__all__", "spot")
    assert isinstance(result, dict)
    assert "BTCUSDT" in result
    assert "ETHUSDT" in result
    assert "XRPBTC" not in result


@pytest.mark.asyncio
async def test_fetch_ticker_stats_spot_volume_uses_quote_volume(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V3}/ticker/24hr",
        json=_spot_tickers(
            _spot_ticker("BTCUSDT", last_price="30000.0", volume="500.0", quote_volume="15000000.0"),
        ),
    )
    result = await worker.fetch_ticker_stats("BTCUSDT", "spot")
    # quoteVolume (15000000) should be preferred over volume * price (500 * 30000 = 15000000)
    assert abs(float(result["daily_volume"]) - 15000000.0) < 1.0


# ---------------------------------------------------------------------------
# fetch_ticker_stats — futures
# ---------------------------------------------------------------------------


def _detail_response(*items):
    return {"data": list(items)}


@pytest.mark.asyncio
async def test_fetch_ticker_stats_futures_single(worker, httpx_mock: HTTPXMock):
    # contract size = 0.001 BTC, price = 30000.5 USDT, volume24 = 1_000_000 contracts
    # linear daily_volume = 1_000_000 * 0.001 * 30000.5 = 30_000_500
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_detail_response(_contract_item("BTC_USDT", contract_size=0.001)),
    )
    httpx_mock.add_response(
        url=f"{_REST_V1}/ticker",
        json=_futures_tickers(
            _futures_ticker("BTC_USDT", last_price="30000.5", rise_fall_rate="0.005", volume24="1000000.0"),
        ),
    )
    result = await worker.fetch_ticker_stats("BTC_USDT", "linear_perp")
    assert result["mark_price"] == "30000.5"
    assert abs(float(result["daily_price_chg"]) - 0.5) < 0.01
    expected_volume = 1_000_000.0 * 0.001 * 30000.5
    assert abs(float(result["daily_volume"]) - expected_volume) < 1.0, (
        f"linear daily_volume mismatch: {result['daily_volume']} != {expected_volume}"
    )


@pytest.mark.asyncio
async def test_fetch_ticker_stats_futures_single_inverse_daily_volume(worker, httpx_mock: HTTPXMock):
    # inverse: daily_volume = volume24 * contract_size (no price multiplier)
    # contract_size = 1 USD, volume24 = 500_000 contracts
    # daily_volume = 500_000 * 1 = 500_000
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_detail_response(_contract_item("BTC_USD", contract_size=1.0, quote_coin="USD", settle_coin="BTC")),
    )
    httpx_mock.add_response(
        url=f"{_REST_V1}/ticker",
        json=_futures_tickers(
            _futures_ticker("BTC_USD", last_price="30000.0", volume24="500000.0"),
        ),
    )
    result = await worker.fetch_ticker_stats("BTC_USD", "inverse_perp")
    expected_volume = 500_000.0 * 1.0
    assert abs(float(result["daily_volume"]) - expected_volume) < 1.0, (
        f"inverse daily_volume mismatch: {result['daily_volume']} != {expected_volume}"
    )


@pytest.mark.asyncio
async def test_fetch_ticker_stats_futures_skips_unknown_contract_size(worker, httpx_mock: HTTPXMock):
    # Symbols absent from /detail must be excluded rather than emitting daily_volume=0.
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_detail_response(_contract_item("ETH_USDT", contract_size=0.01)),
        # BTC_USDT intentionally absent
    )
    httpx_mock.add_response(
        url=f"{_REST_V1}/ticker",
        json=_futures_tickers(
            _futures_ticker("BTC_USDT"),
            _futures_ticker("ETH_USDT"),
        ),
    )
    result = await worker.fetch_ticker_stats("__all__", "linear_perp")
    assert "ETH_USDT" in result
    assert "BTC_USDT" not in result, (
        "symbol with unknown contract size must be skipped, not returned with daily_volume=0"
    )


@pytest.mark.asyncio
async def test_fetch_ticker_stats_futures_lazy_contract_sizes(worker, httpx_mock: HTTPXMock):
    # If list_tickers was never called, _contract_sizes is empty.
    # _fetch_ticker_stats_futures must auto-populate from /detail.
    assert not worker._contract_sizes, "precondition: _contract_sizes must start empty"
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_detail_response(_contract_item("BTC_USDT", contract_size=0.001)),
    )
    httpx_mock.add_response(
        url=f"{_REST_V1}/ticker",
        json=_futures_tickers(
            _futures_ticker("BTC_USDT", last_price="50000.0", volume24="100.0"),
        ),
    )
    result = await worker.fetch_ticker_stats("BTC_USDT", "linear_perp")
    expected = 100.0 * 0.001 * 50000.0
    assert abs(float(result["daily_volume"]) - expected) < 0.1


@pytest.mark.asyncio
async def test_fetch_ticker_stats_futures_all(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_detail_response(
            _contract_item("BTC_USDT", contract_size=0.001),
            _contract_item("ETH_USDT", contract_size=0.01),
            _contract_item("BTC_USD", contract_size=1.0, quote_coin="USD", settle_coin="BTC"),
        ),
    )
    httpx_mock.add_response(
        url=f"{_REST_V1}/ticker",
        json=_futures_tickers(
            _futures_ticker("BTC_USDT"),
            _futures_ticker("ETH_USDT"),
            _futures_ticker("BTC_USD"),  # inverse — excluded from linear_perp
        ),
    )
    result = await worker.fetch_ticker_stats("__all__", "linear_perp")
    assert isinstance(result, dict)
    assert "BTC_USDT" in result
    assert "ETH_USDT" in result
    assert "BTC_USD" not in result


@pytest.mark.asyncio
async def test_fetch_ticker_stats_futures_all_inverse(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/detail",
        json=_detail_response(
            _contract_item("BTC_USDT", contract_size=0.001),
            _contract_item("BTC_USD", contract_size=1.0, quote_coin="USD", settle_coin="BTC"),
        ),
    )
    httpx_mock.add_response(
        url=f"{_REST_V1}/ticker",
        json=_futures_tickers(
            _futures_ticker("BTC_USDT"),
            _futures_ticker("BTC_USD"),
        ),
    )
    result = await worker.fetch_ticker_stats("__all__", "inverse_perp")
    assert "BTC_USD" in result
    assert "BTC_USDT" not in result


# ---------------------------------------------------------------------------
# fetch_depth_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_futures(worker, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{_REST_V1}/depth/BTC_USDT",
        json=_depth_snapshot(
            version=9999,
            bids=[[29999.0, 2.0, 1]],
            asks=[[30001.0, 1.0, 1]],
        ),
    )
    result = await worker.fetch_depth_snapshot("BTC_USDT", "linear_perp")
    assert result["last_update_id"] == 9999
    assert len(result["bids"]) == 1
    assert result["bids"][0]["price"] == "29999.0"
    assert result["asks"][0]["qty"] == "1.0"


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_futures_real_format(worker, httpx_mock: HTTPXMock):
    """Regression for UI-2: MEXC depth API returns [price, vol, count] arrays.

    Previously `_depth_levels` indexed `item["price"]` which raised TypeError
    on every level → snapshot fetch failed → infinite reconnect loop.
    """
    httpx_mock.add_response(
        url=f"{_REST_V1}/depth/BTC_USDT",
        json={
            "success": True,
            "code": 0,
            "data": {
                "version": 12345,
                "timestamp": 1700000000000,
                "bids": [[77577.8, 175490, 6], [77577.7, 700, 1]],
                "asks": [[77578.0, 666, 1], [77578.1, 1227, 1]],
            },
        },
    )
    result = await worker.fetch_depth_snapshot("BTC_USDT", "linear_perp")
    assert result["last_update_id"] == 12345
    assert result["bids"][0] == {"price": "77577.8", "qty": "175490"}
    assert result["asks"][0] == {"price": "77578.0", "qty": "666"}


@pytest.mark.asyncio
async def test_fetch_depth_snapshot_spot_raises(worker):
    """Spot depth snapshot is not supported by MEXC."""
    with pytest.raises(ValueError, match="not supported"):
        await worker.fetch_depth_snapshot("BTCUSDT", "spot")
