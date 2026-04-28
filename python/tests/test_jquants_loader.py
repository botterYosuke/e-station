"""J-Quants ローダ テスト (N1.2)

data-mapping.md §1.3 / §2 / §8 の写像を検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.nautilus.jquants_loader import (
    jquants_code_to_instrument_id,
    load_daily_bars,
    load_minute_bars,
    load_trades,
)
from nautilus_trader.model.enums import AggressorSide

FIXTURES = Path(__file__).parent / "fixtures"


class TestInstrumentIdMapping:
    def test_normal_5digit_code(self) -> None:
        assert jquants_code_to_instrument_id("13010") == "1301.TSE"

    def test_non_zero_check_digit_raises(self) -> None:
        with pytest.raises(ValueError, match="does not end with 0"):
            jquants_code_to_instrument_id("12345")

    def test_wrong_length_raises(self) -> None:
        with pytest.raises(ValueError, match="length"):
            jquants_code_to_instrument_id("123")


class TestLoadTrades:
    def test_filters_by_instrument_id(self) -> None:
        # fixture には 1301.TSE と 1305.TSE が入っている。1301 のみ通す
        ticks = list(
            load_trades(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-05",
                base_dir=FIXTURES,
            )
        )
        assert len(ticks) == 4  # 1301 のみ 4 行
        for t in ticks:
            assert str(t.instrument_id) == "1301.TSE"

    def test_filters_by_date_range(self) -> None:
        # 2024-01-04 だけ
        ticks = list(
            load_trades(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-04",
                base_dir=FIXTURES,
            )
        )
        assert len(ticks) == 3  # 2024-01-04 の 1301 は 3 行
        for t in ticks:
            # ts_event は JST 09:00〜12:30 → UTC 00:00〜03:30 ⇒ 全て 2024-01-03 (UTC) 〜 2024-01-04 (UTC)
            # でも内訳は変わらず 3 件
            pass

    def test_crosses_month_boundary(self) -> None:
        # 1/30 から 2/2 にまたがる
        ticks = list(
            load_trades(
                "1301.TSE",
                start_date="2024-01-30",
                end_date="2024-02-01",
                base_dir=FIXTURES,
            )
        )
        # 202401 ファイル: 1301 は 1/4, 1/5 のみ → 0 件
        # 202402 ファイル: 1301 は 2024-02-01 の 2 行
        assert len(ticks) == 2

    def test_microsecond_precision_ts_event(self) -> None:
        ticks = list(
            load_trades(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-04",
                base_dir=FIXTURES,
            )
        )
        # 最初の行 09:00:00.165806 JST = 00:00:00.165806 UTC = 2024-01-04T00:00:00.165806 UTC
        import datetime as dt
        JST = dt.timezone(dt.timedelta(hours=9))
        expected = dt.datetime(
            2024, 1, 4, 9, 0, 0, 165806, tzinfo=JST
        )
        expected_ns = int(expected.timestamp() * 1_000_000) * 1000
        # 9 桁 ns で正確に一致
        assert ticks[0].ts_event == expected_ns

    def test_aggressor_side_is_no_aggressor(self) -> None:
        ticks = list(
            load_trades(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-05",
                base_dir=FIXTURES,
            )
        )
        assert len(ticks) > 0
        for t in ticks:
            assert t.aggressor_side == AggressorSide.NO_AGGRESSOR

    def test_trade_id_prefixed_with_R(self) -> None:
        ticks = list(
            load_trades(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-04",
                base_dir=FIXTURES,
            )
        )
        assert str(ticks[0].trade_id) == "R-000000000010"

    def test_missing_month_raises_when_no_data(self, tmp_path: Path) -> None:
        # 空ディレクトリでは 1 件もなし → FileNotFoundError
        with pytest.raises(FileNotFoundError):
            list(
                load_trades(
                    "1301.TSE",
                    start_date="2024-01-04",
                    end_date="2024-01-05",
                    base_dir=tmp_path,
                )
            )

    def test_missing_month_silently_skipped_when_other_months_exist(self) -> None:
        # 2024-01 と 2024-03 を要求するが 03 ファイルは fixture に無い
        # 01 のデータは取れること（warning だが raise しない）
        ticks = list(
            load_trades(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-03-31",
                base_dir=FIXTURES,
            )
        )
        # 01 (4 件) + 02 (2 件) = 6 件
        assert len(ticks) == 6


class TestLoadMinuteBars:
    def test_bar_ts_event_is_close_time(self) -> None:
        bars = list(
            load_minute_bars(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-04",
                base_dir=FIXTURES,
            )
        )
        assert len(bars) == 2  # 09:00, 09:01
        # 09:00 bar の close 時刻 = JST 09:00:59.999999999
        import datetime as dt
        JST = dt.timezone(dt.timedelta(hours=9))
        expected_close_jst = dt.datetime(2024, 1, 4, 9, 0, 59, tzinfo=JST)
        expected_ns = int(expected_close_jst.timestamp()) * 1_000_000_000 + 999_999_999
        assert bars[0].ts_event == expected_ns

    def test_filters_by_instrument(self) -> None:
        bars = list(
            load_minute_bars(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-05",
                base_dir=FIXTURES,
            )
        )
        for b in bars:
            assert str(b.bar_type.instrument_id) == "1301.TSE"
        assert len(bars) == 3  # 1301 only


class TestLoadDailyBars:
    def test_bar_ts_event_is_jst_15_30(self) -> None:
        bars = list(
            load_daily_bars(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-04",
                base_dir=FIXTURES,
            )
        )
        assert len(bars) == 1
        import datetime as dt
        JST = dt.timezone(dt.timedelta(hours=9))
        expected_jst = dt.datetime(2024, 1, 4, 15, 30, tzinfo=JST)
        expected_ns = int(expected_jst.timestamp() * 1_000_000_000)
        assert bars[0].ts_event == expected_ns

    def test_ohlc_values(self) -> None:
        bars = list(
            load_daily_bars(
                "1301.TSE",
                start_date="2024-01-04",
                end_date="2024-01-04",
                base_dir=FIXTURES,
            )
        )
        b = bars[0]
        assert str(b.open) == "3775.0"
        assert str(b.high) == "3825.0"
        assert str(b.low) == "3755.0"
        assert str(b.close) == "3815.0"
