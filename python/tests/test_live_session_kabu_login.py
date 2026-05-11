"""issue #42 R2 HIGH-1: LiveSession.login() / run() venue 分岐 unit tests.

旧実装:
- ``login()`` は venue 不問で ``_tachibana_login_call`` + ``DEV_TACHIBANA_*`` env を読む
- ``run()`` in-process arm は venue 不問で ``second_password is None`` チェックし
  ``runner.start_live`` に venue を渡さない

修正後:
- ``login()`` で ``self._venue == "kabu_station"`` のとき ``KabuStationVenue.startup_login()``
  を使う。``DEV_TACHIBANA_*`` / ``_tachibana_login_call`` は呼ばれない。
- ``run()`` in-process arm は ``runner.start_live(..., venue=self._venue)`` を必ず渡す。
- ``second_password is None`` チェックは venue=="tachibana" 限定にする
  (``kabu_station`` では第二暗証番号は使わない)。
"""
from __future__ import annotations

import pytest

from engine.replay_session import LiveSession


@pytest.fixture(autouse=True)
def _isolate_from_running_engine(monkeypatch) -> None:
    """engine-session.json / FLOWSURFACE_ENGINE_TOKEN を消して in-process に倒す。"""
    monkeypatch.setattr("engine.replay_session._read_session_file", lambda: None)
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)


# ---------------------------------------------------------------------------
# login() venue 分岐
# ---------------------------------------------------------------------------


def test_live_session_login_uses_kabu_startup_when_venue_kabu_station(monkeypatch):
    """venue=kabu_station の login() は KabuStationVenue.startup_login() を呼び、
    _tachibana_login_call は呼ばない。"""
    tachibana_called: list = []
    kabu_called: list = []

    async def _fake_tachibana_login(*args, **kwargs):
        tachibana_called.append((args, kwargs))
        raise AssertionError("tachibana login must not be called for venue=kabu_station")

    async def _fake_kabu_startup(self):
        kabu_called.append(self)
        return "fake-token"

    monkeypatch.setattr(
        "engine.replay_session._tachibana_login_call", _fake_tachibana_login
    )
    monkeypatch.setattr(
        "engine.exchanges.kabusapi.KabuStationVenue.startup_login", _fake_kabu_startup
    )

    with LiveSession(venue="kabu_station", demo=True, force_mode="inprocess") as s:
        # credential は渡さない（kabu_station は kabusapi local app の独自ダイアログ）
        s.login()
        assert s.is_logged_in is True

    assert kabu_called, "KabuStationVenue.startup_login must be called for kabu_station venue"
    assert not tachibana_called, (
        "_tachibana_login_call must NOT be called for venue=kabu_station"
    )


def test_live_session_login_tachibana_still_uses_tachibana(monkeypatch):
    """後方互換: venue=tachibana のとき _tachibana_login_call が呼ばれる。"""
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", "u")
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", "p")

    tachibana_called: list = []

    class _StubSession:
        pass

    async def _fake_tachibana_login(*args, **kwargs):
        tachibana_called.append((args, kwargs))
        return _StubSession()

    monkeypatch.setattr(
        "engine.replay_session._tachibana_login_call", _fake_tachibana_login
    )

    with LiveSession(venue="tachibana", demo=True, force_mode="inprocess") as s:
        s.login()
        assert s.is_logged_in is True

    assert tachibana_called, "tachibana login path should still work (backward compat)"


def test_live_session_login_kabu_wraps_login_cancelled_as_runtime_error(monkeypatch):
    """KabuLoginCancelledError は RuntimeError で wrap して raise する
    (CLI / GUI の error handler が共通化されているため)。"""
    from engine.exchanges.kabusapi_auth import KabuLoginCancelledError

    async def _fake_kabu_startup(self):
        raise KabuLoginCancelledError(0, "user cancelled")

    monkeypatch.setattr(
        "engine.exchanges.kabusapi.KabuStationVenue.startup_login", _fake_kabu_startup
    )

    with LiveSession(venue="kabu_station", demo=True, force_mode="inprocess") as s:
        with pytest.raises(RuntimeError, match="cancelled"):
            s.login()
        assert s.is_logged_in is False


# ---------------------------------------------------------------------------
# run() in-process venue 分岐
# ---------------------------------------------------------------------------


