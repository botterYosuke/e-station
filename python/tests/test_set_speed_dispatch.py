"""H-SF1: set_speed() dispatch in attach / inprocess mode.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations


def test_set_speed_attach_sends_set_replay_speed():
    """H-SF1: attach mode の set_speed() が SetReplaySpeed コマンドを送信すること。"""
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
    s._status = _ReplayStatus.RUNNING

    s.set_speed(3)

    # SetReplaySpeed コマンドが送信されたことを検証
    speed_cmds = [c for c in sent_commands if c.get("op") == "SetReplaySpeed"]
    assert len(speed_cmds) >= 1, f"SetReplaySpeed not sent; got: {sent_commands}"
    assert speed_cmds[0]["multiplier"] == 3


def test_set_speed_inprocess_updates_multiplier():
    """H-SF1: in-process mode では multiplier が更新されること（既存動作の維持）。"""
    from engine.replay_session import ReplaySession, _ReplayStatus

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "inprocess"
    s._status = _ReplayStatus.RUNNING

    s.set_speed(5)
    assert s._multiplier == 5
