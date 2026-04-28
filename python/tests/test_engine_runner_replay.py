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


class TestHHIpcVenueTag:
    """H-H: 外向け IPC EngineStarted.account_id は ``replay-`` prefix を使う。"""

    def test_engine_started_account_id_uses_ipc_venue_tag(self) -> None:
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
        started = next(e for e in events if e["event"] == "EngineStarted")
        # H-H: 内部 venue (TSE) ではなく外向け IPC venue tag (replay) を使う
        assert started["account_id"].startswith("replay-"), (
            f"account_id must use _IPC_VENUE_TAG, got {started['account_id']!r}"
        )


class TestHCEngineStartedFailureRecovery:
    """H-C: ``add_venue`` 等が raise しても EngineStopped が補完送出される。"""

    def test_emit_engine_stopped_when_load_trades_raises(self) -> None:
        from unittest.mock import patch

        events, on_event = _collect_events()
        runner = NautilusRunner()

        # try ブロック内で raise させるため load_trades を mock。EngineStarted 送出後・
        # ReplayDataLoaded 送出前のフェーズで例外が発生する状況を再現する。
        with patch(
            "engine.nautilus.engine_runner.load_trades",
            side_effect=RuntimeError("synthetic load_trades failure"),
        ):
            with pytest.raises(RuntimeError, match="synthetic load_trades failure"):
                runner.start_backtest_replay(
                    strategy_id="hc-strategy",
                    instrument_id="1301.TSE",
                    start_date="2024-01-04",
                    end_date="2024-01-05",
                    granularity="Trade",
                    initial_cash=1_000_000,
                    base_dir=FIXTURES,
                    on_event=on_event,
                )

        kinds = [e["event"] for e in events]
        # H-C: EngineStarted の後に EngineStopped 補完が emit される
        assert "EngineStarted" in kinds
        assert "EngineStopped" in kinds
        assert kinds.index("EngineStarted") < kinds.index("EngineStopped")
        # ReplayDataLoaded は出ない (load フェーズで失敗)
        assert "ReplayDataLoaded" not in kinds
        stopped = next(e for e in events if e["event"] == "EngineStopped")
        assert stopped["final_equity"] == "0"
        assert stopped["strategy_id"] == "hc-strategy"


class TestH1NoDoubleEngineStoppedEmit:
    """H-1 (R2 review-fix R2): non-streaming 版でも EngineStopped が二重 emit されない。

    streaming 版には既に `stop_ts_event_ms == 0` ガードがあるが non-streaming にも同じ
    ガードを追加した。本テストは正常系で EngineStopped が 1 回だけ emit されることを
    pin する（normal-path emit と except 補完 emit の重複を検出）。
    """

    def test_engine_stopped_emitted_exactly_once_on_success(self) -> None:
        """正常系: EngineStopped は 1 回だけ emit される。"""
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
        stopped = [e for e in events if e["event"] == "EngineStopped"]
        assert len(stopped) == 1, (
            f"EngineStopped should be emitted exactly once, got {len(stopped)}: "
            f"{[e for e in events]}"
        )

    def test_engine_stopped_not_double_emitted_when_post_run_raises(self) -> None:
        """正常系で engine.run() 完走後に後段が raise しても、normal-path で
        既に emit 済みの EngineStopped に対する補完 emit は走らない (二重 emit 防止)。

        ``_collect_fill_data`` を mock して raise させ、normal-path EngineStopped 送出後
        の例外で except 補完が走らないことを assert。
        """
        from unittest.mock import patch

        events, on_event = _collect_events()
        runner = NautilusRunner()

        # _collect_fill_data は EngineStopped emit より前に呼ばれるので、
        # post-run exception 経路を作るには engine.run() 完走後・EngineStopped emit 後に
        # raise させる必要がある。ReplayBacktestResult 構築側を壊して再現する。
        original_init = None
        from engine.nautilus import engine_runner as er

        original_init = er.ReplayBacktestResult.__init__

        call_count = {"n": 0}

        def boom(*args, **kwargs):
            call_count["n"] += 1
            # 1 回目だけ raise (このテスト内で他の dataclass 構築には影響させない)
            raise RuntimeError("synthetic post-emit failure")

        with patch.object(er.ReplayBacktestResult, "__init__", boom):
            with pytest.raises(RuntimeError, match="synthetic post-emit failure"):
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

        # H-1: stop_ts_ms != 0 なので except 補完は走らないはず
        stopped = [e for e in events if e["event"] == "EngineStopped"]
        assert len(stopped) == 1, (
            f"EngineStopped should not be double-emitted; got {len(stopped)}"
        )
        # 元の except 補完 emit (final_equity='0') ではなく normal-path の値が残ることも確認
        assert stopped[0]["final_equity"] != "0", (
            "normal-path EngineStopped should be preserved, not overwritten by fallback"
        )

        # 復元
        er.ReplayBacktestResult.__init__ = original_init


class TestHICollectFillDataDeterminism:
    """H-I: 同 ts 内も (ts, price) lex sort で安定する。"""

    def test_collect_fill_data_lex_sort_when_ts_collide(self) -> None:
        from engine.nautilus.engine_runner import _collect_fill_data

        # 同 ts のフィル 3 件 + 別 ts 1 件
        class _O:
            def __init__(self, ts, px, closed=True):
                self.ts_last = ts
                self.avg_px = px
                self.is_closed = closed

        class _Cache:
            def __init__(self, orders):
                self._orders = orders

            def orders(self):
                return self._orders

        class _Kernel:
            def __init__(self, orders):
                self.cache = _Cache(orders)

        class _FakeEngine:
            def __init__(self, orders):
                self.kernel = _Kernel(orders)

        # 入力順序を変えて 2 回呼んでもビット一致するはず
        orders_a = [_O(100, "3"), _O(100, "1"), _O(100, "2"), _O(50, "9")]
        orders_b = [_O(50, "9"), _O(100, "2"), _O(100, "1"), _O(100, "3")]
        ts_a, px_a = _collect_fill_data(_FakeEngine(orders_a), "x")
        ts_b, px_b = _collect_fill_data(_FakeEngine(orders_b), "x")
        assert (ts_a, px_a) == (ts_b, px_b)
        # 期待: ts ascending, 同 ts 内は price ascending
        assert ts_a == [50, 100, 100, 100]
        assert px_a == ["9", "1", "2", "3"]


class TestModeValidation:
    def test_validate_start_engine_rejects_live_backtest(self) -> None:
        """mode='live' で StartEngine.engine='Backtest' は engine.mode で拒否される。

        本テストは engine_runner ではなく mode.py を直接呼ぶ pure unit test。
        サーバ統合は test_server_engine_dispatch で確認。
        """
        from engine.mode import validate_start_engine

        with pytest.raises(ValueError, match="requires mode='replay'"):
            validate_start_engine("live", "Backtest")
