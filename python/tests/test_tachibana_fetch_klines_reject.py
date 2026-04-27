"""TDD tests for fetch_klines rejection scenarios.

Covers:
- HIGH-U-11: non-1d timeframe is rejected at the worker boundary
- M-1: invalid sDate rows are skipped with debug log
- M-2: non-dict API response raises TachibanaError(parse_error)
- M-3: OHLC empty-string rows are skipped
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from engine.exchanges.tachibana import TachibanaWorker, VenueCapabilityError
from engine.exchanges.tachibana_auth import TachibanaSession
from engine.exchanges.tachibana_helpers import TachibanaError
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
            '"aCLMMfdsMarketPriceHistory":['
            '{"sDate":"20260424","pDOP":"2860","pDHP":"2900","pDLP":"2800","pDPP":"2880","pDV":"123456"}'
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


@pytest.mark.asyncio
async def test_fetch_klines_sends_sIssueCode_not_sTargetIssueCode(tmp_path: Path):
    """Regression guard: CLMMfdsGetMarketPriceHistory requires sIssueCode/sSizyouC,
    not sTargetIssueCode/sTargetSizyouC (API returns error -1 if wrong param used)."""
    from urllib.parse import unquote

    worker = _stubbed_worker(tmp_path)
    captured_urls: list[str] = []

    async def _fake_get(url: str) -> bytes:
        captured_urls.append(url)
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPriceHistory","sResultCode":"0",'
            '"aCLMMfdsMarketPriceHistory":[]}'
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]
    await worker.fetch_klines("7203", "stock", "1d", limit=1)

    assert len(captured_urls) == 1
    decoded = unquote(captured_urls[0])
    assert '"sIssueCode"' in decoded, f"sIssueCode missing from payload: {decoded}"
    assert '"sTargetIssueCode"' not in decoded, f"sTargetIssueCode must not appear: {decoded}"
    assert '"sSizyouC"' in decoded, f"sSizyouC missing from payload: {decoded}"
    assert '"sTargetSizyouC"' not in decoded, f"sTargetSizyouC must not appear: {decoded}"


# ---------------------------------------------------------------------------
# M-1: invalid sDate rows are skipped; valid rows are returned; log emitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_skips_invalid_sdate_and_returns_valid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """M-1: row with bad sDate is skipped with a debug log; valid row still returned."""
    worker = _stubbed_worker(tmp_path)

    async def _fake_get(url: str) -> bytes:
        body = (
            '{"sCLMID":"CLMMfdsGetMarketPriceHistory","sResultCode":"0",'
            '"aCLMMfdsMarketPriceHistory":['
            '{"sDate":"BADDATE","pDOP":"100","pDHP":"110","pDLP":"90","pDPP":"105","pDV":"1000"},'
            '{"sDate":"20260424","pDOP":"2860","pDHP":"2900","pDLP":"2800","pDPP":"2880","pDV":"123456"}'
            "]}"
        )
        return body.encode("shift_jis")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]

    with caplog.at_level(logging.DEBUG, logger="engine.exchanges.tachibana"):
        result = await worker.fetch_klines("7203", "stock", "1d", limit=10)

    # Only the valid row is returned.
    assert len(result) == 1
    assert result[0]["open"] == "2860"

    # A debug log mentioning the bad sDate must have been emitted.
    assert any(
        "skipped" in r.message and "BADDATE" in r.message
        for r in caplog.records
        if r.levelno == logging.DEBUG
    ), f"Expected skip-log not found in: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# M-2: non-dict API response raises TachibanaError(parse_error)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_klines_raises_parse_error_on_list_response(tmp_path: Path) -> None:
    """M-2: when the API returns a JSON array instead of a dict, TachibanaError is raised."""
    worker = _stubbed_worker(tmp_path)

    async def _fake_get(_url: str) -> bytes:
        return b"[1, 2, 3]"

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]

    with pytest.raises(TachibanaError) as excinfo:
        await worker.fetch_klines("7203", "stock", "1d", limit=10)

    assert excinfo.value.code == "parse_error"
    assert "dict" in excinfo.value.message.lower()


# ---------------------------------------------------------------------------
# M-3: OHLC empty-string rows are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "empty_field",
    ["pDOP", "pDHP", "pDLP", "pDPP"],
)
async def test_fetch_klines_skips_row_with_empty_ohlc_field(
    tmp_path: Path, empty_field: str
) -> None:
    """M-3: a row where any of OHLC is empty string is skipped; valid sibling row returned."""
    worker = _stubbed_worker(tmp_path)

    base = {"pDOP": "100", "pDHP": "110", "pDLP": "90", "pDPP": "105", "pDV": "500"}
    bad = {**base, empty_field: ""}

    import json as _json

    rows = [
        bad,
        {"sDate": "20260424", **base},
    ]
    # Attach sDate only to bad row too so the date parse succeeds and we reach OHLC check.
    rows[0]["sDate"] = "20260423"

    async def _fake_get(url: str) -> bytes:
        body = _json.dumps(
            {
                "sCLMID": "CLMMfdsGetMarketPriceHistory",
                "sResultCode": "0",
                "aCLMMfdsMarketPriceHistory": rows,
            }
        )
        return body.encode("utf-8")

    worker._http_get = AsyncMock(side_effect=_fake_get)  # type: ignore[method-assign]

    result = await worker.fetch_klines("7203", "stock", "1d", limit=10)

    # Only the row with all OHLC populated must survive.
    assert len(result) == 1
    assert result[0]["open"] == "100"
