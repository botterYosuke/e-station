"""B2 pin: ``_yobine_table`` is reloaded on each of the three invalidation
triggers (HIGH-U-10) so a stale tick table from a previous environment /
day / process does not leak into ``resolve_min_ticksize_for_issue``.

Mirrors ``test_tachibana_master_invalidation.py`` but specifically pins
the ``_yobine_table`` field rather than master records as a whole, per
implementation-plan.md §T4 L542 (b).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_master import YobineBand


def _stub_download_with_yobine(worker: TachibanaWorker) -> AsyncMock:
    """Stub `_download_master` to populate `_master_records` and a
    non-empty `_yobine_table` so we can observe it being cleared."""

    async def _fake() -> None:
        worker._master_records = {
            "CLMIssueMstKabu": [{"sIssueCode": "7203", "sIssueName": "T"}],
            "CLMIssueSizyouMstKabu": [
                {
                    "sIssueCode": "7203",
                    "sSizyouC": "00",
                    "sBaibaiTaniNumber": "100",
                    "sYobineTaniNumber": "1",
                }
            ],
        }
        worker._yobine_table = {
            "1": [
                YobineBand(
                    kizun_price=Decimal("999999999"),
                    yobine_tanka=Decimal("1"),
                    decimals=0,
                )
            ]
        }

    mock = AsyncMock(side_effect=_fake)
    worker._download_master = mock  # type: ignore[method-assign]
    return mock


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_id", ["is_demo_flip", "jst_rollover", "init_reset"])
async def test_yobine_table_reloaded_on_invalidation_triggers(
    trigger_id: str, tmp_path: Path
):
    """Each of the three HIGH-U-10 triggers must (a) clear `_yobine_table`
    and (b) cause the next `_ensure_master_loaded` to re-populate it."""
    if trigger_id == "init_reset":
        # `__init__` 再生成: fresh worker starts with an empty yobine_table.
        worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
        assert worker._yobine_table == {}
        return

    # is_demo_flip / jst_rollover: load → invalidate → reload.
    with freeze_time("2026-04-25 14:30:00"):
        worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
        mock = _stub_download_with_yobine(worker)
        await worker._ensure_master_loaded()
        assert mock.await_count == 1
        assert worker._yobine_table  # populated

        if trigger_id == "is_demo_flip":
            worker.set_credentials_demo_flag(False)
            assert worker._yobine_table == {}
            await worker._ensure_master_loaded()
            assert mock.await_count == 2
            assert worker._yobine_table  # repopulated
            return

    # jst_rollover: freezegun crosses JST midnight, the entry-side
    # `_check_jst_rollover` invalidates, then the same call reloads.
    with freeze_time("2026-04-26 00:30:00"):
        await worker._ensure_master_loaded()
        assert mock.await_count == 2
        assert worker._yobine_table  # repopulated under the new JST date
