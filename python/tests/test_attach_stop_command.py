"""C-2 / M-RS3-PY / R2-H1: attach mode stop() command dispatch.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations


def test_stop_attach_sends_stop_engine_command(tmp_path):
    """C-2: attach mode の stop() が StopEngine コマンドを送信すること。"""
    from engine.replay_session import ReplaySession, _ReplayStatus

    received_commands: list[dict] = []

    class _FakeClient:
        def __init__(self):
            pass

        def send_command(self, cmd):
            received_commands.append(cmd)

        def wait_for(self, *a, **kw):
            return {"event": "ReplayDataLoaded"}

        def events(self):
            yield {"event": "EngineStopped", "strategy_id": "s1", "final_equity": "0", "ts_event_ms": 0}

        def close(self):
            pass

    strat = tmp_path / "s.py"
    strat.write_text("# dummy\n")

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "attach"
    s._client = _FakeClient()
    s._status = _ReplayStatus.IDLE
    s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")

    # RUNNING 状態にする
    s._status = _ReplayStatus.RUNNING
    s._strategy_id = "user-strategy"

    # stop() を呼ぶ
    s.stop()

    # StopEngine コマンドが送信されたことを検証
    stop_engine_cmds = [c for c in received_commands if c.get("op") == "StopEngine"]
    assert len(stop_engine_cmds) >= 1, f"StopEngine not sent; got: {received_commands}"


def test_stop_transitions_to_stopped_after_engine_stopped(tmp_path):
    """M-RS3-PY / C-2: stop() 後に EngineStopped を受信すると status == 'stopped' になること。"""
    from engine.replay_session import ReplaySession, _ReplayStatus

    strat = tmp_path / "s.py"
    strat.write_text("# dummy\n")

    class _FakeClientWithStop:
        def __init__(self):
            self.sent = []

        def send_command(self, cmd):
            self.sent.append(cmd)

        def wait_for(self, *a, **kw):
            return {"event": "ReplayDataLoaded"}

        def events(self):
            # EngineStopped を返して終了させる
            yield {"event": "EngineStopped", "strategy_id": "s1", "final_equity": "100", "ts_event_ms": 0}

        def close(self):
            pass

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "attach"
    s._client = _FakeClientWithStop()
    s._status = _ReplayStatus.IDLE
    s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")

    received = []
    s.run(strategy_file=str(strat), on_event=received.append)

    # EngineStopped を受信した後は STOPPED になっているはず
    assert s.status == "stopped", f"expected 'stopped', got {s.status!r}"


# ---------------------------------------------------------------------------
# R2-H1: stop() が RUNNING 以外の状態で StopEngine を送信しないこと
# ---------------------------------------------------------------------------


def test_stop_not_running_does_not_send_stop_engine():
    """R2-H1: ERRORED 状態で stop() を呼んでも StopEngine が送信されないこと。"""
    from engine.replay_session import ReplaySession, _ReplayStatus

    sent_commands: list[dict] = []

    class _FakeClient:
        def send_command(self, cmd):
            sent_commands.append(cmd)

        def wait_for(self, *a, **kw):
            return {}

        def events(self):
            return iter([])

        def close(self):
            pass

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "attach"
    s._client = _FakeClient()
    # ERRORED 状態に設定
    s._status = _ReplayStatus.ERRORED
    s._strategy_id = "user-strategy"

    s.stop()

    # StopEngine コマンドが送信されていないことを検証
    stop_engine_cmds = [c for c in sent_commands if c.get("op") == "StopEngine"]
    assert len(stop_engine_cmds) == 0, \
        f"StopEngine should NOT be sent in ERRORED state; got: {sent_commands}"


def test_stop_loaded_does_not_send_stop_engine():
    """R2-H1: LOADED 状態で stop() を呼んでも StopEngine が送信されないこと。"""
    from engine.replay_session import ReplaySession, _ReplayStatus

    sent_commands: list[dict] = []

    class _FakeClient:
        def send_command(self, cmd):
            sent_commands.append(cmd)

        def wait_for(self, *a, **kw):
            return {}

        def events(self):
            return iter([])

        def close(self):
            pass

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "attach"
    s._client = _FakeClient()
    # LOADED 状態に設定
    s._status = _ReplayStatus.LOADED
    s._strategy_id = "user-strategy"

    s.stop()

    stop_engine_cmds = [c for c in sent_commands if c.get("op") == "StopEngine"]
    assert len(stop_engine_cmds) == 0, \
        f"StopEngine should NOT be sent in LOADED state; got: {sent_commands}"
