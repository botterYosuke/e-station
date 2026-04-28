"""N1.11: replay pacing ロジック (replay_speed.py) のテスト。

D7 pacing 式と境界値、streaming replay の完走・決定論性を検証する。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from engine.nautilus.replay_speed import (
    MIN_TICK_DT_SEC,
    SLEEP_CAP_SEC,
    compute_sleep_sec,
    is_market_break,
    is_new_trading_day,
)

_JST = timezone(timedelta(hours=9))
FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# compute_sleep_sec — D7 pacing 式
# ---------------------------------------------------------------------------


class TestComputeSleepSecMultiplier:
    def test_multiplier_10_divides_sleep(self) -> None:
        """multiplier=10 で sleep が 1/10 になること。"""
        s = compute_sleep_sec(dt_event_sec=1.0, multiplier=10)
        assert abs(s - 0.1) < 1e-9

    def test_multiplier_1_returns_capped_sleep(self) -> None:
        """multiplier=1, dt=1.0 → min(1.0, 0.2) = 0.2 (SLEEP_CAP_SEC)。"""
        s = compute_sleep_sec(dt_event_sec=1.0, multiplier=1)
        assert s == SLEEP_CAP_SEC

    def test_multiplier_100_divides_sleep(self) -> None:
        """multiplier=100, dt=0.5 → 0.5/100 = 0.005。"""
        s = compute_sleep_sec(dt_event_sec=0.5, multiplier=100)
        assert abs(s - 0.005) < 1e-9

    def test_multiplier_zero_raises(self) -> None:
        """multiplier=0 は ValueError を出すこと。"""
        with pytest.raises(ValueError):
            compute_sleep_sec(dt_event_sec=1.0, multiplier=0)

    def test_multiplier_negative_raises(self) -> None:
        """multiplier=-1 は ValueError を出すこと。"""
        with pytest.raises(ValueError):
            compute_sleep_sec(dt_event_sec=1.0, multiplier=-1)


class TestComputeSleepSecMinTickDt:
    def test_zero_dt_uses_min_tick_dt(self) -> None:
        """同一マイクロ秒でも MIN_TICK_DT_SEC=1ms が下限になること。"""
        s = compute_sleep_sec(dt_event_sec=0.0, multiplier=1)
        assert s == MIN_TICK_DT_SEC

    def test_negative_dt_uses_min_tick_dt(self) -> None:
        """負の dt（時刻逆転）でも MIN_TICK_DT_SEC が下限になること。"""
        s = compute_sleep_sec(dt_event_sec=-1.0, multiplier=1)
        assert s == MIN_TICK_DT_SEC

    def test_tiny_dt_uses_min_tick_dt(self) -> None:
        """dt < MIN_TICK_DT_SEC のとき MIN_TICK_DT_SEC が下限になること。"""
        s = compute_sleep_sec(dt_event_sec=0.0005, multiplier=1)
        assert s == MIN_TICK_DT_SEC


class TestComputeSleepSecCap:
    def test_large_dt_capped_at_sleep_cap(self) -> None:
        """1 sleep が SLEEP_CAP_SEC=200ms を超えないこと。"""
        s = compute_sleep_sec(dt_event_sec=10.0, multiplier=1)
        assert s == SLEEP_CAP_SEC

    def test_cap_applies_after_multiplier(self) -> None:
        """cap は multiplier 適用後に適用されること: dt=1.0, mult=1 → 0.2 (not 1.0)。"""
        s = compute_sleep_sec(dt_event_sec=1.0, multiplier=1)
        assert s == SLEEP_CAP_SEC

    def test_no_cap_when_within_limit(self) -> None:
        """結果が cap 以下のとき cap は作用しないこと。"""
        s = compute_sleep_sec(dt_event_sec=0.1, multiplier=1)
        assert abs(s - 0.1) < 1e-9


class TestComputeSleepSecMarketBreak:
    def test_market_break_returns_zero_sleep(self) -> None:
        """11:30〜12:30 JST の tick で sleep=0 になること。"""
        # 11:45 JST
        ts = int(datetime(2024, 1, 4, 11, 45, 0, tzinfo=_JST).timestamp() * 1e9)
        s = compute_sleep_sec(dt_event_sec=1.0, multiplier=1, ts_event_ns=ts)
        assert s == 0.0

    def test_market_break_start_boundary_returns_zero(self) -> None:
        """11:30:00 JST（境界値）で sleep=0 になること。"""
        ts = int(datetime(2024, 1, 4, 11, 30, 0, tzinfo=_JST).timestamp() * 1e9)
        s = compute_sleep_sec(dt_event_sec=1.0, multiplier=1, ts_event_ns=ts)
        assert s == 0.0

    def test_market_break_end_boundary_returns_zero(self) -> None:
        """12:30:00 JST（境界値）で sleep=0 になること。"""
        ts = int(datetime(2024, 1, 4, 12, 30, 0, tzinfo=_JST).timestamp() * 1e9)
        s = compute_sleep_sec(dt_event_sec=1.0, multiplier=1, ts_event_ns=ts)
        assert s == 0.0

    def test_before_market_break_not_zero(self) -> None:
        """11:29:59 JST（ギャップ直前）は通常 pacing が適用されること。"""
        ts = int(datetime(2024, 1, 4, 11, 29, 59, tzinfo=_JST).timestamp() * 1e9)
        s = compute_sleep_sec(dt_event_sec=0.1, multiplier=1, ts_event_ns=ts)
        assert s > 0.0

    def test_after_market_break_not_zero(self) -> None:
        """12:30:01 JST（ギャップ直後）は通常 pacing が適用されること。"""
        ts = int(datetime(2024, 1, 4, 12, 30, 1, tzinfo=_JST).timestamp() * 1e9)
        s = compute_sleep_sec(dt_event_sec=0.1, multiplier=1, ts_event_ns=ts)
        assert s > 0.0

    def test_no_ts_event_ns_skips_market_break_check(self) -> None:
        """ts_event_ns が None のとき市場休憩チェックをスキップし通常計算すること。"""
        s = compute_sleep_sec(dt_event_sec=0.1, multiplier=1, ts_event_ns=None)
        assert abs(s - 0.1) < 1e-9


# ---------------------------------------------------------------------------
# is_market_break
# ---------------------------------------------------------------------------


class TestIsMarketBreak:
    def test_inside_break(self) -> None:
        """12:00 JST は市場休憩中。"""
        ts = int(datetime(2024, 1, 4, 12, 0, 0, tzinfo=_JST).timestamp() * 1e9)
        assert is_market_break(ts) is True

    def test_outside_break_morning(self) -> None:
        """10:00 JST は通常取引時間。"""
        ts = int(datetime(2024, 1, 4, 10, 0, 0, tzinfo=_JST).timestamp() * 1e9)
        assert is_market_break(ts) is False

    def test_outside_break_afternoon(self) -> None:
        """14:00 JST は通常取引時間（午後）。"""
        ts = int(datetime(2024, 1, 4, 14, 0, 0, tzinfo=_JST).timestamp() * 1e9)
        assert is_market_break(ts) is False


# ---------------------------------------------------------------------------
# is_new_trading_day
# ---------------------------------------------------------------------------


class TestIsNewTradingDay:
    def test_new_trading_day_emits_date_change(self) -> None:
        """営業日跨ぎで is_new_trading_day が True になること。"""
        prev = int(datetime(2024, 1, 4, 15, 30, 0, tzinfo=_JST).timestamp() * 1e9)
        curr = int(datetime(2024, 1, 5, 9, 0, 0, tzinfo=_JST).timestamp() * 1e9)
        assert is_new_trading_day(prev, curr) is True

    def test_same_day_returns_false(self) -> None:
        """同一営業日内は False を返すこと。"""
        prev = int(datetime(2024, 1, 4, 9, 0, 0, tzinfo=_JST).timestamp() * 1e9)
        curr = int(datetime(2024, 1, 4, 14, 0, 0, tzinfo=_JST).timestamp() * 1e9)
        assert is_new_trading_day(prev, curr) is False

    def test_prev_none_returns_false(self) -> None:
        """prev_ts_ns=None（最初の tick）は False を返すこと。"""
        curr = int(datetime(2024, 1, 4, 9, 0, 0, tzinfo=_JST).timestamp() * 1e9)
        assert is_new_trading_day(None, curr) is False

    def test_midnight_jst_crossing(self) -> None:
        """23:59:59 JST → 00:00:01 JST は日付跨ぎ。"""
        prev = int(datetime(2024, 1, 4, 23, 59, 59, tzinfo=_JST).timestamp() * 1e9)
        curr = int(datetime(2024, 1, 5, 0, 0, 1, tzinfo=_JST).timestamp() * 1e9)
        assert is_new_trading_day(prev, curr) is True


# ---------------------------------------------------------------------------
# NautilusRunner.start_backtest_replay_streaming — 統合テスト
# ---------------------------------------------------------------------------


class TestStreamingReplayCompletes:
    def test_streaming_replay_completes(self) -> None:
        """start_backtest_replay_streaming が完走すること（multiplier=100 で速度計測）。"""
        from engine.nautilus.engine_runner import NautilusRunner, ReplayBacktestResult

        runner = NautilusRunner()
        result = runner.start_backtest_replay_streaming(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            multiplier=100,
            base_dir=FIXTURES,
        )
        assert isinstance(result, ReplayBacktestResult)
        assert result.strategy_id == "buy-and-hold"
        assert result.trades_loaded == 4

    def test_streaming_replay_emits_events_in_order(self) -> None:
        """streaming replay の on_event は EngineStarted → ReplayDataLoaded → EngineStopped 順。"""
        from engine.nautilus.engine_runner import NautilusRunner

        events: list[dict] = []

        def on_event(evt: dict) -> None:
            events.append(evt)

        runner = NautilusRunner()
        runner.start_backtest_replay_streaming(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            multiplier=100,
            base_dir=FIXTURES,
            on_event=on_event,
        )
        kinds = [e["event"] for e in events]
        assert "EngineStarted" in kinds
        assert "ReplayDataLoaded" in kinds
        assert "EngineStopped" in kinds
        assert kinds.index("EngineStarted") < kinds.index("ReplayDataLoaded")
        assert kinds.index("ReplayDataLoaded") < kinds.index("EngineStopped")

    def test_streaming_replay_stop_event(self) -> None:
        """stop_event が set されたらループが中断して EngineStopped が送出されること。"""
        import threading

        from engine.nautilus.engine_runner import NautilusRunner

        events: list[dict] = []

        def on_event(evt: dict) -> None:
            events.append(evt)

        stop_event = threading.Event()
        # 最初の tick を処理する前に stop を set
        stop_event.set()

        runner = NautilusRunner()
        runner.start_backtest_replay_streaming(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            multiplier=100,
            base_dir=FIXTURES,
            on_event=on_event,
            stop_event=stop_event,
        )
        kinds = [e["event"] for e in events]
        # stop_event が set されていても最終的に EngineStopped まで到達
        assert "EngineStopped" in kinds


class TestStreamingReplayDeterminism:
    def test_streaming_replay_determinism(self) -> None:
        """streaming replay の final_equity が自走経路と一致すること。"""
        from engine.nautilus.engine_runner import NautilusRunner

        kwargs = dict(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
        )

        runner_batch = NautilusRunner()
        result_batch = runner_batch.start_backtest_replay(**kwargs)

        runner_stream = NautilusRunner()
        result_stream = runner_stream.start_backtest_replay_streaming(
            multiplier=100,
            **kwargs,
        )

        assert result_batch.final_equity == result_stream.final_equity
        assert result_batch.trades_loaded == result_stream.trades_loaded

    def test_streaming_replay_two_runs_identical(self) -> None:
        """streaming replay を 2 回実行して final_equity がビット一致すること。"""
        from engine.nautilus.engine_runner import NautilusRunner

        kwargs = dict(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            multiplier=100,
            base_dir=FIXTURES,
        )

        r1 = NautilusRunner().start_backtest_replay_streaming(**kwargs)
        r2 = NautilusRunner().start_backtest_replay_streaming(**kwargs)
        assert r1.final_equity == r2.final_equity


class TestStreamingReplayDateChangeMarker:
    def test_date_change_marker_emitted_on_day_crossing(self) -> None:
        """fixtures が 2024-01-04 と 2024-01-05 の 2 日分あるため
        DateChangeMarker が 1 件以上 emit されること。"""
        from engine.nautilus.engine_runner import NautilusRunner

        events: list[dict] = []

        def on_event(evt: dict) -> None:
            events.append(evt)

        runner = NautilusRunner()
        runner.start_backtest_replay_streaming(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            multiplier=100,
            base_dir=FIXTURES,
            on_event=on_event,
        )
        date_markers = [e for e in events if e.get("event") == "DateChangeMarker"]
        assert len(date_markers) >= 1
        # date フィールドが "YYYY-MM-DD" 形式であること
        for m in date_markers:
            assert "date" in m
            dt.datetime.strptime(m["date"], "%Y-%m-%d")  # パース失敗で AssertionError

    def test_date_change_marker_has_correct_date(self) -> None:
        """DateChangeMarker.date が翌日の日付（2024-01-05）であること。"""
        from engine.nautilus.engine_runner import NautilusRunner

        events: list[dict] = []

        def on_event(evt: dict) -> None:
            events.append(evt)

        runner = NautilusRunner()
        runner.start_backtest_replay_streaming(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            multiplier=100,
            base_dir=FIXTURES,
            on_event=on_event,
        )
        date_markers = [e for e in events if e.get("event") == "DateChangeMarker"]
        assert len(date_markers) >= 1
        # 2024-01-04 → 2024-01-05 の跨ぎで 2024-01-05 が emit される
        dates = {m["date"] for m in date_markers}
        assert "2024-01-05" in dates
