"""schema 3.16: _push_replay_snapshot → StepBackward roundtrip テスト。

受け入れ条件（計画書 [M-6]）:
(1) 3 step 進んで StepBackward × 2 後に portfolio が正確に復元されること
(2) buffer が空になった後の StepBackward は EngineBusy を返すこと
(3) _push_replay_snapshot が deepcopy 失敗時にスナップショットをスキップすること
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from engine.server import DataEngineServer, ReplayState


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
    """Fake WebSocket connection."""
    return MagicMock()


def _make_portfolio(cash: str, ts_event_ms: int = 1700000000000) -> dict:
    """最小限の portfolio dict を生成する（_restore_snapshot / portfolio.get('cash') 形式）。"""
    return {"cash": cash, "ts_event_ms": ts_event_ms}


# ---------------------------------------------------------------------------
# (1) 3 step → StepBackward × 2 → portfolio 復元 roundtrip
# ---------------------------------------------------------------------------


class TestSnapshotRoundtrip:
    def test_step_backward_emits_replay_buying_power(self) -> None:
        """StepBackward 後に ReplayBuyingPower イベントが emit されること。"""
        import asyncio

        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        portfolio_step0 = {
            "event": "ReplayBuyingPower",
            "strategy_id": "s",
            "cash": "1000000",
            "buying_power": "1000000",
            "equity": "1000000",
            "ts_event_ms": 1700000000000,
        }
        portfolio_step1 = {
            **portfolio_step0,
            "cash": "1001000",
            "buying_power": "1001000",
            "equity": "1001000",
        }
        srv._push_replay_snapshot(0, portfolio_step0, [], {}, [])
        srv._push_replay_snapshot(1, portfolio_step1, [], {}, [])

        ws = _make_ws()
        srv._outbox.add_conn(ws)
        asyncio.run(srv._dispatch("StepBackward", {"op": "StepBackward", "request_id": "req"}, ws))

        events = list(srv._outbox)
        bp_events = [e for e in events if e.get("event") == "ReplayBuyingPower"]
        assert bp_events, "StepBackward must emit ReplayBuyingPower to update UI panel"
        assert bp_events[0]["cash"] == "1000000", (
            f"emitted ReplayBuyingPower must reflect step_0 cash, got {bp_events[0]['cash']}"
        )

    def test_three_steps_forward_two_steps_back_restores_portfolio(self) -> None:
        """3 step 進んで StepBackward × 2 後に step_index 0 の状態が復元されること。

        StepBackward のセマンティクス:
          - 末尾 (現在状態) を pop して捨て、新しい末尾 [-1] を restore する
          - 3 snapshots [0,1,2] → 1回目: pop 2, restore 1, buffer=[0,1], has_history=True
          - 2回目: pop 1, restore 0, buffer=[0], has_history=False
        """
        import asyncio

        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        # 3 つのスナップショットを push する（step 0, 1, 2）
        portfolios = [
            _make_portfolio("1000000", ts_event_ms=1700000000000),
            _make_portfolio("1001000", ts_event_ms=1700000001000),
            _make_portfolio("1002000", ts_event_ms=1700000002000),
        ]
        strategy_obj = object()
        for i, pf in enumerate(portfolios):
            srv._push_replay_snapshot(
                step_index=i,
                portfolio=pf,
                open_orders=[],
                strategy_state=strategy_obj,
                ui_events=[],
            )

        assert len(srv._replay_snapshots) == 3, "3 snapshots must be in buffer after 3 pushes"

        ws = _make_ws()
        srv._outbox.add_conn(ws)

        # 1 回目の StepBackward: step_2(現在状態)を捨て step_1 を restore → buffer に 2 件残る
        msg = {"op": "StepBackward", "request_id": "req-bwd-1"}
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        assert len(srv._replay_snapshots) == 2, (
            "After 1st StepBackward: 2 snapshots must remain"
        )
        events_1 = list(srv._outbox)
        restore_events_1 = [e for e in events_1 if e.get("event") == "RestoreSnapshot"]
        assert restore_events_1, "1st StepBackward must emit RestoreSnapshot"
        assert restore_events_1[0]["step_index"] == 1, (
            f"1st StepBackward must restore step_index=1, got {restore_events_1[0]['step_index']}"
        )
        history_ev_1 = next(
            e for e in events_1 if e.get("event") == "ReplayHistoryChanged"
        )
        assert history_ev_1["has_history"] is True, (
            "has_history must be True when 2 snapshots remain (can still go back)"
        )

        # 2 回目の StepBackward: step_1(現在状態)を捨て step_0 を restore → buffer に 1 件残る
        msg2 = {"op": "StepBackward", "request_id": "req-bwd-2"}
        asyncio.run(srv._dispatch(msg2.get("op"), msg2, ws))

        assert len(srv._replay_snapshots) == 1, (
            "After 2nd StepBackward: 1 snapshot must remain"
        )
        all_events = list(srv._outbox)
        restore_events = [e for e in all_events if e.get("event") == "RestoreSnapshot"]
        assert len(restore_events) == 2, (
            "Exactly 2 RestoreSnapshot events must have been emitted"
        )

        # 2 回目の RestoreSnapshot は step_index=0 を指すこと（1つ前 = step_0）
        second_restore = restore_events[1]
        assert second_restore["step_index"] == 0, (
            f"2nd StepBackward must restore step_index=0, got {second_restore['step_index']}"
        )

        # ReplayHistoryChanged の最後: 残り 1 件なので has_history=False（これ以上戻れない）
        history_events = [e for e in all_events if e.get("event") == "ReplayHistoryChanged"]
        last_history = history_events[-1]
        assert last_history["has_history"] is False, (
            "has_history must be False when only 1 snapshot remains (cannot go further back)"
        )

    def test_step_backward_on_two_snapshots_reports_no_history(self) -> None:
        """バッファに 2 件のとき StepBackward 後 has_history=False になること。

        2 snapshots [0,1]: pop 1, restore 0, buffer=[0], has_history=False.
        残り 1 件でもう戻れないことを Rust UI に伝える。
        """
        import asyncio

        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        srv._push_replay_snapshot(
            step_index=0,
            portfolio=_make_portfolio("1000000"),
            open_orders=[],
            strategy_state=object(),
            ui_events=[],
        )
        srv._push_replay_snapshot(
            step_index=1,
            portfolio=_make_portfolio("1001000"),
            open_orders=[],
            strategy_state=object(),
            ui_events=[],
        )
        assert len(srv._replay_snapshots) == 2

        ws = _make_ws()
        srv._outbox.add_conn(ws)

        msg = {"op": "StepBackward", "request_id": "req-two-snap"}
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        assert len(srv._replay_snapshots) == 1, "buffer must have 1 item after StepBackward"
        events = list(srv._outbox)
        history_ev = next(e for e in events if e.get("event") == "ReplayHistoryChanged")
        assert history_ev["has_history"] is False, (
            "has_history must be False when 1 snapshot remains (cannot go further back)"
        )


# ---------------------------------------------------------------------------
# (2) buffer 空 → StepBackward → EngineBusy
# ---------------------------------------------------------------------------


class TestSnapshotEmptyBuffer:
    def test_step_backward_with_single_snapshot_returns_engine_busy(self) -> None:
        """buffer に 1 件しかないとき StepBackward は EngineBusy を返すこと。

        1 snapshot = 現在状態のみ。「1つ前」が存在しないので戻れない。
        """
        import asyncio

        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        srv._push_replay_snapshot(
            step_index=0,
            portfolio=_make_portfolio("999000"),
            open_orders=[],
            strategy_state=object(),
            ui_events=[],
        )
        ws = _make_ws()
        srv._outbox.add_conn(ws)

        msg = {"op": "StepBackward", "request_id": "req-single"}
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        assert len(srv._replay_snapshots) == 1, (
            "buffer must be unchanged when EngineBusy is returned"
        )
        events = list(srv._outbox)
        assert any(e.get("event") == "EngineBusy" for e in events), (
            "StepBackward with only 1 snapshot must emit EngineBusy"
        )

    def test_step_backward_returns_engine_busy_after_exhausting_to_one(self) -> None:
        """2 件まで減らした後の StepBackward は EngineBusy を返すこと。

        2 snapshots → 1st StepBackward OK → buffer=[snap_0] →
        2nd StepBackward → EngineBusy（1件しか残っていない）
        """
        import asyncio

        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        srv._push_replay_snapshot(0, _make_portfolio("999000"), [], object(), [])
        srv._push_replay_snapshot(1, _make_portfolio("1000000"), [], object(), [])
        ws = _make_ws()
        srv._outbox.add_conn(ws)

        # 1 回目 OK → buffer=[snap_0]
        asyncio.run(srv._dispatch("StepBackward", {"op": "StepBackward", "request_id": "req-ok"}, ws))
        assert len(srv._replay_snapshots) == 1

        # 2 回目 → EngineBusy
        asyncio.run(srv._dispatch("StepBackward", {"op": "StepBackward", "request_id": "req-empty"}, ws))
        events = list(srv._outbox)
        assert any(e.get("event") == "EngineBusy" for e in events), (
            "StepBackward after buffer reaches 1 item must emit EngineBusy"
        )


# ---------------------------------------------------------------------------
# (3) deepcopy 失敗時のスナップショットスキップ
# ---------------------------------------------------------------------------


class TestPushSnapshotDeepCopyFailure:
    def test_deepcopy_failure_skips_snapshot(self) -> None:
        """copy.deepcopy が失敗した場合にスナップショットを push しないこと。"""

        class _Uncopiable:
            def __deepcopy__(self, _memo: dict) -> None:
                raise RuntimeError("cannot deepcopy")

        srv = _make_server()
        assert len(srv._replay_snapshots) == 0

        # deepcopy が失敗する strategy_state を渡す
        srv._push_replay_snapshot(
            step_index=0,
            portfolio=_make_portfolio("1000000"),
            open_orders=[],
            strategy_state=_Uncopiable(),
            ui_events=[],
        )

        assert len(srv._replay_snapshots) == 0, (
            "_push_replay_snapshot must skip snapshot when deepcopy fails"
        )

    def test_deepcopy_success_pushes_snapshot(self) -> None:
        """deepcopy が成功する strategy_state のとき snapshot が push されること。"""
        srv = _make_server()

        srv._push_replay_snapshot(
            step_index=1,
            portfolio=_make_portfolio("1005000"),
            open_orders=["order-mock"],
            strategy_state={"state_key": "value"},  # dict は deepcopy 可能
            ui_events=[{"event": "KlineUpdate"}],
        )

        assert len(srv._replay_snapshots) == 1, (
            "_push_replay_snapshot must add snapshot when deepcopy succeeds"
        )
        snap = srv._replay_snapshots[-1]
        assert snap.step_index == 1
        assert snap.portfolio["cash"] == "1005000"
        assert snap.open_orders == ["order-mock"]
        assert snap.ui_events == [{"event": "KlineUpdate"}]


# ---------------------------------------------------------------------------
# R2-C1: push_snapshot クロージャ注入テスト
# ---------------------------------------------------------------------------


class TestPushSnapshotInjection:
    def test_push_snapshot_closure_is_called_and_buffer_grows(self) -> None:
        """R2-C1: _push_replay_snapshot をクロージャとして直接呼んだとき
        _replay_snapshots バッファが増加すること（注入経路の動作確認）。"""
        srv = _make_server()
        assert len(srv._replay_snapshots) == 0

        # engine_runner.py の start_backtest_replay_streaming が push_snapshot=self._push_replay_snapshot
        # として渡す想定のクロージャを直接呼び出す
        push_fn = srv._push_replay_snapshot
        push_fn(
            step_index=0,
            portfolio=_make_portfolio("2000000"),
            open_orders=[],
            strategy_state={"x": 1},
            ui_events=[],
        )

        assert len(srv._replay_snapshots) > 0, (
            "push_snapshot クロージャを呼んだ後 _replay_snapshots バッファに "
            "1 件以上 push されていること (R2-C1)"
        )

    def test_push_snapshot_accepts_none_strategy_state_via_deepcopy_skip(self) -> None:
        """R2-C1: strategy_state=None は deepcopy に成功するため snapshot が保存されること。
        engine_runner が strategy_instance を渡せない場合のフォールバック確認。"""
        srv = _make_server()

        srv._push_replay_snapshot(
            step_index=0,
            portfolio=_make_portfolio("1000000"),
            open_orders=[],
            strategy_state=None,  # None は deepcopy 可能
            ui_events=[],
        )

        assert len(srv._replay_snapshots) == 1, (
            "strategy_state=None のとき deepcopy が成功して snapshot が保存されること (R2-C1)"
        )


# ---------------------------------------------------------------------------
# HIGH-2: _push_replay_snapshot が 0→1 遷移で _notify_history_fn を呼ぶこと
# ---------------------------------------------------------------------------


class TestHistoryChangedOnFirstPush:
    def test_first_push_calls_notify_fn(self) -> None:
        """buffer が空 → 1 件になった瞬間に _notify_history_fn が呼ばれること。"""
        srv = _make_server()
        notified: list[bool] = []
        srv._notify_history_fn = lambda: notified.append(True)

        srv._push_replay_snapshot(
            step_index=0,
            portfolio=_make_portfolio("1000000"),
            open_orders=[],
            strategy_state={},
            ui_events=[],
        )

        assert len(notified) == 1, "first push must call _notify_history_fn exactly once"

    def test_second_push_does_not_call_notify_fn_again(self) -> None:
        """buffer が 1 → 2 件になるときは _notify_history_fn を呼ばないこと。"""
        srv = _make_server()
        notified: list[bool] = []
        srv._notify_history_fn = lambda: notified.append(True)

        srv._push_replay_snapshot(0, _make_portfolio("1000000"), [], {}, [])
        srv._push_replay_snapshot(1, _make_portfolio("1001000"), [], {}, [])

        assert len(notified) == 1, "only first push (0→1 transition) must call _notify_history_fn"

    def test_no_notify_fn_does_not_crash(self) -> None:
        """_notify_history_fn が None のとき push しても例外が出ないこと。"""
        srv = _make_server()
        assert srv._notify_history_fn is None  # type: ignore[attr-defined]

        srv._push_replay_snapshot(0, _make_portfolio("1000000"), [], {}, [])
        assert len(srv._replay_snapshots) == 1


# ---------------------------------------------------------------------------
# HIGH-1: StepBackward 後に strategy_state が _restore_strategy_holder に入ること
# ---------------------------------------------------------------------------


class TestStrategyStateRestore:
    def test_step_backward_stores_strategy_state_in_holder(self) -> None:
        """StepBackward 後に _restore_strategy_holder[0] に strategy_state が入ること。

        2 snapshots 必要: pop 最新(step_1)して step_0 を restore するため。
        """
        import asyncio

        srv = _make_server()
        srv._replay_state = ReplayState.PAUSED
        srv._replay_paused = True

        expected = {"indicator": 42.5, "position": "long"}
        # step_0 は restore 対象（1つ前の状態）
        srv._push_replay_snapshot(0, _make_portfolio("1000000"), [], expected, [])
        # step_1 は現在状態（pop して捨てる）
        srv._push_replay_snapshot(1, _make_portfolio("1001000"), [], {"indicator": 99.0}, [])

        ws = _make_ws()
        srv._outbox.add_conn(ws)
        msg = {"op": "StepBackward", "request_id": "req-strategy-restore"}
        asyncio.run(srv._dispatch(msg.get("op"), msg, ws))

        holder = srv._restore_strategy_holder  # type: ignore[attr-defined]
        assert holder[0] is not None, "_restore_strategy_holder[0] must be set after StepBackward"
        assert holder[0]["indicator"] == 42.5, (
            "restored strategy_state must match step_0 snapshot (1つ前の状態)"
        )

    def test_restore_holder_cleared_on_push(self) -> None:
        """_push_replay_snapshot は _restore_strategy_holder[0] をリセットしないこと
        （runner が自分で None にするのが正しい契約）。"""
        srv = _make_server()
        srv._restore_strategy_holder[0] = {"old": True}  # type: ignore[attr-defined]

        srv._push_replay_snapshot(0, _make_portfolio("1000000"), [], {"new": True}, [])

        # push 自体はホルダーを書き換えない
        assert srv._restore_strategy_holder[0] == {"old": True}  # type: ignore[attr-defined]
