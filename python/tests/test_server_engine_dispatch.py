"""Server IPC dispatch tests for StartEngine / StopEngine / LoadReplayData (N1.4).

`DataEngineServer._dispatch` の新規分岐を検証する。サーバ全体は起動せず、
最小モックで `_handle_*` ハンドラを直接呼ぶ。
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _ListOutbox:
    """_Outbox と duck-type 互換のテスト用スタブ (H5)。

    asyncio.Event.set は呼ばず、単純にキューとして動作する。

    H4 強化: ``send_to(ws, item)`` 経由 (unicast) と ``append(item)`` 経由
    (broadcast) を区別して記録する。
    - ``self._q`` は従来通り順序を保つ payload キュー (`event` 種別の判定用)。
    - ``unicast_calls`` は ``[(ws, payload), ...]`` のタプル列。
    - ``broadcast_calls`` は append 経由の payload 列。
    これにより M10/M12/H4 の "unicast 専用 vs broadcast" をネガティブ assert
    できる。
    """

    def __init__(self) -> None:
        self._q: deque = deque()
        self.unicast_calls: list[tuple[object, dict]] = []
        self.broadcast_calls: list[dict] = []

    def append(self, item: object) -> None:
        self._q.append(item)
        if isinstance(item, dict):
            self.broadcast_calls.append(item)

    def send_to(self, ws: object, item: object) -> None:
        # M-GP8 (Phase 8 R1 / Phase 2): unicast 経路の test stub。
        self._q.append(item)
        if isinstance(item, dict):
            self.unicast_calls.append((ws, item))

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
    "_engine_stop_events": None,  # Task 2: threading.Event レジストリ
    "_replay_speed_multiplier": 1,  # N1.11: pacing 倍率（デフォルト 1）
    "_replay_portfolio": None,  # N1.16: PortfolioView
    "_replay_strategy_id": None,
    "_cache_dir": None,  # _do_get_order_list_replay が参照
    "_replay_streaming_fills": None,  # N1.13: streaming replay 約定蓄積
    # B3: state machine attributes
    "_replay_state": None,
    "_live_state": None,
    # schema 3.14: replay セッション識別子カウンタ
    "_replay_session_epoch": 0,
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
    from pathlib import Path
    from engine.server import ReplayState, LiveState
    defaults: dict[str, object] = {
        "_outbox": _ListOutbox(),  # H5: duck-type スタブ
        "_mode": mode,
        "_workers": {"tachibana": MagicMock()},
        "_tachibana_session": None,
        "_tachibana_p_no_counter": MagicMock(),
        "_session_holder": MagicMock(),
        "_engine_tasks": {},
        "_engine_stop_events": {},  # Task 2: threading.Event レジストリ
        "_replay_speed_multiplier": 1,  # N1.11: pacing 倍率
        "_replay_portfolio": PortfolioView(Decimal("1000000")),  # N1.16
        "_replay_strategy_id": "",
        "_cache_dir": Path("/tmp/test-engine-cache"),
        "_replay_streaming_fills": [],  # N1.13: streaming replay 約定蓄積
        # B3: state machine — LOADED は StartEngine/StopEngine が有効な状態
        "_replay_state": ReplayState.LOADED,
        "_live_state": LiveState.CONNECTED,
        # schema 3.14: replay セッション識別子カウンタ（初期 0、+1 で発番）
        "_replay_session_epoch": 0,
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
        """LoadReplayData → ReplayDataLoaded(outbox)。

        ファイル存在確認のみ行うため counts は 0（実際のカウントは StartEngine で行う）。
        """
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        # B3: LoadReplayData は IDLE 状態から呼ぶ必要がある。
        server._replay_state = ReplayState.IDLE
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
        assert events[0]["trades_loaded"] == 0
        assert events[0]["bars_loaded"] == 0
        # M-8 (R1b / schema 2.5): 単独 LoadReplayData では strategy_id=None を送る。
        assert events[0]["strategy_id"] is None
        # schema 3.14: 新セッション識別子。LoadReplayData 受理ごとに +1。
        assert events[0]["session_epoch"] == 1

    @pytest.mark.asyncio
    async def test_load_replay_data_session_epoch_monotonic(self) -> None:
        """schema 3.14: LoadReplayData を立て続けに 2 回受理すると epoch が単調増加する。

        ファイル切替時に旧 GUI が前 epoch のペインを全閉じできるよう、engine 側で
        セッション境界を一意に発番する必要がある。
        """
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.IDLE
        msg1 = {
            "op": "LoadReplayData",
            "request_id": "req-load-a",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(msg1, base_dir=FIXTURES)
        # 2 回目のために LOADED → IDLE に戻す（テスト用直書き）。
        server._replay_state = ReplayState.IDLE
        msg2 = dict(msg1, request_id="req-load-b", instrument_id="7203.TSE")
        await server._handle_load_replay_data(msg2, base_dir=FIXTURES)

        events = [e for e in server._outbox if e.get("event") == "ReplayDataLoaded"]
        assert len(events) == 2
        epochs = [e["session_epoch"] for e in events]
        assert epochs == [1, 2], f"expected monotonic [1, 2], got {epochs}"

    def test_start_engine_source_does_not_increment_session_epoch(self) -> None:
        """schema 3.14: 1 ファイル切替 = 1 epoch（source-pin）。

        ``LoadReplayData → StartEngine`` の 2-hop で 2 件の ``ReplayDataLoaded``
        が emit されるが、両者の ``session_epoch`` は同値でなければならない。
        ここで epoch が +2 進むと GUI が直前 LoadReplayData で生成したペインを
        ``StartEngine`` 受信時に drain して誤再生成する（review-fix R1 HIGH-1）。

        ``_handle_start_engine`` は nautilus / engine_runner の重い依存を持ち、
        outbox を観測する e2e ライクなテストは fixtures + asyncio.to_thread の
        起動コストが大きい。代替として server.py のソーステキストを直接 pin し
        「StartEngine 経路で ``_next_replay_session_epoch()`` を呼んでいないこと」
        と「``session_epoch=self._replay_session_epoch`` で再利用していること」
        を保証する。
        """
        import re
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "engine" / "server.py"
        text = src.read_text(encoding="utf-8")

        # _handle_start_engine 関数本体を抽出。
        m = re.search(
            r"async def _handle_start_engine\(.*?\n(?=    async def |\nclass |\Z)",
            text,
            re.DOTALL,
        )
        assert m is not None, "_handle_start_engine not found in server.py"
        body = m.group(0)

        # _next_replay_session_epoch() の実呼び出しが無いこと（コメント行は除外）。
        non_comment_lines = "\n".join(
            ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
        )
        assert "_next_replay_session_epoch()" not in non_comment_lines, (
            "`_handle_start_engine` must NOT call `_next_replay_session_epoch()`. "
            "Calling it here advances the epoch a second time within a single "
            "file-switch (LoadReplayData → StartEngine), causing the GUI to drain "
            "and re-generate panes immediately after the user opens a file. "
            "Reuse `self._replay_session_epoch` instead (allocated by LoadReplayData)."
        )

        # session_epoch を runner.start_backtest_replay(_streaming) に渡す行で
        # `self._replay_session_epoch` を読み取り渡しにしていること。
        assert "session_epoch=self._replay_session_epoch" in body, (
            "`_handle_start_engine` must pass "
            "`session_epoch=self._replay_session_epoch` to runner so the "
            "epoch allocated by LoadReplayData is reused (1 file = 1 epoch)."
        )

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


class TestRequestVenueLoginModeGuard:
    """RequestVenueLogin must be rejected in replay mode.

    Companion to the post-handshake `_startup_tachibana` guard in `_handle()`.
    Without this, a UI button or `/api/sidebar/tachibana/request-login` HTTP
    call in replay mode would still drive a full Tachibana login →
    VenueReady → bulk stats fetch path that broke replay startup on
    2026-04-30.
    """

    @pytest.mark.asyncio
    async def test_request_venue_login_rejected_in_replay_mode(self) -> None:
        server = _make_server(mode="replay")
        server._tachibana_login_inflight = MagicMock()
        server._tachibana_login_inflight.locked = MagicMock(return_value=False)

        with patch.object(
            type(server), "_startup_tachibana", new=AsyncMock()
        ) as mock_startup:
            await server._do_request_venue_login(
                {
                    "op": "RequestVenueLogin",
                    "request_id": "req-login-replay",
                    "venue": "tachibana",
                }
            )

        assert not mock_startup.called, (
            "_startup_tachibana must not be invoked from RequestVenueLogin "
            "while mode='replay'"
        )
        errors = [e for e in server._outbox if e.get("event") == "VenueError"]
        assert len(errors) == 1, f"expected 1 VenueError, got: {server._outbox}"
        assert errors[0]["request_id"] == "req-login-replay"
        assert errors[0]["code"] == "mode_mismatch"
        assert errors[0]["venue"] == "tachibana"

    @pytest.mark.asyncio
    async def test_request_venue_login_allowed_in_live_mode(self) -> None:
        """Negative control: in live mode the same call must reach
        `_startup_tachibana` (no mode_mismatch rejection)."""
        from engine.server import LiveState
        server = _make_server(mode="live")
        # B3: RequestVenueLogin は DISCONNECTED 状態のみ受理する。
        server._live_state = LiveState.DISCONNECTED
        server._tachibana_login_inflight = MagicMock()
        server._tachibana_login_inflight.locked = MagicMock(return_value=False)
        server._tachibana_session = MagicMock()
        server._cache_dir = MagicMock()

        with patch.object(
            type(server), "_startup_tachibana", new=AsyncMock()
        ) as mock_startup, patch(
            "engine.server.tachibana_clear_session"
        ):
            await server._do_request_venue_login(
                {
                    "op": "RequestVenueLogin",
                    "request_id": "req-login-live",
                    "venue": "tachibana",
                }
            )
            # Allow the create_task() body to schedule.
            await asyncio.sleep(0)

        assert mock_startup.called, (
            "_startup_tachibana must be invoked in live mode "
            "(regression: replay-mode guard erroneously firing in live)"
        )
        mode_mismatch_errors = [
            e for e in server._outbox
            if e.get("event") == "VenueError" and e.get("code") == "mode_mismatch"
        ]
        assert not mode_mismatch_errors, (
            f"live mode unexpectedly emitted mode_mismatch: {mode_mismatch_errors}"
        )


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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
    async def test_load_then_start_emits_same_session_epoch(self) -> None:
        """schema 3.14: 1 ファイル切替 = 1 epoch（e2e outbox 観測）。

        ``LoadReplayData → StartEngine`` の 2-hop で 2 件の ``ReplayDataLoaded``
        が emit されるが、両者の ``session_epoch`` は **同値**でなければならない。
        epoch が +2 進むと GUI が直前 LoadReplayData で生成したペインを drain
        して誤再生成する（review-fix R1 HIGH-1 の e2e 補強テスト R2 HIGH-2）。

        同じ test を source-scan
        (``test_start_engine_source_does_not_increment_session_epoch``) でも
        pin しているが、等価リファクタで源コードが書き換わったときに source-scan
        は false-pass / false-fail どちらにも転びやすいため、outbox 観測の
        e2e テストで真の不変条件 (= emit 値の一致) を保証する。
        """
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.IDLE

        load_msg = {
            "op": "LoadReplayData",
            "request_id": "req-load-then-start",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(load_msg, base_dir=FIXTURES)

        start_msg = {
            "op": "StartEngine",
            "request_id": "req-start-then",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
                "strategy_file": str(FIXTURES / "test_strategy.py"),
            },
        }
        await server._handle_start_engine(start_msg, base_dir=FIXTURES)

        loaded_events = [
            e for e in server._outbox if e.get("event") == "ReplayDataLoaded"
        ]
        assert len(loaded_events) == 2, (
            f"expected 2 ReplayDataLoaded events (LoadReplayData + StartEngine), "
            f"got {len(loaded_events)}"
        )
        epochs = [e["session_epoch"] for e in loaded_events]
        assert epochs[0] == epochs[1] == 1, (
            f"LoadReplayData and StartEngine must emit ReplayDataLoaded with "
            f"the SAME session_epoch (1 file-switch = 1 epoch). Got epochs={epochs}. "
            "If StartEngine path calls _next_replay_session_epoch() it advances "
            "to 2, which makes the GUI drain panes generated by LoadReplayData."
        )

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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        # B3: StopEngine は RUNNING 状態から呼ぶ。走行中タスクなしでも安全なことを確認。
        server._replay_state = ReplayState.RUNNING
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
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        # B3: StopEngine は RUNNING 状態から呼ぶ。
        server._replay_state = ReplayState.RUNNING
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
            },
        }

        # mode='replay' なので start_backtest_replay_streaming を mock する。
        # EngineStarted を on_event 経由で送出した後に意図的に raise する
        def fake_start(*, on_event, strategy_id, **kw):
            on_event({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": "TEST-ACCOUNT",
                "ts_event_ms": 1000,
            })
            raise RuntimeError("synthetic failure for test")

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay_streaming",
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
    async def test_failure_before_engine_started_emits_stopped(self) -> None:
        """Silent-M1 (Phase 8 R1 / Phase 2): EngineStarted 未送出のうちに runner が
        失敗した場合でも、Rust 側 state machine が stuck しないように EngineStopped
        を必ず補完送出する。旧実装は started_marker でガードして補完を抑制していたが、
        silent failure を生むため撤廃した。Rust 側は EngineStarted なしの
        EngineStopped を no-op として扱う前提（TimeoutError 経路と同じ契約）。
        """
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
            },
        }

        def fake_start(*, on_event, **kw):
            raise RuntimeError("failure before EngineStarted")

        # mode='replay' なので start_backtest_replay_streaming を mock する。
        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay_streaming",
            side_effect=fake_start,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        kinds = [e.get("event") for e in server._outbox]
        assert "EngineStarted" not in kinds
        assert "EngineStopped" in kinds  # Silent-M1: 未 Start でも常に補完
        assert "EngineError" in kinds
        # 順序: Stopped → Error (Started なし)
        assert kinds.index("EngineStopped") < kinds.index("EngineError")
        stopped = next(e for e in server._outbox if e.get("event") == "EngineStopped")
        assert stopped["final_equity"] == "0"
        assert stopped["strategy_id"] == "buy-and-hold"


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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
            },
        }

        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # 早期 return するので outbox に何も積まれない
        assert len(server._outbox) == 0


class TestM7ReplayVenueSubmitOrderRejected:
    """M-7 (N1.5): venue=='replay' SubmitOrder は OrderAccepted を返す（REPLAY_NOT_IMPLEMENTED は廃止）。"""

    @pytest.mark.asyncio
    async def test_replay_venue_submit_order_rejected_with_replay_not_implemented(self, tmp_path) -> None:
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        # B3: replay SubmitOrder は RUNNING 状態から呼ぶ。
        server._replay_state = ReplayState.RUNNING
        server._cache_dir = tmp_path
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

        # N1.5 以降: REPLAY_NOT_IMPLEMENTED で reject しない
        rejected = [e for e in server._outbox if e.get("event") == "OrderRejected"]
        assert len(rejected) == 0
        assert "REPLAY_NOT_IMPLEMENTED" not in str(list(server._outbox))

        # OrderSubmitted → OrderAccepted の順で emit される
        submitted = [e for e in server._outbox if e.get("event") == "OrderSubmitted"]
        assert len(submitted) == 1
        assert submitted[0]["client_order_id"] == "replay-cid-007"

        accepted = [e for e in server._outbox if e.get("event") == "OrderAccepted"]
        assert len(accepted) == 1
        assert accepted[0]["client_order_id"].startswith("REPLAY-")

        events = [e.get("event") for e in server._outbox]
        assert events.index("OrderSubmitted") < events.index("OrderAccepted")


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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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

        # mode='replay' なので start_backtest_replay_streaming を mock する。
        with patch.object(loop, "call_soon_threadsafe", side_effect=spy), patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay_streaming",
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
                "strategy_file": str(FIXTURES / "test_strategy.py"),
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


class TestStartEngineInvalidConfigExtraFields:
    """R2-H-1: EngineStartConfig.extra='forbid' が invalid_config エラーパスを発火すること。"""

    @pytest.mark.asyncio
    async def test_unknown_config_field_emits_invalid_config_error(self) -> None:
        """R2-H-1: config に未知フィールドを含む StartEngine は Error{code='invalid_config'} を emit する。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-extra-field",
            "engine": "Backtest",
            "strategy_id": "extra-field-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
                "unexpected_field": "evil",  # EngineStartConfig.extra="forbid" で弾かれる
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)

        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["request_id"] == "req-extra-field"
        assert errors[0]["code"] == "invalid_config"
        # EngineStarted は発火しない
        assert not any(e.get("event") == "EngineStarted" for e in server._outbox)


