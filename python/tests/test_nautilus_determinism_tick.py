"""N1.9 決定論性テスト（tick ベース）.

同一 J-Quants fixtures ファイル（equities_trades_202401.csv.gz）・同一銘柄（1301.TSE）で
start_backtest_replay() を 2 回回し:
- final_equity がビット一致すること
- fill_timestamps がビット一致すること
- fill_last_prices がビット一致すること
- wall clock をモックしても結果が変わらないこと

N0.6 の Bar ベース版（test_nautilus_determinism.py）と並列で維持。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from engine.nautilus.engine_runner import NautilusRunner, ReplayBacktestResult

FIXTURES = Path(__file__).parent / "fixtures"

_COMMON_KWARGS = dict(
    strategy_id="buy-and-hold",
    instrument_id="1301.TSE",
    start_date="2024-01-04",
    end_date="2024-01-05",
    granularity="Trade",
    initial_cash=1_000_000,
    base_dir=FIXTURES,
)


def _run_twice() -> tuple[ReplayBacktestResult, ReplayBacktestResult]:
    """同一条件で 2 回実行し、結果ペアを返す。"""
    r1 = NautilusRunner().start_backtest_replay(**_COMMON_KWARGS)
    r2 = NautilusRunner().start_backtest_replay(**_COMMON_KWARGS)
    return r1, r2


class TestDeterminismTick:
    def test_two_runs_same_final_equity(self) -> None:
        """同一 tick fixtures・同一銘柄の 2 回実行で final_equity がビット一致すること。"""
        r1, r2 = _run_twice()
        assert r1.final_equity == r2.final_equity, (
            f"Determinism FAILED: {r1.final_equity} != {r2.final_equity}"
        )

    def test_two_runs_same_fill_timestamps(self) -> None:
        """同一 tick fixtures・同一銘柄の 2 回実行で fill_timestamps がビット一致すること。

        注: fixtures の tick 数が少ないため fill_timestamps が空になる可能性がある。
        空であっても [] == [] は成立するため決定論性テストとして有効。
        """
        r1, r2 = _run_twice()
        assert r1.fill_timestamps == r2.fill_timestamps, (
            f"Fill timestamps differ: {r1.fill_timestamps} != {r2.fill_timestamps}"
        )

    def test_two_runs_same_last_prices(self) -> None:
        """同一 tick fixtures・同一銘柄の 2 回実行で fill_last_prices がビット一致すること。

        注: fixtures の tick 数が少ないため fill_last_prices が空になる可能性がある。
        空であっても [] == [] は成立するため決定論性テストとして有効。
        """
        r1, r2 = _run_twice()
        assert r1.fill_last_prices == r2.fill_last_prices, (
            f"fill_last_prices differ: {r1.fill_last_prices} != {r2.fill_last_prices}"
        )

    def test_wall_clock_independence(self) -> None:
        """wall clock を固定値にモックしても backtest 結果が変わらないこと。

        time.time / time.monotonic のみモックする。
        datetime.datetime はモックしない（nautilus 内部スケジューラに干渉するため）。
        """
        # 通常実行
        r_normal = NautilusRunner().start_backtest_replay(**_COMMON_KWARGS)

        # wall clock を固定値にモック
        with (
            patch("time.time", return_value=0.0),
            patch("time.monotonic", return_value=0.0),
        ):
            r_mocked = NautilusRunner().start_backtest_replay(**_COMMON_KWARGS)

        assert r_normal.final_equity == r_mocked.final_equity, (
            "wall clock mock changed backtest result"
        )
        assert r_normal.fill_timestamps == r_mocked.fill_timestamps, (
            "wall clock mock changed fill_timestamps"
        )
        assert r_normal.fill_last_prices == r_mocked.fill_last_prices, (
            "wall clock mock changed fill_last_prices"
        )
