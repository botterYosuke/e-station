"""test_replay_session_inprocess.py

NautilusRunner を mock して J-Quants データなしで ReplaySession をテストする。
"""
from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from engine.replay_session import LiveSession, ReplaySession


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_fake_result():
    from engine.nautilus.engine_runner import ReplayBacktestResult

    return ReplayBacktestResult(
        strategy_id="test",
        final_equity=Decimal("1000000"),
    )


def make_mock_runner(events):
    """events を on_event に送出する mock NautilusRunner を返す。"""

    def fake_backtest(
        *args,
        stop_event=None,
        on_event=None,
        get_multiplier=None,
        **kwargs,
    ):
        for evt in events:
            if stop_event and stop_event.is_set():
                break
            if on_event:
                on_event(evt)
        return _make_fake_result()

    mock = MagicMock()
    mock.start_backtest_replay_streaming.side_effect = fake_backtest
    return mock


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

DUMMY_STRATEGY = "dummy_strategy.py"


@pytest.fixture()
def tmp_strategy(tmp_path):
    """実在する（空の）戦略ファイルを返す。"""
    f = tmp_path / "strategy.py"
    f.write_text("# dummy strategy\n")
    return str(f)


# ---------------------------------------------------------------------------
# test_load_calls_check_data_exists
# ---------------------------------------------------------------------------

def test_load_calls_check_data_exists():
    """load() が check_data_exists を呼ぶこと。"""
    with patch("engine.replay_session.ReplaySession._resolve_base_dir", return_value=None), \
         patch("engine.nautilus.jquants_loader.check_data_exists") as mock_check:
        with ReplaySession() as s:
            s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")

    mock_check.assert_called_once_with(
        "1301.TSE", "2025-01-06", "2025-03-31", "Daily"
    )


# ---------------------------------------------------------------------------
# test_load_raises_file_not_found
# ---------------------------------------------------------------------------

def test_load_raises_file_not_found():
    """check_data_exists が FileNotFoundError を raise したら load() も raise する。"""
    with patch(
        "engine.nautilus.jquants_loader.check_data_exists",
        side_effect=FileNotFoundError("no files"),
    ):
        with ReplaySession() as s:
            with pytest.raises(FileNotFoundError):
                s.load("NONEXISTENT.TSE", "2025-01-01", "2025-01-31", "Daily")


# ---------------------------------------------------------------------------
# test_run_calls_runner
# ---------------------------------------------------------------------------

def test_run_calls_runner(tmp_strategy):
    """run() が NautilusRunner.start_backtest_replay_streaming を呼ぶこと。"""
    received = []
    mock_runner = make_mock_runner([{"type": "EngineStarted"}])

    with patch("engine.nautilus.jquants_loader.check_data_exists"), \
         patch("engine.replay_session.NautilusRunner", return_value=mock_runner):
        with ReplaySession() as s:
            s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
            s.run(
                strategy_file=tmp_strategy,
                on_event=received.append,
            )

    mock_runner.start_backtest_replay_streaming.assert_called_once()
    assert received == [{"type": "EngineStarted"}]


# ---------------------------------------------------------------------------
# test_strategy_file_not_found
# ---------------------------------------------------------------------------

def test_strategy_file_not_found():
    """存在しない strategy_file で FileNotFoundError が raise される。"""
    with patch("engine.nautilus.jquants_loader.check_data_exists"):
        with ReplaySession() as s:
            s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
            with pytest.raises(FileNotFoundError):
                s.run(
                    strategy_file="/nonexistent/path/strategy.py",
                    on_event=lambda _: None,
                )


# ---------------------------------------------------------------------------
# test_stop_from_thread
# ---------------------------------------------------------------------------

def test_stop_from_thread(tmp_strategy):
    """別 thread から stop() で run() が終了する。"""
    import time

    barrier = threading.Barrier(2)

    def slow_backtest(*args, stop_event=None, on_event=None, get_multiplier=None, **kwargs):
        barrier.wait()  # メインスレッドに「走行中」を通知
        # stop_event がセットされるまで待機
        stop_event.wait(timeout=5.0)
        return _make_fake_result()

    mock_runner = MagicMock()
    mock_runner.start_backtest_replay_streaming.side_effect = slow_backtest

    result_holder = []

    def run_in_thread(s):
        s.run(strategy_file=tmp_strategy, on_event=lambda _: None)
        result_holder.append(s.status)

    with patch("engine.nautilus.jquants_loader.check_data_exists"), \
         patch("engine.replay_session.NautilusRunner", return_value=mock_runner):
        with ReplaySession() as s:
            s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
            t = threading.Thread(target=run_in_thread, args=(s,))
            t.start()
            barrier.wait()  # run() が走り始めるまで待つ
            s.stop()
            t.join(timeout=5.0)

    assert not t.is_alive(), "run() が stop() で終了していない"
    assert result_holder == ["stopped"]


# ---------------------------------------------------------------------------
# test_status_transitions
# ---------------------------------------------------------------------------

def test_status_transitions(tmp_strategy):
    """idle → loaded → running → stopped の遷移を確認する。"""
    statuses = []
    mock_runner = make_mock_runner([])

    def recording_on_event(evt):
        pass

    with patch("engine.nautilus.jquants_loader.check_data_exists"), \
         patch("engine.replay_session.NautilusRunner", return_value=mock_runner):
        with ReplaySession() as s:
            assert s.status == "idle"
            s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
            assert s.status == "loaded"
            s.run(strategy_file=tmp_strategy, on_event=recording_on_event)
            assert s.status == "stopped"


# ---------------------------------------------------------------------------
# test_double_enter_raises
# ---------------------------------------------------------------------------

def test_double_enter_raises():
    """同一インスタンスを二度 with に入れると RuntimeError。"""
    s = ReplaySession()
    with s:
        pass
    with pytest.raises(RuntimeError):
        with s:
            pass


# ---------------------------------------------------------------------------
# test_portfolio_updated
# ---------------------------------------------------------------------------

def test_portfolio_updated(tmp_strategy):
    """ReplayBuyingPower event で portfolio が更新される。"""
    buying_power_event = {
        "type": "ReplayBuyingPower",
        "cash": 950000,
        "equity": 1050000,
    }
    mock_runner = make_mock_runner([buying_power_event])

    with patch("engine.nautilus.jquants_loader.check_data_exists"), \
         patch("engine.replay_session.NautilusRunner", return_value=mock_runner):
        with ReplaySession() as s:
            s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
            s.run(strategy_file=tmp_strategy, on_event=lambda _: None)
            assert s.portfolio == buying_power_event
