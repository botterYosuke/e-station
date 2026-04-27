"""TDD: fetch_ticker_stats("__all__") bulk placeholder path."""

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


def _worker_with_master(
    tmp_path: Path,
    sizyou_rows: list[dict],
) -> TachibanaWorker:
    """Create a TachibanaWorker with a pre-loaded master containing the given sizyou_rows."""
    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True, session=_fake_session())

    async def _fake_download() -> None:
        worker._master_records = {
            "CLMIssueSizyouMstKabu": sizyou_rows,
        }

    worker._download_master = AsyncMock(side_effect=_fake_download)  # type: ignore[method-assign]
    return worker


@pytest.mark.asyncio
async def test_fetch_ticker_stats_all_returns_bulk_placeholders(tmp_path: Path):
    """__all__ path returns a dict keyed by issue code with zero-valued stats."""
    worker = _worker_with_master(
        tmp_path,
        sizyou_rows=[
            {"sIssueCode": "7203", "sSizyouC": "00"},
            {"sIssueCode": "9984", "sSizyouC": "00"},
        ],
    )
    result = await worker.fetch_ticker_stats("__all__", "stock")

    assert isinstance(result, dict)
    assert set(result.keys()) == {"7203", "9984"}
    for code in ("7203", "9984"):
        stats = result[code]
        assert stats["mark_price"] == 0
        assert stats["daily_price_chg"] == 0
        assert stats["daily_volume"] == 0


@pytest.mark.asyncio
async def test_fetch_ticker_stats_all_returns_empty_when_master_empty(tmp_path: Path):
    """__all__ path returns an empty dict when CLMIssueSizyouMstKabu is not loaded."""
    worker = _worker_with_master(tmp_path, sizyou_rows=[])
    result = await worker.fetch_ticker_stats("__all__", "stock")

    assert result == {}


@pytest.mark.asyncio
async def test_fetch_ticker_stats_all_skips_empty_issue_code(tmp_path: Path):
    """__all__ path skips rows where sIssueCode is empty or whitespace-only."""
    worker = _worker_with_master(
        tmp_path,
        sizyou_rows=[
            {"sIssueCode": "7203", "sSizyouC": "00"},
            {"sIssueCode": "", "sSizyouC": "00"},
            {"sIssueCode": "   ", "sSizyouC": "00"},
        ],
    )
    result = await worker.fetch_ticker_stats("__all__", "stock")

    assert set(result.keys()) == {"7203"}, (
        "Rows with empty or whitespace-only sIssueCode must be skipped"
    )
