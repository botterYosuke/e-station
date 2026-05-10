"""issue #42 R2-A: 認証関連例外メッセージの credential scrub。

H5 (HIGH): ``engine_runner.py`` の ``warm_up()`` 例外時に ``str(exc)`` をそのまま
``EngineError{warm_up_failed}.message`` に詰めると、live 認証フェーズの credential
（second password / API key 等）が wire を経由して Rust 側 / GUI ログに漏れる
リスクがある。``_scrub_credential_exception`` ヘルパで「型名が認証関連」のときは
詳細を suppress し型名のみを expose する。

M9 (MED): ``server.py`` の ``engine_run_failed`` (Exception 経路) も同 helper を
通して message を scrub する。

完全な scrub ではなく「型名が ``Password`` / ``Auth`` / ``Credential`` を含むなら
詳細を隠す」防御線。誤検知（無関係 type が hit）は logging には残すので、
log を見れば調査可能。
"""

from __future__ import annotations

import queue as _stdlib_queue
import threading
from collections import deque
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engine.nautilus.engine_runner import (
    NautilusRunner,
    _scrub_credential_exception,
)


# ---------------------------------------------------------------------------
# _scrub_credential_exception unit tests
# ---------------------------------------------------------------------------


class TestScrubCredentialExceptionUnit:
    """``_scrub_credential_exception`` の単体テスト。"""

    def test_password_type_message_suppressed(self) -> None:
        class FakePasswordError(Exception):
            pass

        exc = FakePasswordError("secret-second-password=hunter2")
        result = _scrub_credential_exception(exc)
        assert "FakePasswordError" in result
        assert "hunter2" not in result, (
            f"credential value leaked through scrub: {result!r}"
        )
        assert "details suppressed" in result.lower()

    def test_auth_type_message_suppressed(self) -> None:
        class FakeAuthError(Exception):
            pass

        exc = FakeAuthError("apikey=AKIATEST123 secret=XYZ")
        result = _scrub_credential_exception(exc)
        assert "FakeAuthError" in result
        assert "AKIATEST123" not in result
        assert "XYZ" not in result

    def test_credential_type_message_suppressed(self) -> None:
        class FakeCredentialError(Exception):
            pass

        exc = FakeCredentialError("token=eyJabc.def.ghi")
        result = _scrub_credential_exception(exc)
        assert "FakeCredentialError" in result
        assert "eyJabc" not in result

    def test_unrelated_runtimeerror_message_preserved(self) -> None:
        # type name が credential 関連でない場合は str(exc) をそのまま返す
        exc = RuntimeError("simulated warm_up failure: timeout")
        result = _scrub_credential_exception(exc)
        assert "simulated warm_up failure: timeout" == result

    def test_unrelated_valueerror_message_preserved(self) -> None:
        exc = ValueError("max_qty must be positive")
        result = _scrub_credential_exception(exc)
        assert "max_qty must be positive" == result

    # R4 R3-SILENT-1: venue API 由来の例外も型名 prefix で scrub する
    # token / Password / Auth / Credential を含まない型名でも、
    # 取引所 API レスポンスの生テキスト（virtual URL, session token 断片, account 等）
    # が str(exc) に乗る可能性があるため保守的に suppress する。
    def test_tachibana_error_message_scrubbed(self) -> None:
        """``TachibanaError`` の str(exc) は wire / GUI に流さない。"""
        from engine.exchanges.tachibana_helpers import TachibanaError

        exc = TachibanaError(
            code="999",
            message="login failed token=eyJSECRET.payload.sig pass=hunter2",
        )
        result = _scrub_credential_exception(exc)
        assert "TachibanaError" in result, (
            f"type name must be exposed for diagnosability: {result!r}"
        )
        assert "hunter2" not in result, (
            f"credential value leaked through scrub: {result!r}"
        )
        assert "eyJSECRET" not in result, (
            f"token value leaked through scrub: {result!r}"
        )

    def test_kabu_api_error_message_scrubbed(self) -> None:
        """``KabuApiError`` の str(exc) は wire / GUI に流さない。"""
        from engine.exchanges.kabusapi_auth import KabuApiError

        exc = KabuApiError(
            code=4001001,
            message="not logged in: APIPassword=hunter2 acct=12345",
        )
        result = _scrub_credential_exception(exc)
        assert "KabuApiError" in result
        assert "hunter2" not in result
        assert "12345" not in result, (
            f"account value leaked through scrub: {result!r}"
        )

    def test_session_expired_error_message_scrubbed(self) -> None:
        """``SessionExpiredError`` (TachibanaError subclass) も scrub される。"""
        from engine.exchanges.tachibana_helpers import SessionExpiredError

        exc = SessionExpiredError(
            message="session expired token=eyJSECRET sid=sensitive-sid",
        )
        result = _scrub_credential_exception(exc)
        assert "SessionExpiredError" in result, (
            f"type name must be exposed: {result!r}"
        )
        assert "eyJSECRET" not in result
        assert "sensitive-sid" not in result

    def test_kabu_trade_locked_out_error_message_scrubbed(self) -> None:
        """``KabuTradeLockedOutError`` (KabuApiError subclass) も scrub される。"""
        from engine.exchanges.kabusapi_auth import KabuTradeLockedOutError

        exc = KabuTradeLockedOutError(
            code=4001013,
            message="trade locked out password=hunter2 acct=secret-account",
        )
        result = _scrub_credential_exception(exc)
        assert "KabuTradeLockedOutError" in result
        assert "hunter2" not in result
        assert "secret-account" not in result


