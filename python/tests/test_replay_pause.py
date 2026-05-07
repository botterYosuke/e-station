"""schema 3.15/3.16: Replay pause / step / snapshot のテスト。

受け入れ条件（計画書）:
(1) PauseReplay 送信後に replay state が PAUSED になること
(2) StepReplay 送信後に _replay_step_request が +1 されること
(3) ResumeReplay 送信後に replay state が RUNNING になること

StepBackward 受け入れ条件（schema 3.16）:
(4) PAUSED かつ snapshot 非空のとき StepBackward が snapshot を pop すること
(5) PAUSED かつ snapshot 空のとき EngineBusy を返すこと
(6) RUNNING 状態では StepBackward が EngineBusy を返すこと
"""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.server import DataEngineServer, ReplaySnapshot, ReplayState


# ---------------------------------------------------------------------------
# Helper: create a minimal DataEngineServer without real workers
# ---------------------------------------------------------------------------


def _make_server() -> DataEngineServer:
    """DataEngineServer のインスタンスを最小モックで生成する。"""
    with (
        patch("engine.server.BinanceWorker", return_value=MagicMock()),
        patch("engine.server.BybitWorker", return_value=MagicMock()),
        patch("engine.server.HyperliquidWorker", return_value=MagicMock()),
        patch("engine.server.MexcWorker", return_value=MagicMock()),
        patch("engine.server.OkexWorker", return_value=MagicMock()),
        patch("engine.server.TachibanaWorker", return_value=MagicMock()),
    ):
        srv = DataEngineServer.__new__(DataEngineServer)
        DataEngineServer.__init__(srv, port=9999, token="test")
        return srv


def _make_ws() -> MagicMock:
    """Fake WebSocket connection — _outbox.send_to(ws, ...) で使われる。"""
    ws = MagicMock()
    srv = None  # not needed for send_to logic
    return ws


# ---------------------------------------------------------------------------
# (1) PauseReplay → state = PAUSED
# ---------------------------------------------------------------------------


class TestPauseReplay:
    def test_pause_sets_paused_state(self) -> None:
        srv = _make_server()
        srv._replay_state = ReplayState.RUNNING

        # asyncio なしで dispatch する（同期モック）
        import asyncio

        msg = {"op": "PauseReplay", "request_id": "req-pause-1"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        assert srv._replay_state == ReplayState.PAUSED, (
            "PauseReplay must set replay_state to PAUSED"
        )
        assert srv._replay_paused is True, (
            "PauseReplay must set _replay_paused to True"
        )

    def test_pause_in_idle_returns_engine_busy(self) -> None:
        srv = _make_server()
        srv._replay_state = ReplayState.IDLE

        import asyncio

        msg = {"op": "PauseReplay", "request_id": "req-pause-idle"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        # IDLE では PAUSED にならないこと
        assert srv._replay_state == ReplayState.IDLE, (
            "PauseReplay in IDLE must be rejected (state unchanged)"
        )
        # EngineBusy が outbox に積まれていること
        events = list(srv._outbox)
        assert any(e.get("event") == "EngineBusy" for e in events), (
            "PauseReplay in IDLE must emit EngineBusy"
        )


# ---------------------------------------------------------------------------
# (2) StepReplay → _replay_step_request += 1
# ---------------------------------------------------------------------------


class TestStepReplay:
    def test_step_replay_increments_counter(self) -> None:
        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        assert srv._replay_step_request == 0

        import asyncio

        msg = {"op": "StepReplay", "request_id": "req-step-1"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        assert srv._replay_step_request == 1, (
            "StepReplay must increment _replay_step_request by 1"
        )

    def test_step_replay_in_running_returns_engine_busy(self) -> None:
        srv = _make_server()
        srv._replay_state = ReplayState.RUNNING

        import asyncio

        msg = {"op": "StepReplay", "request_id": "req-step-running"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        # RUNNING 中は PAUSED に変わらず、step_request も増えないこと
        assert srv._replay_step_request == 0, (
            "StepReplay in RUNNING must be rejected (counter unchanged)"
        )
        events = list(srv._outbox)
        assert any(e.get("event") == "EngineBusy" for e in events), (
            "StepReplay in RUNNING must emit EngineBusy"
        )


# ---------------------------------------------------------------------------
# (3) ResumeReplay → state = RUNNING
# ---------------------------------------------------------------------------


class TestResumeReplay:
    def test_resume_sets_running_state(self) -> None:
        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        import asyncio

        msg = {"op": "ResumeReplay", "request_id": "req-resume-1"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        assert srv._replay_state == ReplayState.RUNNING, (
            "ResumeReplay must set replay_state to RUNNING"
        )
        assert srv._replay_paused is False, (
            "ResumeReplay must set _replay_paused to False"
        )


# ---------------------------------------------------------------------------
# (4)(5)(6) StepBackward
# ---------------------------------------------------------------------------


class TestStepBackward:
    def _make_snapshot(self, step_index: int = 0) -> ReplaySnapshot:
        return ReplaySnapshot(
            step_index=step_index,
            portfolio={"cash": "1000000", "ts_event_ms": 1700000000000},
            open_orders=[],
            strategy_state=object(),
            ui_events=[],
        )

    def test_step_backward_pops_snapshot_when_paused(self) -> None:
        """(4) PAUSED かつ snapshot 非空のとき snapshot が pop されること。"""
        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        snap = self._make_snapshot(step_index=2)
        srv._replay_snapshots.append(snap)
        assert len(srv._replay_snapshots) == 1

        import asyncio

        msg = {"op": "StepBackward", "request_id": "req-bwd-1"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        assert len(srv._replay_snapshots) == 0, (
            "StepBackward must pop the snapshot from the ring buffer"
        )
        events = list(srv._outbox)
        event_names = [e.get("event") for e in events]
        assert "RestoreSnapshot" in event_names, (
            "StepBackward must emit RestoreSnapshot event"
        )
        assert "ReplayHistoryChanged" in event_names, (
            "StepBackward must emit ReplayHistoryChanged event"
        )
        history_ev = next(e for e in events if e.get("event") == "ReplayHistoryChanged")
        assert history_ev["has_history"] is False, (
            "ReplayHistoryChanged.has_history must be False when buffer is now empty"
        )

    def test_step_backward_returns_engine_busy_when_no_history(self) -> None:
        """(5) PAUSED かつ snapshot 空のとき EngineBusy を返すこと。"""
        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True
        assert len(srv._replay_snapshots) == 0

        import asyncio

        msg = {"op": "StepBackward", "request_id": "req-bwd-empty"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        events = list(srv._outbox)
        assert any(e.get("event") == "EngineBusy" for e in events), (
            "StepBackward with empty buffer must emit EngineBusy"
        )

    def test_step_backward_returns_engine_busy_in_running(self) -> None:
        """(6) RUNNING 状態では StepBackward が EngineBusy を返すこと。"""
        srv = _make_server()
        srv._replay_state = ReplayState.RUNNING
        srv._replay_snapshots.append(self._make_snapshot())

        import asyncio

        msg = {"op": "StepBackward", "request_id": "req-bwd-running"}
        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        # snapshot が pop されていないこと
        assert len(srv._replay_snapshots) == 1, (
            "StepBackward in RUNNING must not pop the snapshot"
        )
        events = list(srv._outbox)
        assert any(e.get("event") == "EngineBusy" for e in events), (
            "StepBackward in RUNNING must emit EngineBusy"
        )
