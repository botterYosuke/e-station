"""Tests for nautilus DataLoader: Klines → nautilus Bar conversion (N0.3)."""

from __future__ import annotations

from datetime import datetime, timezone

from engine.nautilus.data_loader import klines_to_bars, KlineRow


# ---------------------------------------------------------------------------
# KlineRow → Bar 変換テスト
# ---------------------------------------------------------------------------


def _make_kline(
    date_str: str = "20240104",
    open_: str = "2000.0",
    high: str = "2100.0",
    low: str = "1900.0",
    close: str = "2050.0",
    volume: str = "5000",
) -> KlineRow:
    return KlineRow(
        date=date_str,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class TestKlineToBar:
    def test_bar_type_format(self):
        bars = klines_to_bars("7203", "TSE", [_make_kline()])
        assert len(bars) == 1
        assert str(bars[0].bar_type) == "7203.TSE-1-DAY-MID-EXTERNAL"

    def test_ts_event_jst_to_utc_ns(self):
        """YYYYMMDD の日足終値 = 15:30 JST = 06:30 UTC"""
        bars = klines_to_bars("7203", "TSE", [_make_kline(date_str="20240104")])
        bar = bars[0]
        # 2024-01-04 15:30 JST = 2024-01-04 06:30:00 UTC
        expected_dt = datetime(2024, 1, 4, 6, 30, 0, tzinfo=timezone.utc)
        expected_ns = int(expected_dt.timestamp() * 1_000_000_000)
        assert bar.ts_event == expected_ns
        assert bar.ts_init == expected_ns

    def test_ohlcv_precision_1(self):
        kline = _make_kline(open_="2000.0", high="2100.5", low="1900.1", close="2050.3", volume="5000")
        bars = klines_to_bars("7203", "TSE", [kline])
        bar = bars[0]
        assert str(bar.open) == "2000.0"
        assert str(bar.high) == "2100.5"
        assert str(bar.low) == "1900.1"
        assert str(bar.close) == "2050.3"
        assert str(bar.volume) == "5000"

    def test_volume_is_integer_quantity(self):
        bars = klines_to_bars("7203", "TSE", [_make_kline(volume="12345")])
        assert bars[0].volume.precision == 0

    def test_multiple_klines_sorted_by_ts(self):
        klines = [
            _make_kline(date_str="20240106"),
            _make_kline(date_str="20240104"),
            _make_kline(date_str="20240105"),
        ]
        bars = klines_to_bars("7203", "TSE", klines)
        assert len(bars) == 3
        # 時系列順になっているか
        assert bars[0].ts_event < bars[1].ts_event < bars[2].ts_event

    def test_empty_klines_returns_empty(self):
        bars = klines_to_bars("7203", "TSE", [])
        assert bars == []

    def test_boundary_price_string_conversion(self):
        """文字列 → Decimal → nautilus Price: 精度が失われないこと"""
        kline = _make_kline(open_="100000.0", high="110000.0", low="95000.0", close="100000.0")
        bars = klines_to_bars("7203", "TSE", [kline])
        assert str(bars[0].close) == "100000.0"


# ---------------------------------------------------------------------------
# H-1: KlineRow バリデーションテスト
# ---------------------------------------------------------------------------

class TestKlineRowValidation:
    def test_invalid_date_raises_value_error(self):
        """date が 8 桁数字でない場合は ValueError"""
        import pytest
        with pytest.raises(ValueError, match="KlineRow.date"):
            KlineRow(date="2024-01-01", open="2000.0", high="2100.0", low="1900.0", close="2050.0", volume="5000")

    def test_non_numeric_open_raises_value_error(self):
        """open が Decimal 変換不可な場合は ValueError"""
        import pytest
        with pytest.raises(ValueError, match="KlineRow.open"):
            KlineRow(date="20240101", open="N/A", high="2100.0", low="1900.0", close="2050.0", volume="5000")

    def test_valid_kline_does_not_raise(self):
        """正常な KlineRow は例外なし"""
        row = KlineRow(date="20240101", open="2000.0", high="2100.0", low="1900.0", close="2050.0", volume="5000")
        assert row.date == "20240101"

    def test_kline_is_immutable(self):
        """frozen=True でイミュータブルであること"""
        import pytest
        row = KlineRow(date="20240101", open="2000.0", high="2100.0", low="1900.0", close="2050.0", volume="5000")
        with pytest.raises((AttributeError, TypeError)):
            row.date = "20240102"  # type: ignore[misc]
