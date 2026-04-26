"""TDD: TachibanaWorker — concurrent callers trigger a single master DL (MEDIUM-D2).

Verifies the `asyncio.Lock` + `asyncio.Event` double-checked pattern in
`_ensure_master_loaded` (plan §T4 L513-524). `asyncio.Event` alone is not
enough: two concurrent callers can both observe `is_set() == False` before
the first download completes and end up downloading twice.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from engine.exchanges.tachibana import TachibanaWorker


@pytest.mark.asyncio
async def test_concurrent_callers_trigger_single_download(tmp_path):
    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)

    # Replace _download_master with an AsyncMock that simulates a slow DL so
    # that two callers can race the lock.
    download_calls = 0

    async def fake_download() -> None:
        nonlocal download_calls
        download_calls += 1
        # yield once so the second concurrent caller actually races
        await asyncio.sleep(0.05)
        # populate minimum master state so list_tickers / fetch_ticker_stats
        # do not need to hit the network.
        worker._master_records = {
            "CLMIssueMstKabu": [{"sIssueCode": "7203", "sIssueName": "トヨタ"}],
            "CLMIssueSizyouMstKabu": [
                {"sIssueCode": "7203", "sSizyouC": "00", "sBaibaiTaniNumber": "100", "sYobineTaniNumber": "1"}
            ],
        }
        worker._yobine_table = {}

    worker._download_master = AsyncMock(side_effect=fake_download)  # type: ignore[method-assign]

    # Two concurrent callers — list_tickers and fetch_ticker_stats both call
    # _ensure_master_loaded internally.
    async def caller_a():
        await worker._ensure_master_loaded()

    async def caller_b():
        await worker._ensure_master_loaded()

    await asyncio.gather(caller_a(), caller_b())

    assert worker._download_master.await_count == 1
    assert worker._master_loaded.is_set() is True