# ---------------------------------------------------------------------------
# H5: warm_up 例外時の credential scrub（engine_runner 経路）
# ---------------------------------------------------------------------------


class _FakeAuthError(Exception):
    """credential 関連 type 名を持つ模擬例外。"""


class _FakeExecClient:
    instances: list["_FakeExecClient"] = []

    def __init__(self, **_kwargs) -> None:
        self.close_called = False
        self.id = "fake-exec-client"
        type(self).instances.append(self)

    async def warm_up(self) -> bool:
        # 認証エラー: メッセージに credential を含める（scrub されるはず）
        raise _FakeAuthError(
            "AuthenticationFailed: token=eyJSECRET.payload.sig pass=hunter2"
        )

    async def close(self) -> None:
        self.close_called = True


def _patch_min_dependencies(monkeypatch) -> None:
    """warm_up 失敗テスト用の最小モック（test_engine_runner_live_warmup_failure と同流儀）。"""
    monkeypatch.setattr(
        "engine.nautilus.engine_runner.is_market_open",
        lambda *_a, **_kw: True,
    )
    _FakeExecClient.instances = []
    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana.TachibanaLiveExecutionClient",
        lambda *a, **kw: _FakeExecClient(**kw),
    )

    class _FakeDataClient:
        def __init__(self, *_a, **_kw) -> None:
            self.id = "fake-data-client"

    class _FakeOrderIdMap:
        def __init__(self, *_a, **_kw) -> None:
            pass

    class _FakeEventBridge:
        def __init__(self, *_a, **_kw) -> None:
            self._loop = None

    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana_data.TachibanaLiveDataClient",
        _FakeDataClient,
    )
    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana_event_bridge.OrderIdMap",
        _FakeOrderIdMap,
    )
    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana_event_bridge.TachibanaEventBridge",
        _FakeEventBridge,
    )

    class _FakeNode:
        def __init__(self, *_a, **_kw) -> None:
            self._data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
            self._exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()

        def add_data(self, *_a, **_kw) -> None:
            pass

        def add_strategies(self, *_a, **_kw) -> None:
            pass

        def build(self) -> None:
            raise AssertionError("node.build() must NOT be called")

    monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNode)
    monkeypatch.setattr(
        "engine.nautilus.instrument_factory.make_equity_instrument",
        lambda *_a, **_kw: object(),
    )


def _make_runner_args(on_event):
    return dict(
        instrument_id="8306.T",
        strategy_file=None,
        strategy_init_kwargs=None,
        max_qty=100,
        max_notional_jpy=500_000,
        second_password="test-second-pw",
        session=object(),
        fd_queue=_stdlib_queue.Queue(maxsize=10),
        ec_queue=_stdlib_queue.Queue(maxsize=10),
        on_event=on_event,
        stop_event=threading.Event(),
        strategy_id="test-live-strategy",
    )


class TestWarmUpCredentialExceptionScrubbed:
    """H5: warm_up が credential 関連例外を raise したとき、message を scrub。"""

    def test_warmup_credential_exception_message_scrubbed(self, monkeypatch) -> None:
        """``_FakeAuthError`` を warm_up が raise → ``EngineError`` の message は
        type 名のみで credential 値を含まない。
        """
        _patch_min_dependencies(monkeypatch)
        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        warm_up_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "warm_up_failed"
        ]
        assert len(warm_up_failed) == 1, (
            f"expected exactly 1 EngineError(warm_up_failed), got: {events!r}"
        )
        msg = warm_up_failed[0]["message"]
        # 型名は残す
        assert "FakeAuthError" in msg, (
            f"type name must be exposed for diagnosability, got message={msg!r}"
        )
        # credential 値は隠す
        assert "hunter2" not in msg, (
            f"credential value leaked through wire: message={msg!r}"
        )
        assert "eyJSECRET" not in msg, (
            f"token value leaked through wire: message={msg!r}"
        )