class TestStartEngineLowATasksCleanup:
    """LOW-A: initial_cash バリデーション失敗時に _engine_tasks に残骸が残らない。"""

    @pytest.mark.asyncio
    async def test_invalid_initial_cash_does_not_leak_engine_tasks(self) -> None:
        """LOW-A: initial_cash が不正な場合、_engine_tasks に strategy_id が残らない。

        Phase 2 修正: initial_cash のパースは _run() 内の replay 分岐で行われるため、
        strategy_file を指定して _run() まで到達させる必要がある。
        """
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
                "strategy_file": "dummy.py",  # Phase 2: _run() 内でパースするため strategy_file が必要
            },
        }

        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # Error が積まれている
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert any(e.get("code") == "invalid_config" for e in errors)
        # _engine_tasks に残骸が残っていない (LOW-A)
        assert "bad-cash-strategy" not in server._engine_tasks


class TestReplayModeUsesStreamingVersion:
    """replay モードで StartEngine を受けたとき streaming 版が呼ばれること。"""

    @pytest.mark.asyncio
    async def test_replay_mode_calls_streaming(self) -> None:
        """mode='replay' の _handle_start_engine は start_backtest_replay_streaming を呼ぶ。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-streaming-1",
            "engine": "Backtest",
            "strategy_id": "streaming-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
                "strategy_file": str(FIXTURES / "test_strategy.py"),
            },
        }

        streaming_called = False

        def fake_streaming(*, on_event, strategy_id, **kw):
            nonlocal streaming_called
            streaming_called = True
            on_event({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": "TEST-ACCOUNT",
                "ts_event_ms": 1000,
            })
            on_event({
                "event": "EngineStopped",
                "strategy_id": strategy_id,
                "final_equity": "1000000",
                "ts_event_ms": 2000,
            })
            from engine.nautilus.engine_runner import ReplayBacktestResult
            from decimal import Decimal
            return ReplayBacktestResult(
                strategy_id=strategy_id,
                final_equity=Decimal("1000000"),
                fill_timestamps=[],
                fill_last_prices=[],
                portfolio_fills=[],
                bars_loaded=0,
                trades_loaded=4,
                account_id="TEST-ACCOUNT",
                start_ts_event_ms=1000,
                stop_ts_event_ms=2000,
            )

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay_streaming",
            side_effect=fake_streaming,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        assert streaming_called, "mode='replay' で start_backtest_replay_streaming が呼ばれていない"
        kinds = [e.get("event") for e in server._outbox]
        assert "EngineStarted" in kinds
        assert "EngineStopped" in kinds

    @pytest.mark.asyncio
    async def test_replay_mode_stop_event_is_set_on_stop_engine(self) -> None:
        """mode='replay' の _handle_stop_engine は stop_event.set() を呼ぶ。"""
        import threading
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        # B3: StopEngine は RUNNING 状態から呼ぶ。
        server._replay_state = ReplayState.RUNNING
        stop_event = threading.Event()
        server._engine_stop_events["stop-ev-strategy"] = stop_event
        from engine.nautilus.engine_runner import NautilusRunner
        import unittest.mock
        runner = NautilusRunner()
        runner.stop = unittest.mock.MagicMock()
        server._engine_tasks["stop-ev-strategy"] = runner

        await server._handle_stop_engine(
            {"op": "StopEngine", "strategy_id": "stop-ev-strategy"}
        )

        assert stop_event.is_set(), "_handle_stop_engine が stop_event.set() を呼んでいない"

    @pytest.mark.asyncio
    async def test_stop_event_cleaned_up_after_engine_finishes(self) -> None:
        """_handle_start_engine の finally で _engine_stop_events から strategy_id が消える。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-cleanup-1",
            "engine": "Backtest",
            "strategy_id": "cleanup-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
                "strategy_file": str(FIXTURES / "test_strategy.py"),
            },
        }

        def fake_streaming(*, on_event, strategy_id, **kw):
            on_event({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": "TEST",
                "ts_event_ms": 1000,
            })
            on_event({
                "event": "EngineStopped",
                "strategy_id": strategy_id,
                "final_equity": "1000000",
                "ts_event_ms": 2000,
            })
            from engine.nautilus.engine_runner import ReplayBacktestResult
            from decimal import Decimal
            return ReplayBacktestResult(
                strategy_id=strategy_id,
                final_equity=Decimal("1000000"),
                fill_timestamps=[],
                fill_last_prices=[],
                portfolio_fills=[],
                bars_loaded=0,
                trades_loaded=0,
                account_id="TEST",
                start_ts_event_ms=1000,
                stop_ts_event_ms=2000,
            )

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay_streaming",
            side_effect=fake_streaming,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        # finally で _engine_stop_events から cleanup される
        assert "cleanup-strategy" not in server._engine_stop_events


