"""strategy_file 必須バリデーションのテスト。

- _make_replay_strategy(strategy_file=None) → ValueError
- _make_replay_strategy(strategy_file="") → ValueError
- server.py StartEngine ハンドラで strategy_file なし → EngineError{strategy_file_required}
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# _make_replay_strategy
# ---------------------------------------------------------------------------


def test_make_replay_strategy_raises_when_none() -> None:
    from engine.nautilus.engine_runner import _make_replay_strategy

    with pytest.raises(ValueError, match="strategy_file is required"):
        _make_replay_strategy(strategy_file=None)


def test_make_replay_strategy_raises_when_empty_string() -> None:
    from engine.nautilus.engine_runner import _make_replay_strategy

    with pytest.raises(ValueError, match="strategy_file is required"):
        _make_replay_strategy(strategy_file="")


def test_make_replay_strategy_raises_without_args() -> None:
    from engine.nautilus.engine_runner import _make_replay_strategy

    with pytest.raises(ValueError, match="strategy_file is required"):
        _make_replay_strategy()


# ---------------------------------------------------------------------------
# server.py StartEngine early validation
# ---------------------------------------------------------------------------


def _make_server():
    """テスト用の最小 EngineServer を生成して返す。"""
    from engine.server import DataEngineServer

    server = DataEngineServer.__new__(DataEngineServer)
    server._mode = "replay"
    server._outbox = MagicMock()
    server._outbox.append = MagicMock()
    server._engine_tasks = {}
    server._replay_strategy_id = None
    return server


@pytest.mark.asyncio
async def test_start_engine_rejects_missing_strategy_file() -> None:
    """StartEngine で strategy_file なし → strategy_file_required エラーが返ること。"""
    server = _make_server()

    msg = {
        "op": "StartEngine",
        "engine": "Backtest",
        "strategy_id": "test-strat",
        "request_id": "req-001",
        "config": {
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_cash": "1000000",
            "granularity": "Daily",
        },
    }

    await server._handle_start_engine(msg)

    emitted = [call.args[0] for call in server._outbox.append.call_args_list]
    error_events = [e for e in emitted if e.get("event") == "Error"]
    assert any(
        e.get("code") == "strategy_file_required" for e in error_events
    ), f"expected strategy_file_required error; got: {emitted}"


@pytest.mark.asyncio
async def test_start_engine_rejects_empty_strategy_file() -> None:
    """StartEngine で strategy_file="" → strategy_file_required エラーが返ること。"""
    server = _make_server()

    msg = {
        "op": "StartEngine",
        "engine": "Backtest",
        "strategy_id": "test-strat",
        "request_id": "req-002",
        "config": {
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_cash": "1000000",
            "granularity": "Daily",
            "strategy_file": "",
        },
    }

    await server._handle_start_engine(msg)

    emitted = [call.args[0] for call in server._outbox.append.call_args_list]
    error_events = [e for e in emitted if e.get("event") == "Error"]
    assert any(
        e.get("code") == "strategy_file_required" for e in error_events
    ), f"expected strategy_file_required error; got: {emitted}"