# ---------------------------------------------------------------------------
# M9: server.py engine_run_failed credential scrub
# ---------------------------------------------------------------------------


class _ListOutbox:
    def __init__(self) -> None:
        self._q: deque = deque()

    def append(self, item: object) -> None:
        self._q.append(item)

    def send_to(self, ws: object, item: object) -> None:
        self._q.append(item)

    def popleft(self) -> object:
        return self._q.popleft()

    def count(self) -> int:
        return 0

    def __len__(self) -> int:
        return len(self._q)

    def __bool__(self) -> bool:
        return bool(self._q)

    def __iter__(self):
        return iter(list(self._q))


def _make_live_server():
    """live mode で StartEngine を扱える最小 DataEngineServer。"""
    from engine.server import DataEngineServer, ReplayState, LiveState
    from engine.nautilus.portfolio_view import PortfolioView

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _ListOutbox()
    server._mode = "live"
    server._workers = {"tachibana": MagicMock()}
    server._tachibana_session = None
    server._tachibana_p_no_counter = MagicMock()
    server._session_holder = MagicMock()
    server._session_holder.get_password.return_value = "test-password"
    server._engine_tasks = {}
    server._engine_stop_events = {}
    server._replay_speed_multiplier = 1
    server._replay_portfolio = PortfolioView(Decimal("1000000"))
    server._replay_strategy_id = ""
    server._cache_dir = Path("/tmp/test-engine-cache")
    server._replay_streaming_fills = []
    server._replay_state = ReplayState.IDLE
    server._live_state = LiveState.CONNECTED
    server._connected_venue = "tachibana"
    server._event_task = None
    server._replay_session_epoch = 0
    server._live_fd_queue = _stdlib_queue.Queue(maxsize=10_000)
    server._live_ec_queue = _stdlib_queue.Queue(maxsize=1_000)
    server._active_live_venues = set()
    return server


class TestEngineRunFailedCredentialScrub:
    """M9: server.py の engine_run_failed (Exception 経路) も credential を scrub する。"""

    @pytest.mark.asyncio
    async def test_engine_run_failed_credential_scrubbed(self) -> None:
        """``start_live`` 内で credential 関連例外を投げる → emit される
        ``EngineError{engine_run_failed}`` と ``Error{engine_run_failed}`` の
        message が scrub されている。
        """
        server = _make_live_server()

        class FakePasswordExc(Exception):
            pass

        def fake_start_live(self_runner, **kwargs):
            raise FakePasswordExc(
                "Login failed: second_password=hunter2 user=test"
            )

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            fake_start_live,
        ):
            msg = {
                "op": "StartEngine",
                "request_id": "req-secret",
                "engine": "Live",
                "strategy_id": "scrub-strategy",
                "config": {
                    "instrument_id": "8306.T",
                    "max_qty": 100,
                    "max_notional_jpy": 500_000,
                    "strategy_file": "dummy.py",
                },
            }
            await server._handle_start_engine(msg)

        # EngineError{engine_run_failed}
        engine_errs = [
            e for e in server._outbox
            if e.get("event") == "EngineError" and e.get("code") == "engine_run_failed"
        ]
        assert len(engine_errs) == 1, (
            f"expected EngineError(engine_run_failed), got: {list(server._outbox)!r}"
        )
        ee_msg = engine_errs[0]["message"]
        assert "FakePasswordExc" in ee_msg, (
            f"type name must be exposed: {ee_msg!r}"
        )
        assert "hunter2" not in ee_msg, (
            f"credential leaked through engine_run_failed wire: {ee_msg!r}"
        )

        # Error{engine_run_failed} (Rust 60s ハング解消用)
        ack_errs = [
            e for e in server._outbox
            if e.get("event") == "Error" and e.get("code") == "engine_run_failed"
        ]
        assert len(ack_errs) == 1, (
            f"expected Error(engine_run_failed), got: {list(server._outbox)!r}"
        )
        ack_msg = ack_errs[0]["message"]
        assert "hunter2" not in ack_msg, (
            f"credential leaked through Error ack wire: {ack_msg!r}"
        )