# ---------------------------------------------------------------------------
# F7: StopReplay / ForceStopReplay ハンドラのテスト
# ---------------------------------------------------------------------------


class TestStopReplayDispatch:
    """StopReplay IPC ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_stop_replay_emits_replay_stopped_when_running(self) -> None:
        """RUNNING 状態で StopReplay を受信すると ReplayStopped が broadcast される。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        mock_runner = MagicMock()
        server._engine_tasks = {"sid-1": mock_runner}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-1"

        msg = {"op": "StopReplay", "request_id": "req-stop-1"}
        await server._handle_stop_replay(msg, ws=MagicMock())

        # ReplayStopped が outbox に入っていること
        events = [e for e in server._outbox if e.get("event") == "ReplayStopped"]
        assert len(events) == 1, f"Expected 1 ReplayStopped, got {list(server._outbox)}"
        assert events[0]["request_id"] == "req-stop-1"
        # final_equity は None（停止前）
        assert events[0]["final_equity"] is None

    @pytest.mark.asyncio
    async def test_stop_replay_calls_runner_stop(self) -> None:
        """StopReplay 受信時に active な runner の stop() が呼ばれること。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        mock_runner = MagicMock()
        server._engine_tasks = {"sid-2": mock_runner}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-2"

        await server._handle_stop_replay({"op": "StopReplay", "request_id": "req-stop-2"}, ws=MagicMock())

        mock_runner.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_replay_resets_state_to_idle(self) -> None:
        """H2: StopReplay が完了したら _replay_state は IDLE に戻ること。

        以前は中間遷移 STOPPING を pin していたが、H2 で
        ``_handle_stop_replay`` の末尾で IDLE に戻すよう変更したため、
        外から観測できる最終 state は IDLE になる。途中遷移 STOPPING は
        ``_handle_force_stop_replay`` の guard にも IDLE を期待するし、
        ReplayStopped を受信したクライアントが直後に LoadReplayData /
        StartEngine を投げる race を防ぐためにも reset → broadcast の順序が
        必要 (M-5 と対称)。
        """
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        mock_runner = MagicMock()
        server._engine_tasks = {"sid-3": mock_runner}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-3"

        await server._handle_stop_replay({"op": "StopReplay", "request_id": "req-stop-3"}, ws=MagicMock())

        assert server._replay_state == ReplayState.IDLE

    @pytest.mark.asyncio
    async def test_stop_replay_rejected_when_idle(self) -> None:
        """IDLE 状態で StopReplay を受信すると EngineBusy が返され、状態は変化しない。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.IDLE

        await server._handle_stop_replay({"op": "StopReplay", "request_id": "req-idle"}, ws=MagicMock())

        busy_events = [e for e in server._outbox if e.get("event") == "EngineBusy"]
        assert len(busy_events) == 1, f"Expected 1 EngineBusy, got {list(server._outbox)}"
        assert busy_events[0]["attempted_command"] == "StopReplay"
        # 状態は IDLE のまま
        assert server._replay_state == ReplayState.IDLE

    @pytest.mark.asyncio
    async def test_stop_replay_rejected_in_live_mode(self) -> None:
        """live モードで StopReplay を受信すると mode_mismatch EngineError が返される。

        StopReplay は replay-only command であり、live state と EngineBusy の組み合わせは
        pydantic バリデーションエラーになるため、EngineError{code=mode_mismatch} を返す。
        """
        from engine.server import LiveState
        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED

        await server._handle_stop_replay({"op": "StopReplay", "request_id": "req-live"}, ws=MagicMock())

        # EngineError{code=mode_mismatch} が返る
        error_events = [e for e in server._outbox if e.get("event") == "EngineError"]
        assert len(error_events) == 1, f"Expected 1 EngineError, got {list(server._outbox)}"
        assert error_events[0]["code"] == "mode_mismatch"
        # EngineBusy は返さない
        busy_events = [e for e in server._outbox if e.get("event") == "EngineBusy"]
        assert len(busy_events) == 0

    @pytest.mark.asyncio
    async def test_stop_replay_clears_strategy_id(self) -> None:
        """StopReplay 後に _replay_strategy_id がクリアされること。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING
        server._engine_tasks = {"sid-4": MagicMock()}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-4"

        await server._handle_stop_replay({"op": "StopReplay", "request_id": "req-clear"}, ws=MagicMock())

        assert server._replay_strategy_id == ""

    @pytest.mark.asyncio
    async def test_stop_replay_sets_stop_event(self) -> None:
        """StopReplay 受信時に _engine_stop_events の Event が set() されること。"""
        import threading
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        stop_event = threading.Event()
        mock_runner = MagicMock()
        server._engine_tasks = {"sid-5": mock_runner}
        server._engine_stop_events = {"sid-5": stop_event}
        server._replay_strategy_id = "sid-5"

        await server._handle_stop_replay({"op": "StopReplay", "request_id": "req-event"}, ws=MagicMock())

        assert stop_event.is_set(), "stop_event should be set after StopReplay"

    @pytest.mark.asyncio
    async def test_stop_replay_no_active_runner(self) -> None:
        """active な runner がない場合でも ReplayStopped を broadcast すること。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING
        server._engine_tasks = {}  # ランナーなし
        server._engine_stop_events = {}
        server._replay_strategy_id = ""

        await server._handle_stop_replay({"op": "StopReplay", "request_id": "req-norunner"}, ws=MagicMock())

        events = [e for e in server._outbox if e.get("event") == "ReplayStopped"]
        assert len(events) == 1, f"Expected 1 ReplayStopped, got {list(server._outbox)}"


