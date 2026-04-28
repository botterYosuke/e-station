"""Server IPC dispatch tests for StartEngine / StopEngine / LoadReplayData (N1.4).

`DataEngineServer._dispatch` の新規分岐を検証する。サーバ全体は起動せず、
最小モックで `_handle_*` ハンドラを直接呼ぶ。
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _ListOutbox:
    """_Outbox と duck-type 互換のテスト用スタブ (H5)。

    asyncio.Event.set は呼ばず、単純にキューとして動作する。
    """

    def __init__(self) -> None:
        self._q: deque = deque()

    def append(self, item: object) -> None:
        self._q.append(item)

    def popleft(self) -> object:
        return self._q.popleft()

    def __len__(self) -> int:
        return len(self._q)

    def __bool__(self) -> bool:
        return bool(self._q)

    def __iter__(self):
        return iter(list(self._q))


# M-9: DataEngineServer の必須属性が増えたら本ヘルパーも更新する。コードレビューで
# ``DataEngineServer.__init__`` の本体と ``_REQUIRED_ATTRS`` の差分を確認すること。
# サイレント未設定で AttributeError が出ないよう、ここでは「_make_server で未設定に
# なった属性を即座に発見する」目的の集中管理を行う。
_REQUIRED_ATTRS: dict[str, object] = {
    "_outbox": None,         # 各テストで _ListOutbox に差し替える
    "_mode": "replay",
    "_workers": None,        # 同上 (MagicMock)
    "_tachibana_session": None,
    "_tachibana_p_no_counter": None,
    "_session_holder": None,
    "_engine_tasks": None,
    "_replay_portfolio": None,  # N1.16: PortfolioView
    "_replay_strategy_id": None,
}


def _make_server(mode: str = "replay"):
    """Create a minimal DataEngineServer with mocked dependencies.

    M-9: 必須属性は ``_REQUIRED_ATTRS`` に集約し dict + 構築ループで設定する。
    DataEngineServer に新しい必須属性が増えたら ``_REQUIRED_ATTRS`` も合わせて更新する
    こと。silent な ``AttributeError`` 黙殺を防ぐため、レビュー時は
    ``DataEngineServer.__init__`` の body との差分を必ず確認する。

    `for_testing(...)` クラスメソッド導入は影響範囲が広いため本ラウンドでは見送り。
    """
    from engine.server import DataEngineServer

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    # M-9: 必須属性をループで設定 (レビュー粒度を ``_REQUIRED_ATTRS`` 1 箇所に集約)
    from decimal import Decimal
    from engine.nautilus.portfolio_view import PortfolioView
    defaults: dict[str, object] = {
        "_outbox": _ListOutbox(),  # H5: duck-type スタブ
        "_mode": mode,
        "_workers": {"tachibana": MagicMock()},
        "_tachibana_session": None,
        "_tachibana_p_no_counter": MagicMock(),
        "_session_holder": MagicMock(),
        "_engine_tasks": {},
        "_replay_portfolio": PortfolioView(Decimal("1000000")),  # N1.16
        "_replay_strategy_id": "",
    }
    # _REQUIRED_ATTRS に列挙された属性をすべて設定する。差分があれば KeyError で
    # 検出する (silent 黙殺防止)。
    for attr in _REQUIRED_ATTRS:
        setattr(server, attr, defaults[attr])
    return server


class TestLoadReplayDataDispatch:
    """LoadReplayData IPC が ReplayDataLoaded を outbox に流すこと。"""

    @pytest.mark.asyncio
    async def test_load_replay_data_emits_loaded_event(self) -> None:
        """LoadReplayData → ReplayDataLoaded(outbox)、件数は fixtures と一致。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "LoadReplayData",
            "request_id": "req-load-1",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(msg, base_dir=FIXTURES)

        events = [e for e in server._outbox if e.get("event") == "ReplayDataLoaded"]
        assert len(events) == 1
        assert events[0]["trades_loaded"] == 4
        assert events[0]["bars_loaded"] == 0
        # M-8 (R1b / schema 2.5): 単独 LoadReplayData では strategy_id=None を送る。
        assert events[0]["strategy_id"] is None

    @pytest.mark.asyncio
    async def test_load_replay_data_rejected_in_live_mode(self) -> None:
        """M3: mode='live' では LoadReplayData を Error{mode_mismatch} で拒否し
        J-Quants ファイルを開かない（D8 起動時固定 / spec §3.2）。"""
        server = _make_server(mode="live")
        msg = {
            "op": "LoadReplayData",
            "request_id": "req-load-live",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(msg, base_dir=FIXTURES)

        # ReplayDataLoaded は流れない
        assert not any(
            e.get("event") == "ReplayDataLoaded" for e in server._outbox
        )
        # Error{request_id, code="mode_mismatch"} が記録される
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["request_id"] == "req-load-live"
        assert errors[0]["code"] == "mode_mismatch"


class TestStartEngineDispatch:
    """StartEngine IPC が EngineStarted → EngineStopped を outbox に流すこと。"""

    @pytest.mark.asyncio
    async def test_start_engine_replay_emits_started_and_stopped(self) -> None:
        """mode='replay' で StartEngine{engine='Backtest'} は受理され、
        EngineStarted → ReplayDataLoaded → EngineStopped を順に outbox に積む。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-start-1",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)

        events = [
            e for e in server._outbox
            if e.get("event") in ("EngineStarted", "ReplayDataLoaded", "EngineStopped")
        ]
        kinds = [e["event"] for e in events]
        assert "EngineStarted" in kinds
        assert "EngineStopped" in kinds
        # 順序: started が stopped より先
        assert kinds.index("EngineStarted") < kinds.index("EngineStopped")

    @pytest.mark.asyncio
    async def test_start_engine_live_mode_rejects_backtest(self) -> None:
        """mode='live' で StartEngine{engine='Backtest'} は EngineError or Error を返す。"""
        server = _make_server(mode="live")
        msg = {
            "op": "StartEngine",
            "request_id": "req-start-2",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # H4: Error{request_id, code} を確認する
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["request_id"] == "req-start-2"
        assert errors[0]["code"] == "mode_mismatch"
        # H3: EngineError は送出されない（バリデーション失敗は Error のみ）
        assert not any(e.get("event") == "EngineError" for e in server._outbox)
        # EngineStarted は発火しない
        assert not any(e.get("event") == "EngineStarted" for e in server._outbox)


class TestStopEngineDispatch:
    """StopEngine IPC ハンドラの最小確認（走行中 task が無くても安全）。"""

    @pytest.mark.asyncio
    async def test_stop_engine_no_running_task_is_safe(self) -> None:
        """走行中タスクが無い状態で StopEngine を受けても raise しない。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StopEngine",
            "request_id": "req-stop-1",
            "strategy_id": "buy-and-hold",
        }
        # 例外を出さずに完了すること
        await server._handle_stop_engine(msg)

    @pytest.mark.asyncio
    async def test_stop_engine_running_runner_is_no_op(self) -> None:
        """H2: BacktestEngine.run() 走行中の StopEngine は dispose() を呼ばずに log のみ。"""
        import unittest.mock

        from engine.nautilus.engine_runner import NautilusRunner

        server = _make_server(mode="replay")
        runner = NautilusRunner()
        # 走行中フラグを立てて _engine が dispose されないことを確認
        runner._running = True
        engine_mock = MagicMock()
        runner._engine = engine_mock
        # M6: stop() が呼ばれることを spy する
        runner.stop = unittest.mock.MagicMock()
        server._engine_tasks["buy-and-hold"] = runner

        await server._handle_stop_engine(
            {"op": "StopEngine", "strategy_id": "buy-and-hold"}
        )
        # running 中は dispose() を絶対に呼ばない
        engine_mock.dispose.assert_not_called()
        # M6: stop() は 1 回呼ばれる
        runner.stop.assert_called_once()


