"""N0.6 決定論性テスト.

同一 seed・同一データセットで start_backtest を 2 回回し:
- 最終 equity がビット一致すること
- 全約定タイムスタンプがビット一致すること
- wall clock をモックしても結果が変わらないこと
- 全約定 last_price がビット一致すること（H-4）
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch

from pathlib import Path

from engine.nautilus.data_loader import KlineRow
from engine.nautilus.engine_runner import NautilusRunner

_STRATEGY_FILE = str(Path(__file__).parent.parent.parent / "docs" / "example" / "buy_and_hold.py")
_STRATEGY_INIT_KWARGS = {"instrument_id": "7203.TSE", "bar_type_str": "7203.TSE-1-DAY-MID-EXTERNAL"}


def _fixed_klines(n: int = 50) -> list[KlineRow]:
    rows: list[KlineRow] = []
    base = datetime(2024, 1, 4, tzinfo=timezone.utc)
    close = 2000.0
    for i in range(n):
        dt = base + timedelta(days=i)
        close = max(1000.0, close + (10 if i % 2 == 0 else -5))
        rows.append(
            KlineRow(
                date=dt.strftime("%Y%m%d"),
                open=str(close - 10),
                high=str(close + 20),
                low=str(close - 20),
                close=str(close),
                volume="1000",
            )
        )
    return rows


class TestDeterminism:
    def test_two_runs_same_final_equity(self):
        klines = _fixed_klines()
        runner1 = NautilusRunner()
        r1 = runner1.start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )
        runner2 = NautilusRunner()
        r2 = runner2.start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )
        assert r1.final_equity == r2.final_equity, (
            f"Determinism FAILED: {r1.final_equity} != {r2.final_equity}"
        )

    def test_two_runs_same_fill_timestamps(self):
        klines = _fixed_klines()
        runner1 = NautilusRunner()
        r1 = runner1.start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )
        runner2 = NautilusRunner()
        r2 = runner2.start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )
        # 偽陽性防止: タイムスタンプが空でないこと
        assert len(r1.fill_timestamps) > 0, "fill_timestamps must not be empty"
        assert r1.fill_timestamps == r2.fill_timestamps, (
            f"Fill timestamps differ: {r1.fill_timestamps} != {r2.fill_timestamps}"
        )

    def test_wall_clock_independence(self):
        """wall clock を固定値にモックしても backtest 結果が変わらないこと。"""
        klines = _fixed_klines()

        # 通常実行
        r_normal = NautilusRunner().start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )

        # wall clock を固定値にモック（time.time / time.monotonic のみ。
        # datetime.datetime をパッチすると nautilus 内部スケジューラに干渉するため除外）
        with patch("time.time", return_value=0.0), \
             patch("time.monotonic", return_value=0.0):
            r_mocked = NautilusRunner().start_backtest(
                strategy_id="user-strategy",
                ticker="7203",
                venue="TSE",
                klines=klines,
                initial_cash=1_000_000,
                strategy_file=_STRATEGY_FILE,
                strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
            )

        assert r_normal.final_equity == r_mocked.final_equity, (
            "wall clock mock changed backtest result"
        )

    def test_two_runs_same_last_prices(self):
        """H-4: 2 回実行で fill_last_prices がビット一致すること。"""
        klines = _fixed_klines()
        r1 = NautilusRunner().start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )
        r2 = NautilusRunner().start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )
        # 偽陽性防止: fill_last_prices が空でないこと
        assert len(r1.fill_last_prices) > 0, "fill_last_prices must not be empty"
        assert r1.fill_last_prices == r2.fill_last_prices, (
            f"fill_last_prices differ: {r1.fill_last_prices} != {r2.fill_last_prices}"
        )

    def test_fill_timestamps_non_empty_on_buy_and_hold(self):
        """H-4: buy-and-hold で fill_timestamps が空でないこと（偽陽性防止）。"""
        klines = _fixed_klines()
        result = NautilusRunner().start_backtest(
            strategy_id="user-strategy",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
            strategy_file=_STRATEGY_FILE,
            strategy_init_kwargs=_STRATEGY_INIT_KWARGS,
        )
        assert len(result.fill_timestamps) > 0, "fill_timestamps must not be empty"
