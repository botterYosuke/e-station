"""TDD: TachibanaWorker happy paths (B2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl


def _fake_session() -> TachibanaSession:
    return TachibanaSession(
        url_request=RequestUrl("https://example.test/request/"),
        url_master=MasterUrl("https://example.test/master/"),
        url_price=PriceUrl("https://example.test/price/"),
        url_event=EventUrl("https://example.test/event/"),
        url_event_ws="wss://example.test/event/",
        zyoutoeki_kazei_c="",
    )


def _make_master(worker: TachibanaWorker) -> None:
    worker._master_records = {
        "CLMIssueMstKabu": [
            {"sIssueCode": "7203", "sIssueName": "トヨタ自動車", "sIssueNameEizi": "TOYOTA MOTOR"},
            {"sIssueCode": "130A0", "sIssueName": "テスト英数銘柄", "sIssueNameEizi": "TEST ALPHA"},
        ],
        "CLMIssueSizyouMstKabu": [
            {
                "sIssueCode": "7203",
                "sSizyouC": "00",
                "sBaibaiTaniNumber": "100",
                "sYobineTaniNumber": "1",
            },
            {
                "sIssueCode": "130A0",
                "sSizyouC": "00",
                "sBaibaiTaniNumber": "100",
                "sYobineTaniNumber": "1",
            },
        ],
    }
    worker._yobine_table = {}


def _stubbed(tmp_path: Path) -> TachibanaWorker:
    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True, session=_fake_session())

    async def _fake() -> None:
        _make_master(worker)

    worker._download_master = AsyncMock(side_effect=_fake)  # type: ignore[method-assign]
    return worker


@pytest.mark.asyncio
async def test_list_tickers_loads_master_lazily(tmp_path: Path):
    worker = _stubbed(tmp_path)
    assert worker._master_loaded.is_set() is False
    tickers = await worker.list_tickers("stock")
    assert worker._master_loaded.is_set() is True
    assert len(tickers) >= 1


@pytest.mark.asyncio
async def test_list_tickers_includes_display_name_ja_key(tmp_path: Path):
    worker = _stubbed(tmp_path)
    tickers = await worker.list_tickers("stock")
    by_symbol = {t["symbol"]: t for t in tickers}
    assert "display_name_ja" in by_symbol["7203"]
    assert by_symbol["7203"]["display_name_ja"] == "トヨタ自動車"


@pytest.mark.asyncio
async def test_list_tickers_includes_yobine_code(tmp_path: Path):
    worker = _stubbed(tmp_path)
    tickers = await worker.list_tickers("stock")
    for t in tickers:
        assert "yobine_code" in t


@pytest.mark.asyncio
async def test_list_tickers_includes_quote_currency_jpy(tmp_path: Path):
    worker = _stubbed(tmp_path)
    tickers = await worker.list_tickers("stock")
    for t in tickers:
        assert t["quote_currency"] == "JPY"


@pytest.mark.asyncio
async def test_list_tickers_includes_alphanumeric_ticker_130A0(tmp_path: Path):
    worker = _stubbed(tmp_path)
    tickers = await worker.list_tickers("stock")
    symbols = [t["symbol"] for t in tickers]
    assert "130A0" in symbols


@pytest.mark.asyncio
async def test_fetch_ticker_stats_returns_dict(tmp_path: Path):
    worker = _stubbed(tmp_path)

    async def _fake_get(url: str) -> bytes:
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPrice","sResultCode":"0",'
            '"aCLMMfdsMarketPriceData":['
            '{"sIssueCode":"7203","sCurrentPrice":"2880","sCurrentPriceTime":"15:00:00",'
            '"sOpenPrice":"2860","sHighPrice":"2900","sLowPrice":"2800","sVolume":"1234567"}'
            ']}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]
    stats = await worker.fetch_ticker_stats("7203", "stock")
    assert isinstance(stats, dict)
    assert "last_price" in stats or "close" in stats


@pytest.mark.asyncio
async def test_fetch_klines_d1_returns_kline_list(tmp_path: Path):
    worker = _stubbed(tmp_path)

    async def _fake_get(url: str) -> bytes:
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPriceHistory","sResultCode":"0",'
            '"aCLMMfdsMarketPriceHistoryData":['
            '{"sHFutureBA":"2860","sHFutureBB":"2900","sHFutureBC":"2800","sHFutureBD":"2880","sHFutureBE":"123456","sHFutureBF":"20260424"},'
            '{"sHFutureBA":"2870","sHFutureBB":"2890","sHFutureBC":"2810","sHFutureBD":"2880","sHFutureBE":"222222","sHFutureBF":"20260425"}'
            ']}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]
    rows = await worker.fetch_klines("7203", "stock", "1d", limit=2)
    assert isinstance(rows, list) and len(rows) == 2
    for row in rows:
        for key in ("open_time_ms", "open", "high", "low", "close", "volume"):
            assert key in row


@pytest.mark.asyncio
async def test_unimplemented_streams_raise_not_implemented(tmp_path: Path):
    """ABC residual: stream_* / fetch_open_interest / fetch_depth_snapshot."""
    worker = _stubbed(tmp_path)
    with pytest.raises(NotImplementedError):
        await worker.fetch_depth_snapshot("7203", "stock")
    with pytest.raises(NotImplementedError):
        await worker.fetch_open_interest("7203", "stock", "1d")