class TestStartEngineFailureRecovery:
    """H1: EngineStarted 後に run() が raise した場合 EngineStopped で補完されること。"""

    @pytest.mark.asyncio
    async def test_engine_started_then_failure_emits_stopped_and_error(self) -> None:
        """EngineStarted 送出後の例外で EngineStopped + EngineError の双方が outbox に積まれる。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-fail-1",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        # NautilusRunner.start_backtest_replay を mock して、EngineStarted を on_event 経由で
        # 送出した後に意図的に raise する
        def fake_start(*, on_event, strategy_id, **kw):
            on_event({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": "TEST-ACCOUNT",
                "ts_event_ms": 1000,
            })
            raise RuntimeError("synthetic failure for test")

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay",
            side_effect=fake_start,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        kinds = [e.get("event") for e in server._outbox]
        assert "EngineStarted" in kinds
        assert "EngineStopped" in kinds
        assert "EngineError" in kinds
        # 順序: Started → Stopped → Error
        assert kinds.index("EngineStarted") < kinds.index("EngineStopped")
        assert kinds.index("EngineStopped") < kinds.index("EngineError")
        # final_equity は fallback の "0"
        stopped = next(e for e in server._outbox if e.get("event") == "EngineStopped")
        assert stopped["final_equity"] == "0"
        assert stopped["strategy_id"] == "buy-and-hold"
        # M5: EngineError に strategy_id が含まれる
        err = next(e for e in server._outbox if e.get("event") == "EngineError")
        assert err.get("strategy_id") == "buy-and-hold"
        # LOW-C: Error{request_id} イベントの確認
        err_events = [e for e in server._outbox if e.get("event") == "Error"]
        assert any(e.get("request_id") == "req-fail-1" for e in err_events)

    @pytest.mark.asyncio
    async def test_failure_before_engine_started_does_not_emit_stopped(self) -> None:
        """EngineStarted 未送出のうちに失敗した場合は EngineStopped を補完しない。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-fail-2",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        def fake_start(*, on_event, **kw):
            raise RuntimeError("failure before EngineStarted")

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay",
            side_effect=fake_start,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        kinds = [e.get("event") for e in server._outbox]
        assert "EngineStarted" not in kinds
        assert "EngineStopped" not in kinds  # 未 Start 状態では補完しない
        assert "EngineError" in kinds


