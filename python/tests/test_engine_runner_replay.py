"""NautilusRunner.start_backtest_replay() tests (N1.4).

J-Quants fixtures から trade tick / minute bar / daily bar をロードして
BacktestEngine が回ること、`on_event` callback が EngineStarted /
ReplayDataLoaded / EngineStopped を順序通り emit することを検証する。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from engine.nautilus.engine_runner import (
    NautilusRunner,
    ReplayBacktestResult,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _collect_events() -> tuple[list[dict], callable]:
    """Return ``(events, on_event)`` — on_event appends each event dict to events."""
    events: list[dict] = []

    def on_event(evt: dict) -> None:
        events.append(evt)

    return events, on_event


class TestStartBacktestReplayTrades:
    """granularity='Trade' の TradeTick 経路を検証する。"""

    def test_trade_replay_returns_result(self) -> None:
        """fixtures から trade tick をロードし、BacktestEngine が回って
        ReplayBacktestResult を返すこと。"""
        runner = NautilusRunner()
        result = runner.start_backtest_replay(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
        )
        assert isinstance(result, ReplayBacktestResult)
        assert result.strategy_id == "buy-and-hold"

    def test_trade_replay_loads_correct_count(self) -> None:
        """fixtures の 1301 trade 件数 (4 件: 2024-01-04 x3 + 2024-01-05 x1)
        と ``trades_loaded`` が一致すること。"""
        runner = NautilusRunner()
        result = runner.start_backtest_replay(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
        )
        assert result.trades_loaded == 4
        assert result.bars_loaded == 0

    def test_trade_replay_emits_events_in_order(self) -> None:
        """on_event hook は EngineStarted → ReplayDataLoaded → EngineStopped
        の順で 1 件ずつ呼ばれる。"""
        events, on_event = _collect_events()
        runner = NautilusRunner()
        runner.start_backtest_replay(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
            on_event=on_event,
        )
        kinds = [e["event"] for e in events]
        assert kinds == ["EngineStarted", "ReplayDataLoaded", "EngineStopped"]
        # 全イベントに strategy_id 一致 / ts_event_ms が int
        for evt in events:
            assert evt["strategy_id"] == "buy-and-hold"
            assert isinstance(evt["ts_event_ms"], int)
            assert evt["ts_event_ms"] >= 0

    def test_replay_data_loaded_trades_count_matches_fixture(self) -> None:
        """ReplayDataLoaded.trades_loaded = 4 (fixture と一致)。"""
        events, on_event = _collect_events()
        runner = NautilusRunner()
        runner.start_backtest_replay(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
            on_event=on_event,
        )
        loaded = next(e for e in events if e["event"] == "ReplayDataLoaded")
        assert loaded["trades_loaded"] == 4
        assert loaded["bars_loaded"] == 0


class TestStartBacktestReplayBars:
    """granularity='Minute' / 'Daily' の Bar 経路を検証する。"""

    def test_minute_replay_loads_bars(self) -> None:
        """fixtures の 1301 minute bar 件数 (3 件) が bars_loaded と一致。"""
        runner = NautilusRunner()
        result = runner.start_backtest_replay(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Minute",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
        )
        assert result.bars_loaded == 3
        assert result.trades_loaded == 0

    def test_daily_replay_loads_bars(self) -> None:
        """fixtures の 1301 daily bar 件数 (2 件) が bars_loaded と一致。"""
        runner = NautilusRunner()
        result = runner.start_backtest_replay(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Daily",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
        )
        assert result.bars_loaded == 2
        assert result.trades_loaded == 0


class TestStartBacktestReplayEdgeCases:
    def test_invalid_instrument_id_raises(self) -> None:
        """フォーマット不正 instrument_id は ValueError or RuntimeError を出すこと。

        `InstrumentId.from_str("INVALID")` は ValueError を raise するので、
        replay 経路全体が起動前に失敗する。
        """
        runner = NautilusRunner()
        with pytest.raises((ValueError, RuntimeError)):
            runner.start_backtest_replay(
                strategy_id="buy-and-hold",
                instrument_id="INVALID-ID",  # 末尾に "." なし
                start_date="2024-01-04",
                end_date="2024-01-05",
                granularity="Trade",
                initial_cash=1_000_000,
                base_dir=FIXTURES,
            )

    def test_empty_range_still_emits_engine_stopped(self, tmp_path: Path) -> None:
        """trade 0 件でも EngineStopped まで到達し、final_equity == initial_cash。

        空ディレクトリは FileNotFoundError を出すので、有効なフィクスチャを使い
        対象銘柄を fixtures に存在しないコードに変えて 0 件にする。
        """
        events, on_event = _collect_events()
        runner = NautilusRunner()
        # 1306 (= 13060) は fixtures に存在しない → 0 件ロード
        # ただし fixtures dir には trades file が存在するので FileNotFoundError は起きない
        result = runner.start_backtest_replay(
            strategy_id="buy-and-hold",
            instrument_id="1306.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
            on_event=on_event,
        )
        assert result.trades_loaded == 0
        assert result.final_equity == Decimal(1_000_000)
        kinds = [e["event"] for e in events]
        assert "EngineStopped" in kinds


class TestDeterminism:
    def test_two_runs_produce_identical_final_equity(self) -> None:
        """同一フィクスチャ・同一銘柄の 2 回実行で final_equity ビット一致。"""
        runner1 = NautilusRunner()
        runner2 = NautilusRunner()
        kwargs = dict(
            strategy_id="buy-and-hold",
            instrument_id="1301.TSE",
            start_date="2024-01-04",
            end_date="2024-01-05",
            granularity="Trade",
            initial_cash=1_000_000,
            base_dir=FIXTURES,
        )
        r1 = runner1.start_backtest_replay(**kwargs)
        r2 = runner2.start_backtest_replay(**kwargs)
        assert r1.final_equity == r2.final_equity
        assert r1.trades_loaded == r2.trades_loaded


class TestModeValidation:
    def test_validate_start_engine_rejects_live_backtest(self) -> None:
        """mode='live' で StartEngine.engine='Backtest' は engine.mode で拒否される。

        本テストは engine_runner ではなく mode.py を直接呼ぶ pure unit test。
        サーバ統合は test_server_engine_dispatch で確認。
        """
        from engine.mode import validate_start_engine

        with pytest.raises(ValueError, match="requires mode='replay'"):
            validate_start_engine("live", "Backtest")
