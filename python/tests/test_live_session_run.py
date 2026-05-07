"""Phase 8 (test_live_session_run.py): LiveSession.run() / stop() の単体テスト。

テスト対象:
- LiveSession.run() inprocess: monkeypatch した start_live() が EngineStarted / EngineStopped を emit する
- D7: login() 前に run() → RuntimeError テスト
- D8: second_password=None で run() → RuntimeError テスト
- D9: stop() が stop_event.set() を呼ぶテスト

設計方針:
- start_live() を monkeypatch してリアルな nautilus を起動しない
- LiveSession.__init__ に second_password 引数が追加されていることを前提とする
"""

from __future__ import annotations

import threading

import pytest

from engine.replay_session import LiveSession


# ---------------------------------------------------------------------------
# 全テスト共通: engine への自動 attach を防ぐ
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_from_running_engine(monkeypatch) -> None:
    """engine-session.json が存在しても自動 attach しないよう session file を無効化する。"""
    monkeypatch.setattr("engine.replay_session._read_session_file", lambda: None)
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


class _StubSession:
    """内部 Tachibana session のスタブ。"""

    def __init__(self) -> None:
        self.user_id = "test_user"
        self.is_demo = True


def _make_logged_in_session(monkeypatch, second_password: str | None = "second-pw") -> LiveSession:
    """login() 済みの LiveSession を返す。

    monkeypatch で内部 login API をスタブ化し、実際の Tachibana API を呼ばない。
    """

    async def _fake_login(user_id, password, *, is_demo, p_no_counter, http_client=None):
        return _StubSession()

    monkeypatch.setattr("engine.replay_session._tachibana_login_call", _fake_login)

    s = LiveSession(venue="tachibana", demo=True, second_password=second_password)
    s.__enter__()
    s.login(user_id="user123", password="pw456")
    return s


# ---------------------------------------------------------------------------
# D7: login() 前に run() → RuntimeError
# ---------------------------------------------------------------------------


def test_run_before_login_raises(monkeypatch) -> None:
    """D7: login() を呼ぶ前に run() を呼んだら RuntimeError を raise する。"""
    s = LiveSession(venue="tachibana", demo=True, second_password="second-pw")
    s.__enter__()

    with pytest.raises(RuntimeError, match="login"):
        s.run(
            instrument_id="8306.T",
            strategy_file="dummy.py",
            max_qty=100,
            max_notional_jpy=500_000,
        )


# ---------------------------------------------------------------------------
# D8: second_password=None で run() → RuntimeError
# ---------------------------------------------------------------------------


def test_run_without_second_password_raises(monkeypatch) -> None:
    """D8: second_password=None の LiveSession で run() を呼んだら RuntimeError を raise する。"""
    s = _make_logged_in_session(monkeypatch, second_password=None)

    with pytest.raises(RuntimeError, match="second_password"):
        s.run(
            instrument_id="8306.T",
            strategy_file="dummy.py",
            max_qty=100,
            max_notional_jpy=500_000,
        )


# ---------------------------------------------------------------------------
# D9: stop() が stop_event.set() を呼ぶ
# ---------------------------------------------------------------------------


def test_stop_sets_stop_event(monkeypatch) -> None:
    """D9: stop() を呼んだら内部の stop_event がセットされる。"""
    # start_live() を no-op でモックしてブロックしないようにする
    stop_events_created: list[threading.Event] = []

    def _fake_start_live(self_or_first, **kwargs):
        # instance method なので self が渡される場合と kwargs のみの場合を処理
        # monkeypatch でクラスメソッドを差し替えると self は最初の positional 引数
        stop_events_created.append(kwargs["stop_event"])

    monkeypatch.setattr(
        "engine.nautilus.engine_runner.NautilusRunner.start_live",
        _fake_start_live,
    )

    s = _make_logged_in_session(monkeypatch, second_password="second-pw")
    s.run(
        instrument_id="8306.T",
        strategy_file="dummy.py",
        max_qty=100,
        max_notional_jpy=500_000,
    )

    # run() 完了後に stop() を呼ぶ
    s.stop()

    assert s._stop_event is not None
    assert s._stop_event.is_set(), "stop() は stop_event.set() を呼ばなければならない"


# ---------------------------------------------------------------------------
# main: run() inprocess — start_live() が EngineStarted / EngineStopped を emit する
# ---------------------------------------------------------------------------


def test_run_inprocess_emits_engine_started_and_stopped(monkeypatch) -> None:
    """LiveSession.run() inprocess: monkeypatch した start_live() が EngineStarted と
    EngineStopped を on_event に emit することを確認する。"""
    events: list[dict] = []

    def _fake_start_live(_self, **kwargs):
        on_event = kwargs["on_event"]
        strategy_id = kwargs["strategy_id"]
        # EngineStarted を emit
        on_event({"event": "EngineStarted", "strategy_id": strategy_id,
                  "account_id": "tachibana", "ts_event_ms": 0})
        # EngineStopped を emit
        on_event({"event": "EngineStopped", "strategy_id": strategy_id,
                  "final_equity": "0"})

    monkeypatch.setattr(
        "engine.nautilus.engine_runner.NautilusRunner.start_live",
        _fake_start_live,
    )

    s = _make_logged_in_session(monkeypatch, second_password="second-pw")
    s.run(
        instrument_id="8306.T",
        strategy_file="dummy.py",
        max_qty=100,
        max_notional_jpy=500_000,
        on_event=events.append,
        strategy_id="test-strategy",
    )

    event_names = [e["event"] for e in events]
    assert "EngineStarted" in event_names, f"EngineStarted がない: {event_names}"
    assert "EngineStopped" in event_names, f"EngineStopped がない: {event_names}"

    started = next(e for e in events if e["event"] == "EngineStarted")
    assert started["strategy_id"] == "test-strategy"
    assert started["account_id"] == "tachibana"
