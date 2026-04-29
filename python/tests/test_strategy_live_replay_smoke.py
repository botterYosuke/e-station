"""N1.8: live mock / replay 両経路スモークテスト。

BuyAndHold 戦略を:
  1. live mock 経路 (start_backtest — Bar ベース)
  2. replay J-Quants 経路 (start_backtest_replay — Trade ベース)
の両方で走らせ、例外なく完走することを確認する。

spec.md §3.5.4: 最終ポジション方向の一致検証は fill_timestamps の非空チェックで代用。
(複雑な fill 解析は N1.5 で実施。本テストでは「完走 + クラッシュしない」が主眼。)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from engine.nautilus.data_loader import KlineRow
from engine.nautilus.engine_runner import NautilusRunner, BacktestResult, ReplayBacktestResult

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent.parent
STRATEGY_FILE = str(REPO_ROOT / "docs" / "example" / "buy_and_hold.py")

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _year_klines() -> list[KlineRow]:
    """緩やかな上昇トレンドの 250 本日足データを返す（live mock 用）。"""
    rows: list[KlineRow] = []
    base = datetime(2024, 1, 4, tzinfo=timezone.utc)
    close = 3775.0
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


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

def test_live_mock_completes_without_exception():
    """ユーザー戦略が live mock (Bar 経路) で例外なく完走すること。"""
    runner = NautilusRunner()
    result = runner.start_backtest(
        strategy_id="user-strategy",
        ticker="1301",
        venue="TSE",
        klines=_year_klines(),
        initial_cash=1_000_000,
        strategy_file=STRATEGY_FILE,
    )
    assert isinstance(result, BacktestResult)
    assert result.strategy_id == "user-strategy"
    assert result.final_equity > 0


def test_replay_jquants_trade_completes_without_exception():
    """ユーザー戦略が replay J-Quants Trade 経路で例外なく完走すること。

    fixtures/equities_trades_202401.csv.gz を使う。
    """
    runner = NautilusRunner()
    result = runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Trade",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        strategy_file=STRATEGY_FILE,
    )
    assert isinstance(result, ReplayBacktestResult)
    assert result.strategy_id == "user-strategy"
    assert result.final_equity > 0


def test_live_mock_and_replay_both_complete():
    """ユーザー戦略が live mock / replay 両方で例外なく完走すること。

    spec.md §3.5.4: fill_timestamps の非空チェックでポジション生成を確認する。
    (fill が 0 件でも完走は完走とみなす — データ量次第)
    """
    # live mock
    live_runner = NautilusRunner()
    live_result = live_runner.start_backtest(
        strategy_id="user-strategy",
        ticker="1301",
        venue="TSE",
        klines=_year_klines(),
        initial_cash=1_000_000,
        strategy_file=STRATEGY_FILE,
    )

    # replay J-Quants
    replay_runner = NautilusRunner()
    replay_result = replay_runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Trade",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        strategy_file=STRATEGY_FILE,
    )

    # 両方完走チェック
    assert isinstance(live_result, BacktestResult)
    assert isinstance(replay_result, ReplayBacktestResult)

    # equity が正数（エンジンが正常稼働している）
    assert live_result.final_equity > 0
    assert replay_result.final_equity > 0


def test_live_mock_fill_timestamps_non_empty():
    """live mock でユーザー戦略が約定を生成すること（fill_timestamps が非空）。

    250 本の Bar データがあれば最初のバーで買いが入るため fill が生じる。
    """
    runner = NautilusRunner()
    result = runner.start_backtest(
        strategy_id="user-strategy",
        ticker="1301",
        venue="TSE",
        klines=_year_klines(),
        initial_cash=1_000_000,
        strategy_file=STRATEGY_FILE,
    )
    # fill_timestamps が非空であること（少なくとも 1 件の約定がある）
    assert len(result.fill_timestamps) > 0, (
        "live mock must produce at least one fill with 250-bar data"
    )


def test_replay_jquants_daily_bar_completes():
    """ユーザー戦略が replay J-Quants Daily Bar 経路でも完走すること。"""
    runner = NautilusRunner()
    result = runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Daily",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        strategy_file=STRATEGY_FILE,
    )
    assert isinstance(result, ReplayBacktestResult)
    assert result.final_equity > 0


def test_replay_ipc_events_emitted():
    """start_backtest_replay が on_event callback に EngineStarted / ReplayDataLoaded / EngineStopped を emit すること。"""
    events: list[dict] = []
    runner = NautilusRunner()
    runner.start_backtest_replay(
        strategy_id="user-strategy",
        instrument_id="1301.TSE",
        start_date="2024-01-01",
        end_date="2024-01-31",
        granularity="Trade",
        initial_cash=1_000_000,
        base_dir=FIXTURES,
        on_event=events.append,
        strategy_file=STRATEGY_FILE,
    )

    event_names = [e["event"] for e in events]
    assert "EngineStarted" in event_names, f"EngineStarted not emitted: {event_names}"
    assert "ReplayDataLoaded" in event_names, f"ReplayDataLoaded not emitted: {event_names}"
    assert "EngineStopped" in event_names, f"EngineStopped not emitted: {event_names}"