class TestForceStopReplayDispatch:
    """ForceStopReplay IPC ハンドラのテスト。"""

    @pytest.mark.asyncio
    async def test_force_stop_replay_emits_replay_stopped(self) -> None:
        """ForceStopReplay は state に関わらず ReplayStopped を broadcast する。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        # STOPPING 状態でも強制実行できること
        server._replay_state = ReplayState.STOPPING

        mock_runner = MagicMock()
        server._engine_tasks = {"sid-f1": mock_runner}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-f1"

        await server._handle_force_stop_replay({"op": "ForceStopReplay", "request_id": "req-force-1"}, ws=MagicMock())

        events = [e for e in server._outbox if e.get("event") == "ReplayStopped"]
        assert len(events) == 1, f"Expected 1 ReplayStopped, got {list(server._outbox)}"
        assert events[0]["request_id"] == "req-force-1"

    @pytest.mark.asyncio
    async def test_force_stop_replay_calls_all_runners(self) -> None:
        """ForceStopReplay は全ランナーの stop() を呼ぶこと。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        mock_runner1 = MagicMock()
        mock_runner2 = MagicMock()
        server._engine_tasks = {"sid-f2a": mock_runner1, "sid-f2b": mock_runner2}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-f2a"

        await server._handle_force_stop_replay({"op": "ForceStopReplay", "request_id": "req-force-2"}, ws=MagicMock())

        mock_runner1.stop.assert_called_once()
        mock_runner2.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_stop_replay_when_idle(self) -> None:
        """IDLE 状態でも ForceStopReplay は実行でき（state guard なし）、ReplayStopped を返す。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.IDLE
        server._engine_tasks = {}
        server._engine_stop_events = {}
        server._replay_strategy_id = ""

        await server._handle_force_stop_replay({"op": "ForceStopReplay", "request_id": "req-force-idle"}, ws=MagicMock())

        # EngineBusy は返さない
        busy_events = [e for e in server._outbox if e.get("event") == "EngineBusy"]
        assert len(busy_events) == 0, f"ForceStopReplay must not emit EngineBusy, got {busy_events}"
        # ReplayStopped は返す
        events = [e for e in server._outbox if e.get("event") == "ReplayStopped"]
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_force_stop_replay_clears_strategy_id(self) -> None:
        """ForceStopReplay 後に _replay_strategy_id がクリアされること。"""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING
        server._engine_tasks = {"sid-f3": MagicMock()}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-f3"

        await server._handle_force_stop_replay({"op": "ForceStopReplay", "request_id": "req-force-3"}, ws=MagicMock())

        assert server._replay_strategy_id == ""

    @pytest.mark.asyncio
    async def test_force_stop_replay_sets_stop_events(self) -> None:
        """ForceStopReplay は _engine_stop_events の全 Event を set() すること。"""
        import threading
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        stop_event1 = threading.Event()
        stop_event2 = threading.Event()
        server._engine_tasks = {"sid-f4a": MagicMock(), "sid-f4b": MagicMock()}
        server._engine_stop_events = {"sid-f4a": stop_event1, "sid-f4b": stop_event2}
        server._replay_strategy_id = "sid-f4a"

        await server._handle_force_stop_replay({"op": "ForceStopReplay", "request_id": "req-force-4"}, ws=MagicMock())

        assert stop_event1.is_set(), "stop_event1 should be set"
        assert stop_event2.is_set(), "stop_event2 should be set"

    @pytest.mark.asyncio
    async def test_force_stop_replay_resets_state_to_idle(self) -> None:
        """H3: ForceStopReplay must reset _replay_state to IDLE so a subsequent
        LoadReplayData / StartEngine is not blocked by stuck STOPPING state.
        """
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.STOPPING

        mock_runner = MagicMock()
        server._engine_tasks = {"sid-h3": mock_runner}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-h3"

        await server._handle_force_stop_replay(
            {"op": "ForceStopReplay", "request_id": "req-h3"}, ws=MagicMock()
        )

        assert server._replay_state == ReplayState.IDLE, (
            "ForceStopReplay must reset _replay_state to IDLE; got "
            f"{server._replay_state!r}"
        )


class TestStopReplayUnicast:
    """H4 / M10 / M12: error responses must unicast to the caller, not broadcast."""

    @pytest.mark.asyncio
    async def test_stop_replay_rejected_in_live_mode_unicasts_to_caller(self) -> None:
        """H4: live モードでの mode_mismatch error は呼び出し元のみに unicast する。

        We assert the unicast contract by spying on `_outbox.send_to` and
        verifying it was called with the originating ws plus an EngineError
        payload (instead of the broadcast `append` path).
        """
        from unittest.mock import MagicMock
        from engine.server import LiveState
        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED

        fake_ws = MagicMock(name="caller_ws")
        # Spy on send_to to capture (ws, payload) pairs.
        send_to_calls: list[tuple[object, dict]] = []
        original_send_to = server._outbox.send_to

        def _spy(ws_arg, payload):  # type: ignore[no-untyped-def]
            send_to_calls.append((ws_arg, payload))
            return original_send_to(ws_arg, payload)

        server._outbox.send_to = _spy  # type: ignore[assignment]
        # Ensure `append` is NOT used for the error (broadcast path).
        broadcast_calls: list[dict] = []
        original_append = server._outbox.append

        def _spy_append(item):  # type: ignore[no-untyped-def]
            broadcast_calls.append(item)
            return original_append(item)

        server._outbox.append = _spy_append  # type: ignore[assignment]

        await server._handle_stop_replay(
            {"op": "StopReplay", "request_id": "req-h4"}, ws=fake_ws
        )

        # Unicast error must go through send_to with the caller's ws.
        unicast_errors = [
            (w, p) for (w, p) in send_to_calls
            if isinstance(p, dict) and p.get("event") == "EngineError"
            and p.get("code") == "mode_mismatch"
        ]
        assert unicast_errors, (
            f"Expected a unicast mode_mismatch EngineError via send_to(ws, ...); "
            f"got send_to_calls={send_to_calls}, broadcast_calls={broadcast_calls}"
        )
        # The unicast must target the caller's ws.
        assert all(w is fake_ws for (w, _) in unicast_errors), (
            "mode_mismatch EngineError must be unicast to the caller's ws (H4)"
        )
        # No broadcast (append) of the error payload.
        assert not any(
            isinstance(p, dict) and p.get("code") == "mode_mismatch"
            for p in broadcast_calls
        ), "mode_mismatch must NOT be broadcast via append() (H4)"

    @pytest.mark.asyncio
    async def test_stop_replay_missing_request_id_emits_error(self) -> None:
        """M10: StopReplay with empty/missing request_id is rejected with malformed_json."""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        # Empty request_id
        await server._handle_stop_replay({"op": "StopReplay"}, ws=MagicMock())
        errors = [e for e in server._outbox if e.get("event") == "EngineError"]
        assert any(e.get("code") == "malformed_json" for e in errors), (
            f"Expected malformed_json EngineError for missing request_id; got {list(server._outbox)}"
        )

    @pytest.mark.asyncio
    async def test_force_stop_replay_missing_request_id_emits_error(self) -> None:
        """M10: ForceStopReplay with empty/missing request_id is rejected with malformed_json."""
        server = _make_server(mode="replay")

        await server._handle_force_stop_replay(
            {"op": "ForceStopReplay", "request_id": ""}, ws=MagicMock()
        )
        errors = [e for e in server._outbox if e.get("event") == "EngineError"]
        assert any(e.get("code") == "malformed_json" for e in errors), (
            f"Expected malformed_json EngineError for empty request_id; got {list(server._outbox)}"
        )

    @pytest.mark.asyncio
    async def test_handle_stop_replay_rejects_none_ws(self) -> None:
        """M-1: ws is structurally mandatory for unicast error replies.
        Passing ws=None must trip the AssertionError before any IO happens.
        """
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING

        with pytest.raises(AssertionError, match="ws is mandatory"):
            await server._handle_stop_replay(
                {"op": "StopReplay", "request_id": "req-none"}, ws=None
            )

    @pytest.mark.asyncio
    async def test_handle_force_stop_replay_rejects_none_ws(self) -> None:
        """M-1: same contract as test_handle_stop_replay_rejects_none_ws."""
        server = _make_server(mode="replay")

        with pytest.raises(AssertionError, match="ws is mandatory"):
            await server._handle_force_stop_replay(
                {"op": "ForceStopReplay", "request_id": "req-none-f"}, ws=None
            )

    @pytest.mark.asyncio
    async def test_stop_replay_state_idle_before_broadcast(self) -> None:
        """H2/M-5: when StopReplay broadcasts ReplayStopped, _replay_state has
        ALREADY been reset to IDLE — no STOPPING residue race for clients that
        immediately re-issue LoadReplayData/StartEngine on receipt.
        """
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.RUNNING
        server._engine_tasks = {"sid-h2": MagicMock()}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-h2"

        # Spy on the broadcast point: capture _replay_state at the moment the
        # ReplayStopped payload is appended.
        outbox = server._outbox
        captured_state: list[object] = []
        original_append = outbox.append

        def _spy(item):  # type: ignore[no-untyped-def]
            if isinstance(item, dict) and item.get("event") == "ReplayStopped":
                captured_state.append(server._replay_state)
            return original_append(item)

        outbox.append = _spy  # type: ignore[assignment]

        await server._handle_stop_replay(
            {"op": "StopReplay", "request_id": "req-h2"}, ws=MagicMock()
        )

        assert captured_state == [ReplayState.IDLE], (
            "ReplayStopped must be broadcast AFTER _replay_state is reset to IDLE; "
            f"captured={captured_state}"
        )

    @pytest.mark.asyncio
    async def test_force_stop_replay_state_idle_before_broadcast(self) -> None:
        """M-5: same race-free order applies to ForceStopReplay."""
        from engine.server import ReplayState
        server = _make_server(mode="replay")
        server._replay_state = ReplayState.STOPPING
        server._engine_tasks = {"sid-m5": MagicMock()}
        server._engine_stop_events = {}
        server._replay_strategy_id = "sid-m5"

        outbox = server._outbox
        captured_state: list[object] = []
        original_append = outbox.append

        def _spy(item):  # type: ignore[no-untyped-def]
            if isinstance(item, dict) and item.get("event") == "ReplayStopped":
                captured_state.append(server._replay_state)
            return original_append(item)

        outbox.append = _spy  # type: ignore[assignment]

        await server._handle_force_stop_replay(
            {"op": "ForceStopReplay", "request_id": "req-m5"}, ws=MagicMock()
        )

        assert captured_state == [ReplayState.IDLE], (
            "ForceStopReplay must reset _replay_state to IDLE BEFORE broadcasting "
            f"ReplayStopped; captured={captured_state}"
        )