class TestStartEngineTimeoutRecovery:
    """HIGH-1: TimeoutError パスで started_marker=False でも EngineStopped が積まれること。"""

    @pytest.mark.asyncio
    async def test_timeout_with_started_marker_false_emits_stopped(self) -> None:
        """HIGH-1: TimeoutError 時は started_marker に依存せず常に EngineStopped を送出する。"""
        import asyncio
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-timeout-1",
            "engine": "Backtest",
            "strategy_id": "timeout-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        # started_marker["sent"] が False のまま TimeoutError を発生させる
        # (EngineStarted を送出しないまま timeout)
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        kinds = [e.get("event") for e in server._outbox]
        # started_marker=False でも EngineStopped が積まれる (HIGH-1)
        assert "EngineStopped" in kinds
        stopped = next(e for e in server._outbox if e.get("event") == "EngineStopped")
        assert stopped["strategy_id"] == "timeout-strategy"
        assert stopped["final_equity"] == "0"

    @pytest.mark.asyncio
    async def test_timeout_message_is_not_empty(self) -> None:
        """MEDIUM-1: TimeoutError の message が空文字にならない。"""
        import asyncio
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-timeout-msg",
            "engine": "Backtest",
            "strategy_id": "timeout-strategy-msg",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        # EngineError と Error の message が空文字でないことを確認
        all_events = list(server._outbox)
        engine_errors = [e for e in all_events if e.get("event") == "EngineError"]
        errors = [e for e in all_events if e.get("event") == "Error"]
        assert engine_errors, "EngineError should be emitted on timeout"
        assert errors, "Error should be emitted on timeout"
        for e in engine_errors:
            assert e.get("message"), f"EngineError.message must not be empty, got: {e!r}"
        for e in errors:
            if e.get("code") == "timeout":
                assert e.get("message"), f"Error.message must not be empty, got: {e!r}"


