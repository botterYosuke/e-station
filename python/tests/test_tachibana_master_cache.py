"""TDD: master cache file path uses JST date (HIGH-D3, plan §T4 L531/L534)."""

from __future__ import annotations

from pathlib import Path

import pytest
from freezegun import freeze_time

from engine.exchanges.tachibana import (
    TachibanaWorker,
    master_cache_path,
)


# UTC 14:30 == JST 23:30 (same calendar day) ; UTC 15:30 == JST 00:30 (next day)


@freeze_time("2026-04-25 14:30:00")
def test_jst_date_boundary_before_midnight(tmp_path: Path):
    p = master_cache_path(tmp_path, env="demo")
    assert p.name == "master_demo_20260425.jsonl"


@freeze_time("2026-04-25 15:30:00")
def test_jst_date_boundary_after_midnight(tmp_path: Path):
    p = master_cache_path(tmp_path, env="demo")
    assert p.name == "master_demo_20260426.jsonl"


@pytest.mark.asyncio
async def test_cache_invalid_after_jst_rollover(tmp_path: Path):
    """Cache from JST yesterday must NOT be reused after JST rollover."""
    worker_dir = tmp_path
    cache_dir = worker_dir / "tachibana"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Write a stale cache file dated for JST 2026-04-25.
    stale = cache_dir / "master_demo_20260425.jsonl"
    stale.write_text('{"sCLMID":"CLMIssueMstKabu","sIssueCode":"9999"}\n', encoding="utf-8")

    # Simulate JST 2026-04-26 00:30 (= UTC 2026-04-25 15:30) startup.
    with freeze_time("2026-04-25 15:30:00"):
        worker = TachibanaWorker(cache_dir=worker_dir, is_demo=True)
        loaded = worker._try_load_cached_master()
        assert loaded is False, (
            "JST-yesterday cache file must not be treated as today's cache"
        )


@pytest.mark.asyncio
async def test_cache_used_when_today_file_present(tmp_path: Path):
    cache_dir = tmp_path / "tachibana"
    cache_dir.mkdir(parents=True, exist_ok=True)

    with freeze_time("2026-04-25 14:30:00"):
        # Today (JST 2026-04-25)
        path = cache_dir / "master_demo_20260425.jsonl"
        path.write_text(
            '{"sCLMID":"CLMIssueMstKabu","sIssueCode":"7203","sIssueName":"\\u30c8\\u30e8\\u30bf"}\n',
            encoding="utf-8",
        )
        worker = TachibanaWorker(cache_dir=tmp_path, is_demo=True)
        loaded = worker._try_load_cached_master()
        assert loaded is True
        assert "CLMIssueMstKabu" in worker._master_records
        assert worker._master_records["CLMIssueMstKabu"][0]["sIssueCode"] == "7203"
