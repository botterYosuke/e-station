"""N0.5 headless smoke test: NautilusRunner.start_backtest() が完走すること。

Exit 条件: NautilusRunner.start_backtest(...) を 1 回呼び、
EngineStopped 相当の結果が返ること（N0 は IPC 未実装のため戻り値で確認）。
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from engine.nautilus.data_loader import KlineRow
from engine.nautilus.engine_runner import NautilusRunner, BacktestResult


def _year_klines() -> list[KlineRow]:
    rows: list[KlineRow] = []
    base = datetime(2024, 1, 4, tzinfo=timezone.utc)
    close = 2000.0
    for i in range(250):
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


class TestNautilusRunnerSmoke:
    def test_start_backtest_returns_result(self):
        runner = NautilusRunner()
        result = runner.start_backtest(
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=_year_klines(),
            initial_cash=1_000_000,
        )
        assert isinstance(result, BacktestResult)

    def test_start_backtest_final_equity_is_positive(self):
        runner = NautilusRunner()
        result = runner.start_backtest(
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=_year_klines(),
            initial_cash=1_000_000,
        )
        assert result.final_equity > 0

    def test_start_backtest_strategy_id_in_result(self):
        runner = NautilusRunner()
        result = runner.start_backtest(
            strategy_id="buy-and-hold",
            ticker="7203",
            venue="TSE",
            klines=_year_klines(),
            initial_cash=1_000_000,
        )
        assert result.strategy_id == "buy-and-hold"

    def test_start_live_is_stub(self):
        """N0 では start_live() は stub（NotImplementedError を出さない）。"""
        runner = NautilusRunner()
        runner.start_live()  # must not raise

    def test_stop_is_safe(self):
        runner = NautilusRunner()
        runner.stop()  # must not raise even if never started
