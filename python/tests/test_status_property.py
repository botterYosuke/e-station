"""H-GP1 / H-Type3: `ReplaySession.status` property mapping.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations


def test_status_stopping_returns_stopping():
    """C-GP1 / H-Type3 (Phase 8 R1 / Phase 3): STOPPING 状態の status は
    "stopping" を返す (旧仕様の "running" マップは撤廃)。Literal も 6 値に
    拡張済み。詳細は test_replay_session_phase3_review_fixes.py を参照。"""
    from engine.replay_session import ReplaySession, _ReplayStatus

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "inprocess"
    s._status = _ReplayStatus.STOPPING

    result = s.status
    assert result == "stopping", f"expected 'stopping' for STOPPING, got {result!r}"


def test_status_literals_are_correct():
    """H-GP1: STOPPING 以外の状態は正しい文字列を返すこと。"""
    from engine.replay_session import ReplaySession, _ReplayStatus

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "inprocess"

    for state, expected in [
        (_ReplayStatus.IDLE, "idle"),
        (_ReplayStatus.LOADED, "loaded"),
        (_ReplayStatus.RUNNING, "running"),
        (_ReplayStatus.STOPPED, "stopped"),
        (_ReplayStatus.ERRORED, "errored"),
    ]:
        s._status = state
        assert s.status == expected, f"state={state}, expected={expected!r}, got={s.status!r}"