class TestStartEngineMissingRequestId:
    """MEDIUM-2: request_id=None ガード。"""

    @pytest.mark.asyncio
    async def test_missing_request_id_returns_early(self) -> None:
        """MEDIUM-2: StartEngine で request_id が None/空の場合は早期 return する。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            # request_id を意図的に省略 (None)
            "engine": "Backtest",
            "strategy_id": "no-request-id",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # 早期 return するので outbox に何も積まれない
        assert len(server._outbox) == 0


class TestM7ReplayVenueSubmitOrderRejected:
    """M-7: venue=='replay' SubmitOrder は OrderRejected{REPLAY_NOT_IMPLEMENTED} を返す。"""

    @pytest.mark.asyncio
    async def test_replay_venue_submit_order_rejected_with_replay_not_implemented(self) -> None:
        server = _make_server(mode="replay")
        # _do_submit_order_inner が参照するカウンタ
        server._submit_order_inflight_count = 0
        msg = {
            "op": "SubmitOrder",
            "request_id": "req-replay-order",
            "venue": "replay",
            "order": {
                "client_order_id": "replay-cid-007",
                "instrument_id": "1301.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "time_in_force": "DAY",
                "post_only": False,
                "reduce_only": False,
            },
        }
        await server._do_submit_order_inner(msg)
        rejected = [e for e in server._outbox if e.get("event") == "OrderRejected"]
        assert len(rejected) == 1
        assert rejected[0]["reason_code"] == "REPLAY_NOT_IMPLEMENTED"
        assert rejected[0]["client_order_id"] == "replay-cid-007"

        # M-7 (R2 review-fix R2): OrderSubmitted も先に emit される (通常経路と対称)。
        # Rust UI の submitting フラグを reset するため。
        submitted = [e for e in server._outbox if e.get("event") == "OrderSubmitted"]
        assert len(submitted) == 1
        assert submitted[0]["client_order_id"] == "replay-cid-007"
        # 順序: OrderSubmitted → OrderRejected
        events = [e.get("event") for e in server._outbox]
        assert events.index("OrderSubmitted") < events.index("OrderRejected")


class TestM10UnknownEngineKind:
    """M-10: validate_start_engine の unknown engine kind は別 code で出す。"""

    @pytest.mark.asyncio
    async def test_unknown_engine_kind_returns_distinct_code(self) -> None:
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-unknown-kind",
            "engine": "Bogus",  # validate_start_engine が UnknownEngineKindError
            "strategy_id": "x",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["request_id"] == "req-unknown-kind"
        assert errors[0]["code"] == "unknown_engine_kind"


class TestM14StartEngineRaceGuard:
    """M-14: 同 strategy_id 連投で _engine_tasks が上書きされないこと。"""

    @pytest.mark.asyncio
    async def test_second_start_engine_with_same_strategy_id_is_rejected(self) -> None:
        server = _make_server(mode="replay")
        # 1 回目走行中を擬似再現: _engine_tasks に既存 entry を入れる
        server._engine_tasks["dup-strategy"] = MagicMock()
        existing_runner = server._engine_tasks["dup-strategy"]

        msg = {
            "op": "StartEngine",
            "request_id": "req-dup",
            "engine": "Backtest",
            "strategy_id": "dup-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)

        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["request_id"] == "req-dup"
        assert errors[0]["code"] == "engine_already_running"
        # 既存 runner 上書きが起きていない
        assert server._engine_tasks["dup-strategy"] is existing_runner


class TestHGCallSoonThreadsafeUnification:
    """H-G / H-2: ``_handle_start_engine`` の append 経路の使い分け。

    - **worker thread** (``asyncio.to_thread`` 内 ``_on_event``) →
      ``loop.call_soon_threadsafe`` 経由で main loop に戻す。
    - **main thread** (validation 失敗 / race guard / parse 失敗 / TimeoutError /
      except) → 直接 ``self._outbox.append`` する。これにより
      ``_handle_start_engine`` が ``asyncio.CancelledError`` でキャンセルされたときも
      Error イベントが落ちない (H-2, R2 review-fix R2)。

    本クラスの各テストは「main-thread 経路は **直 append**、worker 経路は
    **call_soon_threadsafe** 経由」という分離契約を pin する。
    """

    @pytest.mark.asyncio
    async def test_validation_error_uses_direct_append(self) -> None:
        """H-2: validation 失敗 (main thread coroutine 内) は直接 ``_outbox.append`` する。
        ``call_soon_threadsafe`` を呼ばないので、cancel 時も Error が落ちない。"""
        import asyncio

        server = _make_server(mode="live")  # live mode で Backtest → mode_mismatch
        msg = {
            "op": "StartEngine",
            "request_id": "req-hg-val",
            "engine": "Backtest",
            "strategy_id": "hg-val",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        loop = asyncio.get_running_loop()
        scheduled: list = []
        original_cs = loop.call_soon_threadsafe

        def spy(callback, *args):
            # _outbox.append への schedule のみ収集する
            try:
                if getattr(callback, "__self__", None) is server._outbox:
                    scheduled.append(args[0] if args else None)
            except Exception:
                pass
            return original_cs(callback, *args)

        with patch.object(loop, "call_soon_threadsafe", side_effect=spy):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        # Error{mode_mismatch} が outbox に 1 件存在する
        outbox_list = list(server._outbox)
        errors = [e for e in outbox_list if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "mode_mismatch"
        # H-2: main thread 経路は call_soon_threadsafe を経由しない (直 append)
        assert scheduled == [], (
            f"main-thread validation error should bypass call_soon_threadsafe; got {scheduled}"
        )

    @pytest.mark.asyncio
    async def test_race_guard_uses_direct_append(self) -> None:
        """H-2: race guard (main thread) も直 append。"""
        import asyncio

        server = _make_server(mode="replay")
        server._engine_tasks["dup-hg"] = MagicMock()
        msg = {
            "op": "StartEngine",
            "request_id": "req-hg-dup",
            "engine": "Backtest",
            "strategy_id": "dup-hg",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        loop = asyncio.get_running_loop()
        scheduled: list = []
        original_cs = loop.call_soon_threadsafe

        def spy(callback, *args):
            try:
                if getattr(callback, "__self__", None) is server._outbox:
                    scheduled.append(args[0] if args else None)
            except Exception:
                pass
            return original_cs(callback, *args)

        with patch.object(loop, "call_soon_threadsafe", side_effect=spy):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        outbox_list = list(server._outbox)
        # H-2: race guard も main thread 経路なので直 append (call_soon_threadsafe 不使用)
        assert len(outbox_list) >= 1
        assert scheduled == [], (
            f"main-thread race guard should bypass call_soon_threadsafe; got {scheduled}"
        )

    @pytest.mark.asyncio
    async def test_invalid_initial_cash_uses_direct_append(self) -> None:
        """H-2: initial_cash parse 失敗 (main thread) も直 append。"""
        import asyncio

        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-hg-cash",
            "engine": "Backtest",
            "strategy_id": "hg-cash",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "not-int",
                "granularity": "Trade",
            },
        }

        loop = asyncio.get_running_loop()
        scheduled: list = []
        original_cs = loop.call_soon_threadsafe

        def spy(callback, *args):
            try:
                if getattr(callback, "__self__", None) is server._outbox:
                    scheduled.append(args[0] if args else None)
            except Exception:
                pass
            return original_cs(callback, *args)

        with patch.object(loop, "call_soon_threadsafe", side_effect=spy):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        outbox_list = list(server._outbox)
        # H-2: parse 失敗も main thread 経路なので直 append
        assert len(outbox_list) >= 1
        assert scheduled == [], (
            f"main-thread invalid-config should bypass call_soon_threadsafe; got {scheduled}"
        )

    @pytest.mark.asyncio
    async def test_failure_path_worker_uses_call_soon_threadsafe_main_uses_direct(self) -> None:
        """EngineStarted 後の except パスは worker thread の EngineStarted のみ
        ``call_soon_threadsafe`` 経由で、main thread の (EngineStopped 補完 +
        EngineError + Error) は直 append される。
        H-2 (R2 review-fix R2): 経路ごとに append 方式が切り替わることを pin する。"""
        import asyncio

        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-hg-fail",
            "engine": "Backtest",
            "strategy_id": "hg-fail",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        def fake_start(*, on_event, strategy_id, **kw):
            on_event({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": "TEST",
                "ts_event_ms": 1000,
            })
            raise RuntimeError("hg fail")

        loop = asyncio.get_running_loop()
        scheduled: list = []
        original_cs = loop.call_soon_threadsafe

        def spy(callback, *args):
            try:
                if getattr(callback, "__self__", None) is server._outbox:
                    scheduled.append(args[0] if args else None)
            except Exception:
                pass
            return original_cs(callback, *args)

        with patch.object(loop, "call_soon_threadsafe", side_effect=spy), patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay",
            side_effect=fake_start,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        outbox_list = list(server._outbox)
        # H-2: worker thread が emit した EngineStarted のみ call_soon_threadsafe 経由。
        # except 補完 (EngineStopped / EngineError / Error) は main thread 経路で直 append。
        assert len(scheduled) == 1, (
            f"only worker-thread EngineStarted should use call_soon_threadsafe; got {scheduled}"
        )
        assert scheduled[0].get("event") == "EngineStarted"
        # 順序保証: EngineStarted → EngineStopped → EngineError → Error
        kinds = [e.get("event") for e in outbox_list]
        assert kinds.index("EngineStarted") < kinds.index("EngineStopped")
        assert kinds.index("EngineStopped") < kinds.index("EngineError")
        assert kinds.index("EngineError") < kinds.index("Error")

    @pytest.mark.asyncio
    async def test_cancelled_error_still_emits_error_to_outbox(self) -> None:
        """H-2 リグレッション: ``_handle_start_engine`` が ``asyncio.CancelledError``
        でキャンセルされても、validation で出した Error{code=...} は outbox に
        積まれている。

        R1b H-G の ``call_soon_threadsafe`` 統一だと、scheduled callback が
        cancel 後に drain されず Error が落ちる cancel-unsafe な経路を作っていた。
        H-2 (R2 review-fix R2) で main thread 経路を直 append に戻したことを pin する。
        """
        import asyncio

        server = _make_server(mode="live")  # live mode で Backtest → mode_mismatch
        msg = {
            "op": "StartEngine",
            "request_id": "req-h2-cancel",
            "engine": "Backtest",
            "strategy_id": "h2-cancel",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        # _handle_start_engine を Task として走らせ、すぐ cancel する。
        task = asyncio.create_task(server._handle_start_engine(msg, base_dir=FIXTURES))
        await asyncio.sleep(0)  # 1 step 進めて validation を通過させる
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Error{mode_mismatch} が outbox に少なくとも 1 件積まれていること
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) >= 1, (
            f"Error event should survive cancellation; outbox={list(server._outbox)}"
        )
        assert errors[0]["code"] == "mode_mismatch"


class TestStartEngineLowATasksCleanup:
    """LOW-A: initial_cash バリデーション失敗時に _engine_tasks に残骸が残らない。"""

    @pytest.mark.asyncio
    async def test_invalid_initial_cash_does_not_leak_engine_tasks(self) -> None:
        """LOW-A: initial_cash が不正な場合、_engine_tasks に strategy_id が残らない。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-bad-cash",
            "engine": "Backtest",
            "strategy_id": "bad-cash-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "not-a-number",  # 不正値
                "granularity": "Trade",
            },
        }

        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # Error が積まれている
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert any(e.get("code") == "invalid_config" for e in errors)
        # _engine_tasks に残骸が残っていない (LOW-A)
        assert "bad-cash-strategy" not in server._engine_tasks
