"""TDD Red: BinanceWorker.fetch_trades() — historical trade download tests."""

from __future__ import annotations

import io
import zipfile
import pytest
from pytest_httpx import HTTPXMock

from engine.exchanges.binance import BinanceWorker

# Helpers to build a fake aggTrades zip/CSV
def _make_agg_trades_csv(rows: list[tuple]) -> bytes:
    """Build CSV bytes matching Binance aggTrades format (no header)."""
    lines = []
    for agg_id, price, qty, _first, _last, time_ms, is_sell in rows:
        lines.append(f"{agg_id},{price},{qty},{_first},{_last},{time_ms},{is_sell}")
    return "\n".join(lines).encode()


def _make_zip(csv_bytes: bytes, filename: str = "BTCUSDT-aggTrades-2024-01-01.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, csv_bytes)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# fetch_trades — intraday (today, uses aggTrades API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_trades_intraday_returns_list(
    worker: BinanceWorker, httpx_mock: HTTPXMock
):
    """When start_ms is today, fetch_trades uses the aggTrades REST endpoint."""
    from datetime import timezone
    import datetime as dt

    today_ms = int(
        dt.datetime.now(tz=timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
        * 1000
    )
    start_ms = today_ms + 3_600_000  # 1 hour into today

    httpx_mock.add_response(
        url=f"https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&limit=1000&startTime={start_ms}",
        json=[
            {"T": start_ms, "p": "68000.0", "q": "0.5", "m": False},
            {"T": start_ms + 1000, "p": "68100.0", "q": "0.3", "m": True},
        ],
    )

    trades = await worker.fetch_trades("BTCUSDT", "linear_perp", start_ms)

    assert len(trades) == 2
    t0 = trades[0]
    assert t0["ts_ms"] == start_ms
    assert t0["price"] == "68000.0"
    assert t0["qty"] == "0.5"
    assert t0["side"] == "buy"
    t1 = trades[1]
    assert t1["side"] == "sell"


@pytest.mark.asyncio
async def test_fetch_trades_intraday_spot(worker: BinanceWorker, httpx_mock: HTTPXMock):
    from datetime import timezone
    import datetime as dt

    today_ms = int(
        dt.datetime.now(tz=timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
        * 1000
    )
    start_ms = today_ms + 1_000

    httpx_mock.add_response(
        url=f"https://api.binance.com/api/v3/aggTrades?symbol=BTCUSDT&limit=1000&startTime={start_ms}",
        json=[{"T": start_ms, "p": "50000.0", "q": "1.0", "m": False}],
    )

    trades = await worker.fetch_trades("BTCUSDT", "spot", start_ms)
    assert len(trades) == 1
    assert trades[0]["side"] == "buy"


# ---------------------------------------------------------------------------
# fetch_trades — historical (uses data.binance.vision zip)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_trades_historical_downloads_zip(tmp_path, httpx_mock: HTTPXMock):
    """Historical dates download and cache a zip from data.binance.vision."""
    import datetime as dt
    from datetime import timezone

    # A date well in the past
    past_ms = int(
        dt.datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    worker = BinanceWorker()

    csv_bytes = _make_agg_trades_csv(
        [
            (1, "68000.0", "0.5", 1, 1, past_ms, False),
            (2, "68100.0", "0.3", 2, 2, past_ms + 1000, True),
        ]
    )
    zip_bytes = _make_zip(
        csv_bytes, "BTCUSDT-aggTrades-2024-01-01.csv"
    )

    # data.binance.vision URL
    zip_url = "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-01-01.zip"
    httpx_mock.add_response(url=zip_url, content=zip_bytes)

    trades = await worker.fetch_trades(
        "BTCUSDT", "linear_perp", past_ms, data_path=tmp_path
    )

    assert len(trades) >= 2
    assert trades[0]["price"] == "68000.0"
    assert trades[0]["side"] == "buy"
    assert trades[1]["side"] == "sell"


@pytest.mark.asyncio
async def test_fetch_trades_historical_uses_cache(tmp_path, httpx_mock: HTTPXMock):
    """Second call uses the cached zip file without re-downloading."""
    import datetime as dt
    from datetime import timezone

    past_ms = int(
        dt.datetime(2024, 1, 2, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    worker = BinanceWorker()

    csv_bytes = _make_agg_trades_csv(
        [(1, "69000.0", "1.0", 1, 1, past_ms, False)]
    )
    zip_bytes = _make_zip(csv_bytes, "BTCUSDT-aggTrades-2024-01-02.csv")

    zip_url = "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-01-02.zip"

    # Pre-write the zip to disk to simulate a cache hit
    cache_dir = tmp_path / "data" / "futures" / "um" / "daily" / "aggTrades" / "BTCUSDT"
    cache_dir.mkdir(parents=True)
    (cache_dir / "BTCUSDT-aggTrades-2024-01-02.zip").write_bytes(zip_bytes)

    trades = await worker.fetch_trades("BTCUSDT", "linear_perp", past_ms, data_path=tmp_path)

    # data.binance.vision should NOT have been called
    assert not any(zip_url in str(req.url) for req in httpx_mock.get_requests())
    assert len(trades) >= 1


@pytest.mark.asyncio
async def test_fetch_trades_historical_falls_back_on_404(
    tmp_path, httpx_mock: HTTPXMock
):
    """When data.binance.vision returns 404, fall back to intraday aggTrades."""
    import datetime as dt
    from datetime import timezone

    past_ms = int(
        dt.datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    worker = BinanceWorker()

    zip_url = "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-01-03.zip"
    httpx_mock.add_response(url=zip_url, status_code=404)

    intraday_url = f"https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&limit=1000&startTime={past_ms}"
    httpx_mock.add_response(
        url=intraday_url,
        json=[{"T": past_ms, "p": "70000.0", "q": "0.1", "m": False}],
    )

    trades = await worker.fetch_trades("BTCUSDT", "linear_perp", past_ms, data_path=tmp_path)

    assert len(trades) == 1
    assert trades[0]["price"] == "70000.0"


# ---------------------------------------------------------------------------
# Bug 1: intraday end_ms=0 must clamp to next midnight, not start_ms+24h
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intraday_end_ms_zero_uses_next_midnight_not_plus_24h(
    worker: BinanceWorker, httpx_mock: HTTPXMock
):
    """_fetch_intraday_trades with end_ms=0 must cap at the next calendar midnight.

    Regression: the old code used start_ms + 86_400_000, which for a mid-day
    start crosses into the next calendar day and breaks the one-day-per-batch
    contract expected by the batch loop in fetcher.rs.
    """
    _DAY_MS = 86_400_000
    # 2024-01-05 15:00:00 UTC
    start_ms = 1_704_466_800_000
    next_midnight = (start_ms // _DAY_MS + 1) * _DAY_MS   # 2024-01-06 00:00:00 UTC
    after_midnight = next_midnight + 1_000                  # 2024-01-06 00:00:01 UTC

    httpx_mock.add_response(
        url=f"https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&limit=1000&startTime={start_ms}",
        json=[
            {"T": start_ms, "p": "68000.0", "q": "0.5", "m": False, "a": 1},
            {"T": next_midnight - 1_000, "p": "68001.0", "q": "0.1", "m": True, "a": 2},
            {"T": after_midnight, "p": "68002.0", "q": "0.2", "m": False, "a": 3},
        ],
    )

    trades = await worker._fetch_intraday_trades("BTCUSDT", "linear_perp", start_ms)

    ts_list = [t["ts_ms"] for t in trades]
    assert after_midnight not in ts_list, (
        f"Trade at {after_midnight} (after midnight) must be excluded when end_ms=0; "
        "the bug set end_ms=start_ms+24h which includes it"
    )
    assert start_ms in ts_list, "Trade at start_ms must be included"
    assert next_midnight - 1_000 in ts_list, "Trade just before midnight must be included"


@pytest.mark.asyncio
async def test_fallback_mid_day_start_caps_at_calendar_midnight(
    tmp_path, httpx_mock: HTTPXMock
):
    """When the zip fallback triggers mid-day, returned trades must not cross midnight.

    This exercises the full fetch_trades → _fetch_intraday_trades path and
    verifies the fix is wired end-to-end, not just in the helper.
    """
    _DAY_MS = 86_400_000
    # 2024-01-05 15:00:00 UTC — mid-day, historical date to force zip path
    start_ms = 1_704_466_800_000
    next_midnight = (start_ms // _DAY_MS + 1) * _DAY_MS
    after_midnight = next_midnight + 1_000

    worker = BinanceWorker()

    zip_url = (
        "https://data.binance.vision/data/futures/um/daily/aggTrades"
        "/BTCUSDT/BTCUSDT-aggTrades-2024-01-05.zip"
    )
    httpx_mock.add_response(url=zip_url, status_code=404)

    httpx_mock.add_response(
        url=f"https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&limit=1000&startTime={start_ms}",
        json=[
            {"T": start_ms, "p": "68000.0", "q": "0.5", "m": False, "a": 1},
            {"T": after_midnight, "p": "68002.0", "q": "0.2", "m": False, "a": 2},
        ],
    )

    trades = await worker.fetch_trades(
        "BTCUSDT", "linear_perp", start_ms, data_path=tmp_path
    )

    ts_list = [t["ts_ms"] for t in trades]
    assert after_midnight not in ts_list, (
        "Trade after midnight included in fallback — end_ms was start_ms+24h instead of next midnight"
    )
    assert start_ms in ts_list


@pytest.mark.asyncio
async def test_fetch_trades_historical_filters_by_start_ms(tmp_path, httpx_mock: HTTPXMock):
    """Historical zip fetch should filter by start_ms and end_ms (if provided).

    This ensures that when a start_ms falls mid-day, we get only trades
    >= start_ms and, if specified, <= end_ms. This is the Python behavior.
    """
    # 2024-01-05 15:00:00 UTC — mid-day, will force zip path
    start_ms = 1_704_466_800_000
    # 2024-01-05 23:59:59:000 UTC — end of day
    end_of_day_ms = 1_704_499_199_000

    worker = BinanceWorker()

    # Zip CSV has trades before and after start_ms
    csv_bytes = _make_agg_trades_csv([
        (1, "68000.0", "0.5", 1, 1, start_ms - 3_600_000, False),  # Before start_ms
        (2, "68100.0", "0.3", 2, 2, start_ms, True),               # At start_ms
        (3, "68200.0", "0.2", 3, 3, end_of_day_ms, False),         # Near end of day
    ])
    zip_bytes = _make_zip(csv_bytes, "BTCUSDT-aggTrades-2024-01-05.csv")

    zip_url = (
        "https://data.binance.vision/data/futures/um/daily/aggTrades"
        "/BTCUSDT/BTCUSDT-aggTrades-2024-01-05.zip"
    )
    httpx_mock.add_response(url=zip_url, content=zip_bytes)

    trades = await worker.fetch_trades("BTCUSDT", "linear_perp", start_ms, data_path=tmp_path)

    # Only trades >= start_ms should be returned
    assert all(t["ts_ms"] >= start_ms for t in trades), (
        "All trades must have ts_ms >= start_ms"
    )
    # Should have exactly 2 trades (one before start_ms was filtered out)
    assert len(trades) == 2, f"Expected 2 trades, got {len(trades)}"
    assert trades[0]["ts_ms"] == start_ms


@pytest.mark.asyncio
async def test_fetch_trades_historical_with_end_ms(tmp_path, httpx_mock: HTTPXMock):
    """Historical zip fetch with end_ms should also filter upper bound.

    When end_ms is provided, trades should be in range [start_ms, end_ms].
    """
    # 2024-01-05 12:00:00 UTC
    start_ms = 1_704_441_600_000
    # 2024-01-05 18:00:00 UTC — mid-day cutoff
    end_ms = 1_704_463_200_000
    # 2024-01-05 23:59:59:000 UTC
    end_of_day_ms = 1_704_499_199_000

    worker = BinanceWorker()

    csv_bytes = _make_agg_trades_csv([
        (1, "68000.0", "0.5", 1, 1, start_ms, False),
        (2, "68100.0", "0.3", 2, 2, end_ms - 1_000, True),
        (3, "68200.0", "0.2", 3, 3, end_ms + 1_000, False),  # After end_ms
        (4, "68300.0", "0.1", 4, 4, end_of_day_ms, True),     # After end_ms
    ])
    zip_bytes = _make_zip(csv_bytes, "BTCUSDT-aggTrades-2024-01-05.csv")

    zip_url = (
        "https://data.binance.vision/data/futures/um/daily/aggTrades"
        "/BTCUSDT/BTCUSDT-aggTrades-2024-01-05.zip"
    )
    httpx_mock.add_response(url=zip_url, content=zip_bytes)

    trades = await worker.fetch_trades(
        "BTCUSDT", "linear_perp", start_ms, end_ms=end_ms, data_path=tmp_path
    )

    # All trades must be in range [start_ms, end_ms]
    assert all(start_ms <= t["ts_ms"] <= end_ms for t in trades), (
        "All trades must be in range [start_ms, end_ms]"
    )
    # Should have exactly 2 trades
    assert len(trades) == 2, f"Expected 2 trades, got {len(trades)}"


@pytest.fixture
def worker():
    return BinanceWorker()
