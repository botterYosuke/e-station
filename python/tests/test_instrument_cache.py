"""InstrumentCache テスト (N1.2)

Q10 案 B + 案 A fallback の永続化動作を検証する。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from engine.nautilus.instrument_cache import InstrumentCache


class TestInstrumentCacheFallback:
    def test_cache_miss_returns_fallback_lot_size_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cache = InstrumentCache(cache_path=tmp_path / "master.json")
        with caplog.at_level(logging.WARNING):
            assert cache.get_lot_size("9999.TSE") == 100
        assert any(
            "9999.TSE" in r.getMessage() and "fallback" in r.getMessage()
            for r in caplog.records
        )

    def test_cache_miss_returns_fallback_price_precision(
        self, tmp_path: Path
    ) -> None:
        cache = InstrumentCache(cache_path=tmp_path / "master.json")
        # Q8 案 A: price_precision は当面 1 固定
        assert cache.get_price_precision("9999.TSE") == 1


class TestInstrumentCacheLiveUpdate:
    def test_update_from_live_then_get_returns_cached(
        self, tmp_path: Path
    ) -> None:
        cache = InstrumentCache(cache_path=tmp_path / "master.json")
        cache.update_from_live("1301.TSE", lot_size=1, price_precision=1)
        assert cache.get_lot_size("1301.TSE") == 1

    def test_override_takes_precedence_over_cache(self, tmp_path: Path) -> None:
        cache = InstrumentCache(cache_path=tmp_path / "master.json")
        cache.update_from_live("1301.TSE", lot_size=1, price_precision=1)
        result = cache.get_lot_size("1301.TSE", override={"1301.TSE": 50})
        assert result == 50


class TestInstrumentCachePersistence:
    def test_persists_across_instances(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "master.json"
        cache1 = InstrumentCache(cache_path=cache_path)
        cache1.update_from_live("7203.TSE", lot_size=100, price_precision=1)
        # 別インスタンスから再ロード
        cache2 = InstrumentCache(cache_path=cache_path)
        assert cache2.get_lot_size("7203.TSE") == 100

    def test_atomic_write_uses_tmp_then_rename(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "master.json"
        cache = InstrumentCache(cache_path=cache_path)
        cache.update_from_live("7203.TSE", lot_size=100, price_precision=1)
        # tmp ファイルが残っていない
        assert not (cache_path.parent / (cache_path.name + ".tmp")).exists()
        # 本ファイルは正しい JSON
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert "7203.TSE" in data["instruments"]

    def test_corrupted_file_treated_as_empty(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "master.json"
        cache_path.write_text("{not valid json", encoding="utf-8")
        cache = InstrumentCache(cache_path=cache_path)
        # 壊れていても fallback で動作
        assert cache.get_lot_size("9999.TSE") == 100
