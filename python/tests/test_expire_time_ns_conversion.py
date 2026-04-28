"""C5: expire_time_ns → JST YYYYMMDD 変換テスト。

_expire_ns_to_jst_yyyymmdd() は UTC nanoseconds を JST の日付文字列に変換する
（architecture.md §10.2 GTD）。UTC と JST の +9 時間のオフセットにより、
UTC の夜（20:00 以降）は JST では翌日になることを検証する。
"""

from datetime import datetime, timezone

import pytest

from engine.exchanges.tachibana_orders import _expire_ns_to_jst_yyyymmdd


class TestExpireTimeNsToJstYyyymmdd:
    def test_utc_midnight_stays_same_jst_day(self):
        """UTC 2024-01-15 00:00:00 → JST 2024-01-15 09:00:00 → "20240115"。"""
        dt_utc = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert result == "20240115"

    def test_utc_20_00_converts_to_jst_next_day(self):
        """UTC 2024-01-15 20:00:00 → JST 2024-01-16 05:00:00 → "20240116"。"""
        dt_utc = datetime(2024, 1, 15, 20, 0, 0, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert result == "20240116"

    def test_utc_15_00_converts_to_jst_next_day_at_midnight(self):
        """UTC 2024-03-20 15:00:00 → JST 2024-03-21 00:00:00 → "20240321"。"""
        dt_utc = datetime(2024, 3, 20, 15, 0, 0, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert result == "20240321"

    def test_utc_just_before_15_00_stays_same_jst_day(self):
        """UTC 2024-03-20 14:59:59 → JST 2024-03-20 23:59:59 → "20240320"（境界直前）。"""
        dt_utc = datetime(2024, 3, 20, 14, 59, 59, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert result == "20240320"

    def test_format_is_yyyymmdd(self):
        """返り値が 8 桁の数字文字列であること。"""
        dt_utc = datetime(2024, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert len(result) == 8, f"Expected 8-char string, got {result!r} (len={len(result)})"
        assert result.isdigit(), f"Expected all-digit string, got {result!r}"
        assert result == "20241231"

    def test_year_month_day_boundary_new_year(self):
        """UTC 2024-12-31 15:00:00 → JST 2025-01-01 00:00:00 → "20250101"（年越し境界）。"""
        dt_utc = datetime(2024, 12, 31, 15, 0, 0, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert result == "20250101"

    def test_utc_08_59_59_stays_same_jst_day(self):
        """UTC 2024-06-10 08:59:59 → JST 2024-06-10 17:59:59 → "20240610"（夜間だが翌日ではない）。"""
        dt_utc = datetime(2024, 6, 10, 8, 59, 59, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert result == "20240610"

    def test_large_ns_value(self):
        """大きな nanosecond 値でも正しく変換できること。"""
        dt_utc = datetime(2099, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
        ns = int(dt_utc.timestamp() * 1_000_000_000)
        result = _expire_ns_to_jst_yyyymmdd(ns)
        assert result == "20991231"
        assert len(result) == 8
        assert result.isdigit()