def test_live_session_run_inprocess_passes_venue_to_runner(monkeypatch):
    """run() in-process arm は runner.start_live(..., venue=self._venue) を渡す。"""

    # KabuStationVenue.startup_login を mock してログイン成功
    async def _fake_kabu_startup(self):
        return "fake-token"

    monkeypatch.setattr(
        "engine.exchanges.kabusapi.KabuStationVenue.startup_login", _fake_kabu_startup
    )

    captured_kwargs: dict = {}

    class _FakeRunner:
        def start_live(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(
        "engine.nautilus.engine_runner.NautilusRunner", _FakeRunner
    )

    with LiveSession(venue="kabu_station", demo=True, force_mode="inprocess") as s:
        s.login()
        s.run(
            instrument_id="9433.KabuStation Stock",
            strategy_file="dummy.py",
            max_qty=100,
            max_notional_jpy=500_000,
            strategy_id="test-strategy",
        )

    assert captured_kwargs.get("venue") == "kabu_station", (
        f"runner.start_live must receive venue=kabu_station, got {captured_kwargs!r}"
    )


def test_live_session_run_inprocess_skips_second_password_check_for_kabu(monkeypatch):
    """venue=kabu_station の in-process run() は second_password チェックを skip する。

    旧版は ``second_password is None`` で RuntimeError + SecondPasswordRequired event
    を投げていたが、kabu_station は KabuTradePasswordHolder 経由なので不要。
    """

    async def _fake_kabu_startup(self):
        return "fake-token"

    monkeypatch.setattr(
        "engine.exchanges.kabusapi.KabuStationVenue.startup_login", _fake_kabu_startup
    )

    class _FakeRunner:
        def start_live(self, **kwargs):
            return None

    monkeypatch.setattr(
        "engine.nautilus.engine_runner.NautilusRunner", _FakeRunner
    )

    events: list[dict] = []
    with LiveSession(venue="kabu_station", demo=True, force_mode="inprocess") as s:
        # second_password は渡さない (kabu_station は不要)
        s.login()
        # RuntimeError が出ないこと + SecondPasswordRequired event も emit されないこと
        s.run(
            instrument_id="9433.KabuStation Stock",
            strategy_file="dummy.py",
            max_qty=100,
            max_notional_jpy=500_000,
            on_event=events.append,
            strategy_id="test-strategy",
        )

    second_pw_events = [e for e in events if e.get("event") == "SecondPasswordRequired"]
    assert not second_pw_events, (
        "venue=kabu_station の run() で SecondPasswordRequired event を出してはいけない"
        f" (events={events!r})"
    )


def test_live_session_run_inprocess_tachibana_still_requires_second_password(monkeypatch):
    """後方互換: venue=tachibana では second_password=None なら RuntimeError + event。"""
    monkeypatch.setenv("DEV_TACHIBANA_USER_ID", "u")
    monkeypatch.setenv("DEV_TACHIBANA_PASSWORD", "p")

    class _StubSession:
        pass

    async def _fake_tachibana_login(*args, **kwargs):
        return _StubSession()

    monkeypatch.setattr(
        "engine.replay_session._tachibana_login_call", _fake_tachibana_login
    )

    events: list[dict] = []
    with LiveSession(
        venue="tachibana", demo=True, force_mode="inprocess", second_password=None
    ) as s:
        s.login()
        with pytest.raises(RuntimeError, match="second_password"):
            s.run(
                instrument_id="8306.T",
                strategy_file="dummy.py",
                max_qty=100,
                max_notional_jpy=500_000,
                on_event=events.append,
                strategy_id="test",
            )

    # second_password 要求の event が emit されている
    second_pw_events = [e for e in events if e.get("event") == "SecondPasswordRequired"]
    assert second_pw_events, "tachibana では SecondPasswordRequired event を出すべき"


# ---------------------------------------------------------------------------
# R3 M6: venue Literal narrowing — typo は runtime で reject
# ---------------------------------------------------------------------------


def test_live_session_init_rejects_typo_venue() -> None:
    """R3 M6: venue typo (e.g. 'kabu_statoin') は __init__ で ValueError。

    旧実装は ``venue: str`` のため typo を受理してしまい、後段の
    ``runner.start_live(venue=venue)`` まで silent に流れた。型注釈を
    ``Literal["tachibana", "kabu_station"]`` に narrow + runtime validation で
    早期に reject する。
    """
    with pytest.raises(ValueError, match="venue"):
        LiveSession(venue="kabu_statoin", demo=True, force_mode="inprocess")


def test_live_session_init_accepts_known_venues() -> None:
    """R3 M6: 既知 venue は __init__ を通る (regression)."""
    s1 = LiveSession(venue="tachibana", demo=True, force_mode="inprocess")
    assert s1._venue == "tachibana"
    s2 = LiveSession(venue="kabu_station", demo=True, force_mode="inprocess")
    assert s2._venue == "kabu_station"
