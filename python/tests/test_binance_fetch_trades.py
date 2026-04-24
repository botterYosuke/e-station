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


@pytest.fixture
def worker():
    return BinanceWorker()
