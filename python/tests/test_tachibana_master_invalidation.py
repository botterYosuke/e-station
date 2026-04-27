"""TDD: master in-memory invalidation regulation (HIGH-U-10, plan §T4 L548-552)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from engine.exchanges.tachibana import TachibanaWorker


def _stub_download(worker: TachibanaWorker) -> AsyncMock:
    async def _fake() -> None:
        worker._master_records = {
            "CLMIssueMstKabu": [{"sIssueCode": "7203", "sIssueName": "T"}],
            "CLMIssueSizyouMstKabu": [
                {"sIssueCode": "7203", "sSizyouC": "00", "sBaibaiTaniNumber": "100", "sYobineTaniNumber": "1"}
            ],
        }
        worker._yobine_table = {}

    mock = AsyncMock(side_effect=_fake)
    worker._download_master = mock  # type: ignore[method-assign]
    return mock


@pytest.mark.asyncio
async def test_is_demo_flip_triggers_master_reload(tmp_path: Path):
    """Cache key differs between demo and prod, but in-memory state must
    also flush so a freshly constructed worker pulls again."""
    worker_demo = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
    mock_demo = _stub_download(worker_demo)
    await worker_demo._ensure_master_loaded()
    assert mock_demo.await_count == 1

    worker_prod = TachibanaWorker(cache_dir=tmp_path, is_demo=False)
    mock_prod = _stub_download(worker_prod)
    assert worker_prod._master_loaded.is_set() is False
    await worker_prod._ensure_master_loaded()
    assert mock_prod.await_count == 1


@pytest.mark.asyncio
async def test_jst_date_rollover_invalidates_in_memory_master(tmp_path: Path):
    """A long-running worker must reload its in-memory master after JST midnight."""
    with freeze_time("2026-04-25 14:30:00"):
        worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
        mock = _stub_download(worker)
        await worker._ensure_master_loaded()
        assert mock.await_count == 1

    # Simulate explicit invalidation (e.g. detected at next list_tickers entry
    # via current_jst_yyyymmdd() comparison). The worker exposes
    # `invalidate_master()` for B3 tests / runtime callers.
    worker.invalidate_master()
    assert worker._master_loaded.is_set() is False

    with freeze_time("2026-04-25 15:30:00"):
        await worker._ensure_master_loaded()
        assert mock.await_count == 2


def test_worker_init_starts_with_fresh_event(tmp_path: Path):
    worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
    assert isinstance(worker._master_loaded, asyncio.Event)
    assert worker._master_loaded.is_set() is False
    assert isinstance(worker._master_lock, asyncio.Lock)
