"""N0.6 決定論性テスト.

同一 seed・同一データセットで start_backtest を 2 回回し:
- 最終 equity がビット一致すること
- 全約定タイムスタンプがビット一致すること
- wall clock をモックしても結果が変わらないこと
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch

from engine.nautilus.data_loader import KlineRow
from engine.nautilus.engine_runner import NautilusRunner


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
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
        )
        runner2 = NautilusRunner()
        r2 = runner2.start_backtest(
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
        )
        assert r1.final_equity == r2.final_equity, (
            f"Determinism FAILED: {r1.final_equity} != {r2.final_equity}"
        )

    def test_two_runs_same_fill_timestamps(self):
        klines = _fixed_klines()
        runner1 = NautilusRunner()
        r1 = runner1.start_backtest(
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
        )
        runner2 = NautilusRunner()
        r2 = runner2.start_backtest(
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
        )
        assert r1.fill_timestamps == r2.fill_timestamps, (
            f"Fill timestamps differ: {r1.fill_timestamps} != {r2.fill_timestamps}"
        )

    def test_wall_clock_independence(self):
        """wall clock を固定値にモックしても backtest 結果が変わらないこと。"""
        klines = _fixed_klines()

        # 通常実行
        r_normal = NautilusRunner().start_backtest(
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=klines,
            initial_cash=1_000_000,
        )

        # wall clock を 1970-01-01 に固定
        with patch("time.time", return_value=0.0), \
             patch("time.monotonic", return_value=0.0):
            r_mocked = NautilusRunner().start_backtest(
                strategy_id="buy-and-hold",
                ticker="7203",
                venue="TSE",
                klines=klines,
                initial_cash=1_000_000,
            )

        assert r_normal.final_equity == r_mocked.final_equity, (
            "wall clock mock changed backtest result"
        )
