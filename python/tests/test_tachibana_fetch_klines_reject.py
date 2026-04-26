"""TDD: HIGH-U-11 — non-1d timeframe fetch_klines is rejected at the worker boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from engine.exchanges.tachibana import TachibanaWorker, VenueCapabilityError
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


def _stubbed_worker(tmp_path: Path) -> TachibanaWorker:
    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True, session=_fake_session())

    async def _fake_download() -> None:
        worker._master_records = {
            "CLMIssueMstKabu": [{"sIssueCode": "7203", "sIssueName": "T"}],
            "CLMIssueSizyouMstKabu": [
                {"sIssueCode": "7203", "sSizyouC": "00", "sBaibaiTaniNumber": "100", "sYobineTaniNumber": "1"}
            ],
        }
        worker._yobine_table = {}

    worker._download_master = AsyncMock(side_effect=_fake_download)  # type: ignore[method-assign]
    return worker


@pytest.mark.asyncio
@pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "1h", "4h"])
async def test_fetch_klines_rejects_non_d1_timeframes(tmp_path: Path, timeframe: str):
    worker = _stubbed_worker(tmp_path)
    with pytest.raises(VenueCapabilityError) as excinfo:
        await worker.fetch_klines("7203", "stock", timeframe, limit=10)
    assert excinfo.value.code == "not_implemented"
    assert "1d" in excinfo.value.message


@pytest.mark.asyncio
async def test_fetch_klines_accepts_1d(tmp_path: Path):
    worker = _stubbed_worker(tmp_path)

    async def _fake_get(url: str) -> bytes:
        # MarketPriceHistoryResponse with one row.
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPriceHistory","sResultCode":"0",'
            '"aCLMMfdsMarketPriceHistoryData":['
            '{"sHFutureBA":"2860","sHFutureBB":"2900","sHFutureBC":"2800","sHFutureBD":"2880","sHFutureBE":"123456","sHFutureBF":"20260424"}'
            ']}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]

    result = await worker.fetch_klines("7203", "stock", "1d", limit=1)
    assert isinstance(result, list)
    assert len(result) == 1
    row = result[0]
    for key in ("open_time_ms", "open", "high", "low", "close", "volume"):
        assert key in row
