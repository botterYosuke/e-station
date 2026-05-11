"""issue #42 R2 CRITICAL-1: kabu_station live data PUSH 物理経路本配線.

server.py 側で kabu PUSH 受信時に live strategy 起動中の場合は
``_live_fd_queue`` / ``_live_ec_queue`` に流す経路を確立する。

検証対象:
- ``_on_kabu_board_push`` は ``LiveState.TRADING`` 且つ
  ``_connected_venue == "kabu_station"`` のとき、kabu board PUSH から派生する
  trade dict を ``_live_fd_queue`` に push する
- ``_kabu_fill_poller`` は ``LiveState.TRADING`` 中、poll した fill record を
  ``_live_ec_queue`` に push する
- _handle_start_engine の kabu_station 分岐は ``config_obj.instrument_id`` から
  symbol を抽出して ``_kabu_register_set.register`` + ``_kabu_put_register``
  を warm-up 時に明示的に走らせる
"""
from __future__ import annotations

import asyncio
import queue as _stdlib_queue
import uuid
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.kabusapi_adapter import KabuStationAdapter
from engine.server import DataEngineServer, LiveState


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


class _StubOutbox:
    """_Broadcaster の最小スタブ。"""

    def __init__(self) -> None:
        self._q: deque[dict] = deque()

    def append(self, item: dict) -> None:
        self._q.append(item)

    def send_to(self, _ws: object, item: dict) -> None:
        self._q.append(item)

    def count(self) -> int:
        return 0

    def __len__(self) -> int:
        return len(self._q)

    def __iter__(self):
        return iter(list(self._q))


_VALID_BOARD: dict = {
    "Symbol": "9433",
    "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
    "CurrentPrice": 3000.0,
    "Buy1": {"Price": 3000.0, "Qty": 100.0, "Time": "15:00:00", "Sign": "0101"},
    "Sell1": {"Price": 3001.0, "Qty": 50.0, "Time": "15:00:00", "Sign": "0101"},
}


def _make_server(
    *,
    live_state: LiveState = LiveState.DISCONNECTED,
    connected_venue: str | None = None,
) -> DataEngineServer:
    """最小フィールドのみ設定した DataEngineServer を返す。"""
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _StubOutbox()  # type: ignore[assignment]
    server._kabu_push_ssid = "test-ssid"
    server._kabu_push_seq = 0
    server._kabu_adapter = KabuStationAdapter([])
    server._engine_session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    # live state
    server._live_state = live_state
    server._connected_venue = connected_venue
    server._mode = "live"
    server._live_fd_queue = _stdlib_queue.Queue(maxsize=1000)
    server._live_ec_queue = _stdlib_queue.Queue(maxsize=1000)
    # R7 MEDIUM-3: prod __init__ で初期化される `_kabu_last_trading_volume` を
    # helper 側でも同期する。`_on_kabu_board_push` の defensive getattr lazy init
    # は撤廃しないが、helper-prod drift を解消することで将来 defensive 経路が
    # 消えた瞬間にテストが落ちる脆さをなくす。
    server._kabu_last_trading_volume = {}

    return server


# ---------------------------------------------------------------------------
# _on_kabu_board_push → _live_fd_queue (TRADING のときのみ)
# ---------------------------------------------------------------------------


class TestKabuBoardPushToLiveFdQueue:
    """R2 CRITICAL-1: live_state==TRADING + venue==kabu_station の時、
    kabu board PUSH から trade dict を _live_fd_queue に enqueue する。"""

    def test_pushes_trade_to_live_fd_queue_when_trading_with_positive_volume_delta(
        self,
    ) -> None:
        """R5-CRITICAL-2: kabu PUSH を 2 連続流して TradingVolume delta が
        per-trade qty として `_live_fd_queue` に enqueue される。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )

        # 1 回目: state seed (skip)
        s._on_kabu_board_push(
            {
                "Symbol": "9433",
                "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
                "CurrentPrice": 3000.0,
                "TradingVolume": 1000,
                "Buy1": {"Price": 3000.0, "Qty": 100.0, "Time": "15:00:00", "Sign": "0101"},
                "Sell1": {"Price": 3001.0, "Qty": 50.0, "Time": "15:00:00", "Sign": "0101"},
            }
        )
        # 2 回目: TradingVolume = 1100 → delta = 100 → push される
        s._on_kabu_board_push(
            {
                "Symbol": "9433",
                "CurrentPriceTime": "2024-01-01T15:00:01+09:00",
                "CurrentPrice": 3000.0,
                "TradingVolume": 1100,
                "Buy1": {"Price": 3000.0, "Qty": 100.0, "Time": "15:00:01", "Sign": "0101"},
                "Sell1": {"Price": 3001.0, "Qty": 50.0, "Time": "15:00:01", "Sign": "0101"},
            }
        )

        # _live_fd_queue に trade dict が enqueue されている
        assert not s._live_fd_queue.empty(), (
            "TRADING + kabu_station + positive volume delta 時は kabu board PUSH を "
            "_live_fd_queue に流すべき"
        )
        trade = s._live_fd_queue.get_nowait()
        # tachibana の FdFrameProcessor 互換形式 — ts_ms / price / qty / side が必須
        assert "ts_ms" in trade and isinstance(trade["ts_ms"], int)
        assert "price" in trade
        assert "qty" in trade
        assert "side" in trade

    def test_does_not_push_when_not_trading(self) -> None:
        """LiveState.CONNECTED（TRADING ではない）では enqueue しない。"""
        s = _make_server(
            live_state=LiveState.CONNECTED,
            connected_venue="kabu_station",
        )
        s._on_kabu_board_push(_VALID_BOARD)

        assert s._live_fd_queue.empty(), (
            "TRADING でない時は _live_fd_queue に流さない（live data 漏れ防止）"
        )

    def test_does_not_push_when_venue_is_tachibana(self) -> None:
        """venue が tachibana なら kabu PUSH を流さない（venue 取り違え防止）。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="tachibana",
        )
        s._on_kabu_board_push(_VALID_BOARD)

        assert s._live_fd_queue.empty(), (
            "connected_venue==tachibana で kabu PUSH を流すと cross-venue 汚染"
        )

    def test_normal_outbox_path_still_works_when_trading(self) -> None:
        """TRADING でも従来の outbox 経路（DepthSnapshot / Trades）は壊さない。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )
        s._on_kabu_board_push(_VALID_BOARD)

        # outbox に DepthSnapshot / Trades が書かれている（従来挙動）
        events = [item.get("event") for item in s._outbox._q]
        assert "DepthSnapshot" in events, (
            "TRADING 中でも DepthSnapshot は outbox に出すべき（GUI 描画用）"
        )

    def test_does_not_push_zero_qty_trade_to_live_fd_queue(self) -> None:
        """R3 M8: KabuStationAdapter.parse_execution は qty=0 を返す (kabu PUSH
        仕様)。0 数量の trade tick を live strategy SDK に流すと dedup / 異常
        トランザクションに見える ため、qty==0 のときは skip する。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )
        # _VALID_BOARD は kabu PUSH の通常形式 (qty 情報なし → parse_execution は 0)。
        s._on_kabu_board_push(_VALID_BOARD)

        assert s._live_fd_queue.empty(), (
            "qty=0 の kabu trade は _live_fd_queue に流さない (R3 M8)。"
            "Strategy SDK は depth_snapshot ベースで動作する設計。"
        )


# ---------------------------------------------------------------------------
# _kabu_fill_poller → _live_ec_queue
# ---------------------------------------------------------------------------


class TestKabuFillPollerToLiveEcQueue:
    """R2 CRITICAL-1: poll_fills の結果を _live_ec_queue に push する。"""

    def test_kabu_fill_poller_pushes_to_live_ec_queue_when_trading(self) -> None:
        """TRADING 中の poll fill を _live_ec_queue に enqueue する。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )
        # _kabu_venue mock — poll_fills が 1 件返したら break する fake
        fake_venue = MagicMock()
        fill_record = {
            "OrderID": "ORD-1",
            "State": 5,
            "Symbol": "9433",
            "Price": 1500.0,
            "OrderQty": 100,
        }

        # poll_fills を 1 回目は fill 返す → poller 内で enqueue → 2 回目で
        # CancelledError を投げてループ終了させる
        call_count = {"n": 0}

        async def _fake_poll_fills():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [fill_record]
            # 2 回目は CancelledError で抜ける
            raise asyncio.CancelledError()

        fake_venue.poll_fills = _fake_poll_fills
        s._kabu_venue = fake_venue

        # _kabu_fill_poller は asyncio.sleep(30) を含むので monkeypatch する
        async def _no_sleep(*_a, **_k):
            return None

        with patch("asyncio.sleep", _no_sleep):
            try:
                asyncio.run(s._kabu_fill_poller())
            except asyncio.CancelledError:
                pass

        # _live_ec_queue に fill が enqueue されている
        assert not s._live_ec_queue.empty(), (
            "_kabu_fill_poller は TRADING 中の fill を _live_ec_queue に流すべき"
        )
        got = s._live_ec_queue.get_nowait()
        assert got == fill_record

    def test_kabu_fill_poller_skips_live_ec_queue_when_venue_is_tachibana(self) -> None:
        """R3 H4: connected_venue='tachibana' + live_state=TRADING で kabu fill が
        来ても _live_ec_queue を汚染しない (cross-venue 汚染防止)。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="tachibana",
        )
        fake_venue = MagicMock()
        fill_record = {"OrderID": "ORD-1", "State": 5, "Symbol": "9433"}

        call_count = {"n": 0}

        async def _fake_poll_fills():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [fill_record]
            raise asyncio.CancelledError()

        fake_venue.poll_fills = _fake_poll_fills
        s._kabu_venue = fake_venue

        async def _no_sleep(*_a, **_k):
            return None

        with patch("asyncio.sleep", _no_sleep):
            try:
                asyncio.run(s._kabu_fill_poller())
            except asyncio.CancelledError:
                pass

        assert s._live_ec_queue.empty(), (
            "connected_venue=tachibana で kabu fill を _live_ec_queue に流すと "
            "tachibana の EC bridge を汚染する (cross-venue 汚染)"
        )

    def test_kabu_fill_poller_skips_live_ec_queue_when_not_trading(self) -> None:
        """TRADING でない（CONNECTED 等）時は _live_ec_queue に流さない。"""
        s = _make_server(
            live_state=LiveState.CONNECTED,
            connected_venue="kabu_station",
        )
        fake_venue = MagicMock()
        fill_record = {"OrderID": "ORD-1", "State": 5, "Symbol": "9433"}

        call_count = {"n": 0}

        async def _fake_poll_fills():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [fill_record]
            raise asyncio.CancelledError()

        fake_venue.poll_fills = _fake_poll_fills
        s._kabu_venue = fake_venue

        async def _no_sleep(*_a, **_k):
            return None

        with patch("asyncio.sleep", _no_sleep):
            try:
                asyncio.run(s._kabu_fill_poller())
            except asyncio.CancelledError:
                pass

        assert s._live_ec_queue.empty(), (
            "TRADING でない時は fill を _live_ec_queue に流さない"
        )


# ---------------------------------------------------------------------------
# _handle_start_engine kabu_station 分岐: warm-up 時の instrument register
# ---------------------------------------------------------------------------


class TestKabuStartEngineRegistersInstrument:
    """R2 CRITICAL-1 / R3 C1: live 起動時に config_obj.instrument_id の symbol を
    _kabu_register_set に register + PUT /register を走らせる。

    R3 C1: register と PUT の **順序** が逆だと strategy 銘柄が PUT payload に
    含まれず live data が流れない silent failure になる。動的に PUT payload を
    capture して strategy 銘柄が含まれることを assert する。
    """

    def test_handle_start_engine_kabu_calls_register_and_put_register(self) -> None:
        import inspect
        from engine.server import DataEngineServer

        src = inspect.getsource(DataEngineServer._handle_start_engine)
        # kabu_station 分岐内で register / PUT /register を **両方** 呼んでいる
        # ことを pin。具体的な行は実装依存だが、ソース全体に両方が含まれる必要あり。
        assert "_kabu_register_set" in src, (
            "_handle_start_engine kabu 分岐で _kabu_register_set を触る必要あり"
            " (warm-up 時の instrument 自動 register、R2 CRITICAL-1)"
        )
        assert "_kabu_put_register" in src, (
            "_handle_start_engine kabu 分岐で _kabu_put_register 経由で PUT /register を"
            " 走らせる必要あり (R2 CRITICAL-1)"
        )


# ---------------------------------------------------------------------------
# R3 C1 / H1: register → PUT 順序の動的検証
# ---------------------------------------------------------------------------


def _make_server_for_start_engine(
    *,
    live_state: LiveState = LiveState.CONNECTED,
    connected_venue: str = "kabu_station",
):
    """_handle_start_engine 経路を流す最小フィールド構成の DataEngineServer."""
    from engine.exchanges.kabusapi_register import RegisterSet as KabuRegisterSet

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _StubOutbox()  # type: ignore[assignment]
    server._kabu_push_ssid = "test-ssid"
    server._kabu_push_seq = 0
    server._kabu_adapter = KabuStationAdapter([])
    server._engine_session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    server._live_state = live_state
    server._connected_venue = connected_venue
    server._mode = "live"
    server._live_fd_queue = _stdlib_queue.Queue(maxsize=1000)
    server._live_ec_queue = _stdlib_queue.Queue(maxsize=1000)
    server._engine_tasks = {}
    server._engine_stop_events = {}
    server._tachibana_session = None
    server._session_holder = MagicMock()
    server._active_live_venues = set()
    # kabu_station 接続済として _kabu_venue を mock 設定。
    fake_venue = MagicMock()
    fake_venue.is_connected = True
    server._kabu_venue = fake_venue
    # RegisterSet を fresh instance に
    server._kabu_register_set = KabuRegisterSet()
    # R7 MEDIUM-3: prod __init__ で初期化される `_kabu_last_trading_volume` を
    # helper 側でも同期する (helper-prod drift 解消)。
    server._kabu_last_trading_volume = {}
    return server


@pytest.mark.asyncio
class TestKabuStartEngineRegisterPutOrdering:
    """R3 C1 + H1: register が PUT /register より **前** に実行されている
    ことを動的に検証する。順序が逆だと strategy 銘柄が PUT payload に
    含まれず live data が流れない silent failure になる。
    """

    async def test_handle_start_engine_kabu_includes_strategy_symbol_in_put_register_payload(
        self,
    ) -> None:
        """PUT /register の payload に strategy symbol が含まれる (register が先)。"""
        from engine.server import DataEngineServer

        s = _make_server_for_start_engine()

        captured_payload: list[list] = []

        async def _fake_put(syms):
            captured_payload.append(list(syms))
            return True

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        # NautilusRunner.start_live は呼ばれないように patch (asyncio.to_thread 経由)
        msg = {
            "op": "StartEngine",
            "request_id": "req-r3-c1-1",
            "engine": "Live",
            "strategy_id": "strat-c1-1",
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
            await DataEngineServer._handle_start_engine(s, msg)
            await asyncio.sleep(0)

        assert captured_payload, (
            "_kabu_put_register must be called before _run (live data flow)"
        )
        # strategy symbol が含まれる (= register が PUT より前)
        symbols_only = [sym for (sym, _ex) in captured_payload[0]]
        assert "9433" in symbols_only, (
            f"strategy symbol '9433' must be in PUT /register payload (register must "
            f"run before PUT). got: {captured_payload[0]!r}"
        )

    async def test_handle_start_engine_kabu_register_failure_emits_engine_error_and_skips_run(
        self,
    ) -> None:
        """register が raise したら EngineError{code:'kabu_register_failed'} emit
        + _live_state を CONNECTED のまま維持 + _run 未呼出。"""
        from engine.server import DataEngineServer

        s = _make_server_for_start_engine()
        # register を raise させる
        s._kabu_register_set.register = MagicMock(  # type: ignore[assignment]
            side_effect=RuntimeError("boom")
        )

        async def _fake_put(syms):
            return True

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        msg = {
            "op": "StartEngine",
            "request_id": "req-r3-c1-2",
            "engine": "Live",
            "strategy_id": "strat-c1-2",
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)) as _to_thread:
            await DataEngineServer._handle_start_engine(s, msg)
            # outbox は call_soon_threadsafe 経由で append される (worker thread 経路
            # 用の _on_event_tracked 経由)。次の event-loop tick で実行されるので
            # ここで一度 yield して drain する。
            await asyncio.sleep(0)

        errors = [
            e for e in s._outbox._q
            if e.get("event") == "EngineError"
            and e.get("code") == "kabu_register_failed"
        ]
        assert errors, (
            f"register failure must emit EngineError{{code='kabu_register_failed'}}; "
            f"outbox: {list(s._outbox._q)!r}"
        )
        # _run 未呼出 = asyncio.to_thread が呼ばれていない
        assert _to_thread.await_count == 0, (
            "register failure must skip _run (asyncio.to_thread)"
        )
        # _live_state は TRADING に進んでいない
        assert s._live_state == LiveState.CONNECTED, (
            "register failure must keep _live_state at CONNECTED"
        )

    async def test_handle_start_engine_kabu_put_register_failure_skips_run(
        self,
    ) -> None:
        """PUT /register が False を返したら EngineError + _run 未呼出。"""
        from engine.server import DataEngineServer

        s = _make_server_for_start_engine()

        async def _fake_put(syms):
            return False

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        msg = {
            "op": "StartEngine",
            "request_id": "req-r3-c1-3",
            "engine": "Live",
            "strategy_id": "strat-c1-3",
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)) as _to_thread:
            await DataEngineServer._handle_start_engine(s, msg)
            await asyncio.sleep(0)

        errors = [
            e for e in s._outbox._q
            if e.get("event") == "EngineError"
            and e.get("code") == "kabu_register_failed"
        ]
        assert errors, (
            f"PUT failure must emit EngineError{{code='kabu_register_failed'}}; "
            f"outbox: {list(s._outbox._q)!r}"
        )
        assert _to_thread.await_count == 0, (
            "PUT failure must skip _run"
        )
        assert s._live_state == LiveState.CONNECTED


# ---------------------------------------------------------------------------
# R5-CRITICAL-1 / R5 反映: register/PUT 失敗 early-return path での
# EngineStopped + Error{request_id} + cleanup 補完の検証.
# R3 で register/PUT を _run の外に移動した結果、catch-all (`except Exception`)
# から外れ、両 early-return 経路で EngineStopped (Rust state machine unstuck) /
# Error{request_id} (Rust 60s hang 解消) / `_active_live_venues` discard /
# `_engine_tasks` / `_engine_stop_events` pop が脱漏していた。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestKabuStartEngineEarlyReturnCleanup:
    """R5-CRITICAL-1: register / PUT 失敗の early-return 経路で
    EngineStopped + Error{request_id} + cleanup を完全に行う。"""

    async def test_kabu_register_failure_emits_engine_stopped_and_error_request_id(
        self,
    ) -> None:
        """register が raise したとき:
        - EngineStopped event が outbox に出る (Rust state machine unstuck)
        - Error{request_id, code:'kabu_register_failed'} event が outbox に出る
          (Rust 60s hang 解消)
        - `_active_live_venues` から venue が discard されている
        - `_engine_tasks` / `_engine_stop_events` から strategy_id が pop されている
        """
        from engine.server import DataEngineServer

        s = _make_server_for_start_engine()
        # 事前に venue を _active_live_venues に push されている期待: handler が register
        # cleanup 前に _active_live_venues に追加するため。
        s._kabu_register_set.register = MagicMock(  # type: ignore[assignment]
            side_effect=RuntimeError("boom")
        )

        async def _fake_put(syms):
            return True

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        request_id = "req-r5-c1-register"
        strategy_id = "strat-r5-c1-register"
        msg = {
            "op": "StartEngine",
            "request_id": request_id,
            "engine": "Live",
            "strategy_id": strategy_id,
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
            await DataEngineServer._handle_start_engine(s, msg)
            await asyncio.sleep(0)

        events = list(s._outbox._q)

        # (1) EngineStopped event が出ている
        engine_stopped = [
            e for e in events
            if e.get("event") == "EngineStopped"
            and e.get("strategy_id") == strategy_id
        ]
        assert engine_stopped, (
            f"register failure must emit EngineStopped (Rust state machine unstuck); "
            f"outbox events: {[e.get('event') for e in events]!r}"
        )

        # (2) Error{request_id, code:'kabu_register_failed'} event が出ている
        errors = [
            e for e in events
            if e.get("event") == "Error"
            and e.get("request_id") == request_id
            and e.get("code") == "kabu_register_failed"
        ]
        assert errors, (
            f"register failure must emit Error{{request_id, code='kabu_register_failed'}} "
            f"to unblock Rust RequestEngineStart 60s wait; "
            f"outbox events: {events!r}"
        )

        # (3) `_active_live_venues` から venue が discard されている
        assert "kabu_station" not in s._active_live_venues, (
            "register failure must discard live_venue from _active_live_venues "
            "so the next StartEngine on the same venue is not rejected"
        )

        # (4) `_engine_tasks` / `_engine_stop_events` から pop されている
        assert strategy_id not in s._engine_tasks, (
            "register failure must pop strategy_id from _engine_tasks"
        )
        assert strategy_id not in s._engine_stop_events, (
            "register failure must pop strategy_id from _engine_stop_events"
        )

        # (5) R7 MEDIUM-5: _live_state は CONNECTED に戻り、`_engine_stop_events`
        # の get も None である (state machine 完全リセット + helper 経路網羅性 pin)。
        assert s._live_state == LiveState.CONNECTED, (
            "register failure must leave _live_state == CONNECTED "
            "(early-abort cleanup completeness)"
        )
        assert s._engine_stop_events.get(strategy_id) is None, (
            "register failure must clear _engine_stop_events[strategy_id] "
            "(pop completeness; redundant with 'not in' but explicit intent pin)"
        )

    async def test_kabu_put_register_failure_emits_engine_stopped_and_error_request_id(
        self,
    ) -> None:
        """PUT /register が False (失敗) を返したとき:
        - EngineStopped event が outbox に出る
        - Error{request_id, code:'kabu_register_failed'} event が outbox に出る
        - `_active_live_venues` から venue が discard されている
        - `_engine_tasks` / `_engine_stop_events` から pop されている
        """
        from engine.server import DataEngineServer

        s = _make_server_for_start_engine()

        async def _fake_put(syms):
            return False  # PUT 失敗

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        request_id = "req-r5-c1-put"
        strategy_id = "strat-r5-c1-put"
        msg = {
            "op": "StartEngine",
            "request_id": request_id,
            "engine": "Live",
            "strategy_id": strategy_id,
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
            await DataEngineServer._handle_start_engine(s, msg)
            await asyncio.sleep(0)

        events = list(s._outbox._q)

        engine_stopped = [
            e for e in events
            if e.get("event") == "EngineStopped"
            and e.get("strategy_id") == strategy_id
        ]
        assert engine_stopped, (
            f"PUT failure must emit EngineStopped (Rust state machine unstuck); "
            f"outbox events: {[e.get('event') for e in events]!r}"
        )

        errors = [
            e for e in events
            if e.get("event") == "Error"
            and e.get("request_id") == request_id
            and e.get("code") == "kabu_register_failed"
        ]
        assert errors, (
            f"PUT failure must emit Error{{request_id, code='kabu_register_failed'}}; "
            f"outbox events: {events!r}"
        )

        assert "kabu_station" not in s._active_live_venues, (
            "PUT failure must discard live_venue from _active_live_venues"
        )
        assert strategy_id not in s._engine_tasks, (
            "PUT failure must pop strategy_id from _engine_tasks"
        )
        assert strategy_id not in s._engine_stop_events, (
            "PUT failure must pop strategy_id from _engine_stop_events"
        )

        # R7 MEDIUM-5: _live_state == CONNECTED + `_engine_stop_events.get()` is None
        # を明示 pin (early-abort cleanup の完全性担保)。
        assert s._live_state == LiveState.CONNECTED, (
            "PUT failure must leave _live_state == CONNECTED "
            "(early-abort cleanup completeness)"
        )
        assert s._engine_stop_events.get(strategy_id) is None, (
            "PUT failure must clear _engine_stop_events[strategy_id]"
        )

    async def test_kabu_register_failure_releases_active_live_venues_for_next_start_engine(
        self,
    ) -> None:
        """register fail 後、同 venue で次 StartEngine が
        `engine_busy_for_venue` で reject されないこと。"""
        from engine.server import DataEngineServer

        s = _make_server_for_start_engine()
        s._kabu_register_set.register = MagicMock(  # type: ignore[assignment]
            side_effect=RuntimeError("boom")
        )

        async def _fake_put(syms):
            return True

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        # 1 回目: register 失敗
        msg1 = {
            "op": "StartEngine",
            "request_id": "req-r5-c1-rel-1",
            "engine": "Live",
            "strategy_id": "strat-rel-1",
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
            await DataEngineServer._handle_start_engine(s, msg1)
            await asyncio.sleep(0)

        # _active_live_venues は空に戻っているはず
        assert "kabu_station" not in s._active_live_venues
        # R7 MEDIUM-5: 1 回目 early-abort 後の state machine + stop_events も
        # 完全リセットされていることを pin (2 回目が clean state から start するため)。
        assert s._live_state == LiveState.CONNECTED, (
            "first start (failed) must leave _live_state == CONNECTED"
        )
        assert s._engine_stop_events.get("strat-rel-1") is None, (
            "first start (failed) must clear _engine_stop_events for its strategy_id"
        )

        # 2 回目: 同 venue で別 strategy_id 起動を試みると EngineBusy で reject
        # されないこと
        # 2 回目は register/PUT 成功させる: register MagicMock を success に置換
        s._kabu_register_set = type(s._kabu_register_set)()  # fresh RegisterSet

        msg2 = {
            "op": "StartEngine",
            "request_id": "req-r5-c1-rel-2",
            "engine": "Live",
            "strategy_id": "strat-rel-2",
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        # 既存 outbox をクリアして 2 回目の event のみ観測
        s._outbox = _StubOutbox()

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
            await DataEngineServer._handle_start_engine(s, msg2)
            await asyncio.sleep(0)

        # EngineBusy が出ていない
        events = list(s._outbox._q)
        engine_busy = [
            e for e in events
            if e.get("event") == "EngineBusy"
            or (
                e.get("event") == "Error"
                and e.get("code") == "engine_busy_for_venue"
            )
        ]
        assert not engine_busy, (
            f"2nd StartEngine on the same venue (after 1st register failure) "
            f"must NOT be rejected by engine_busy_for_venue / EngineBusy guard; "
            f"got events: {events!r}"
        )

    # Note: live_venue_for_cleanup=None 経路は _kabu_register_early_abort 内 if ガード
    # (`if live_venue_for_cleanup is not None`) で no-op となり実害なし。冪等性は
    # 集合 .discard の性質で担保される (R7 LOW-3 review)。


# ---------------------------------------------------------------------------
# R5-CRITICAL-2 / R5 反映: kabu live data 経路 (TradingVolume delta).
# R3 M8 で qty=0 trade skip と決定したため、kabu live data 経路が完全 dead path
# 化していた。Option A (TradingVolume delta) で per-trade qty を計算する。
# ---------------------------------------------------------------------------


def _board_with_volume(volume: int, *, symbol: str = "9433") -> dict:
    """`_VALID_BOARD` をベースに `TradingVolume` 値だけ差し替えた kabu PUSH frame."""
    return {
        "Symbol": symbol,
        "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
        "CurrentPrice": 3000.0,
        "TradingVolume": volume,
        "Buy1": {"Price": 3000.0, "Qty": 100.0, "Time": "15:00:00", "Sign": "0101"},
        "Sell1": {"Price": 3001.0, "Qty": 50.0, "Time": "15:00:00", "Sign": "0101"},
    }


class TestKabuBoardPushTradingVolumeDelta:
    """R5-CRITICAL-2: per-trade qty は TradingVolume の delta で計算する。

    R3 M8 で「qty=Decimal('0') を skip」したため kabu PUSH の live data 経路が
    完全 dead 化していた (parse_execution は qty=0 ハードコードのため)。
    本ラウンドで:
    - `_kabu_last_trading_volume` state を server.py に追加し、ticker 別の
      last_volume を保持
    - delta_qty = current_volume - last_volume を計算 (delta_qty <= 0 は skip)
    - 初回 frame は state seed のみで skip (過去全累積を 1 件として流さない)
    - `_clear_kabu_session` で `_kabu_last_trading_volume.clear()`
    """

    def test_kabu_board_push_computes_qty_from_trading_volume_delta(self) -> None:
        """連続 PUSH frame の TradingVolume delta が trade qty として流れる。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )

        # 初回 frame: state seed のみ (skip)
        s._on_kabu_board_push(_board_with_volume(1000))
        assert s._live_fd_queue.empty(), (
            "first frame must be skipped (state seed only) "
            "to avoid pushing past cumulative volume as a single trade"
        )

        # 2 回目: TradingVolume = 1500 → delta = 500
        s._on_kabu_board_push(_board_with_volume(1500))

        assert not s._live_fd_queue.empty(), (
            "subsequent frame with positive delta must be pushed to _live_fd_queue"
        )
        trade = s._live_fd_queue.get_nowait()
        assert trade["qty"] == "500", (
            f"qty must be TradingVolume delta (1500-1000=500); got {trade['qty']!r}"
        )

    def test_kabu_board_push_first_frame_skipped_for_state_seed(self) -> None:
        """初回 frame は state seed として扱い `_live_fd_queue` には流さない。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )

        s._on_kabu_board_push(_board_with_volume(2500))

        assert s._live_fd_queue.empty(), (
            "first kabu PUSH frame must NOT enqueue a trade "
            "(seed last_trading_volume only)"
        )
        # state が seed されていることを assert
        assert s._kabu_last_trading_volume.get("9433") == 2500

    def test_kabu_board_push_zero_delta_skipped(self) -> None:
        """TradingVolume が変わらない 2 連続 frame では 2 件目を skip する。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )
        # 1 回目: seed (skip)
        s._on_kabu_board_push(_board_with_volume(1000))
        # 2 回目: 同じ volume (delta = 0)
        s._on_kabu_board_push(_board_with_volume(1000))

        assert s._live_fd_queue.empty(), (
            "zero-delta frame must NOT enqueue a trade (no execution occurred)"
        )

    def test_kabu_board_push_negative_delta_skipped(self) -> None:
        """TradingVolume が減少した場合 (再オープン後の reset 等) は skip する。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )
        # 1 回目: seed (skip)
        s._on_kabu_board_push(_board_with_volume(5000))
        # 2 回目: 減少 (delta = -3000)
        s._on_kabu_board_push(_board_with_volume(2000))

        assert s._live_fd_queue.empty(), (
            "negative-delta frame must NOT enqueue a trade (volume reset / anomaly)"
        )
        # state は新値で更新されることが望ましい (= 次の delta 計算の基準)
        assert s._kabu_last_trading_volume.get("9433") == 2000, (
            "negative delta should reseed state so next positive delta computes from new base"
        )

    def test_clear_kabu_session_resets_last_trading_volume(self) -> None:
        """`_clear_kabu_session` 後の最初 frame は再び state seed として扱う
        (古い state を引き継がない)。"""
        s = _make_server(
            live_state=LiveState.TRADING,
            connected_venue="kabu_station",
        )

        # `_clear_kabu_session` が触る他 attr を defensive に初期化
        s._kabu_venue = None
        s._kabu_fill_poller_task = None
        s._kabu_push_task = None

        # 1 回目: seed
        s._on_kabu_board_push(_board_with_volume(1000))
        # 2 回目: delta = 500 → push される
        s._on_kabu_board_push(_board_with_volume(1500))
        assert not s._live_fd_queue.empty()
        s._live_fd_queue.get_nowait()  # drain

        # session clear
        s._clear_kabu_session()
        assert "9433" not in s._kabu_last_trading_volume, (
            "_clear_kabu_session must clear _kabu_last_trading_volume "
            "to avoid carrying state across login sessions"
        )

        # 再ログイン後の最初 frame は seed (skip)
        # connected_venue は _clear_kabu_session で None になるので再設定
        s._connected_venue = "kabu_station"
        s._live_state = LiveState.TRADING
        s._on_kabu_board_push(_board_with_volume(2000))
        assert s._live_fd_queue.empty(), (
            "first frame after _clear_kabu_session must be seed-only (skip)"
        )


# ---------------------------------------------------------------------------
# R7 MEDIUM-1 / MEDIUM-2: 空 instrument_id / 空 register set の early-abort.
#
# - MEDIUM-1: `instrument_id` 空文字で kabu register が silent skip → PUT /register
#   payload に対象なしで TRADING 起動 → live data dead path 化していた。
#   `EngineError{code:"invalid_config"}` を emit して abort する。
# - MEDIUM-2: `_kabu_register_set.all_symbols()` が空のとき PUT /register が
#   silent skip され、live 起動が成立してしまうケースを検出可能にするため
#   warning ログを出す (早期 abort はしない)。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestKabuStartEngineEmptyInstrumentIdAborts:
    """R7 MEDIUM-1: `instrument_id` 空文字で kabu register/PUT が silent skip
    されないこと (live data dead path 化を防ぐ早期 abort)。"""

    async def test_kabu_start_engine_empty_instrument_id_aborts(self) -> None:
        """`instrument_id=""` で StartEngine を送ると:
        - `EngineError{code:"invalid_config"}` が emit される
        - `EngineStopped` が emit される (Rust state machine unstuck)
        - `_live_state == CONNECTED` を維持 (TRADING に進まない)
        - `_run` は呼ばれない (`asyncio.to_thread.await_count == 0`)
        """
        from engine.server import DataEngineServer

        s = _make_server_for_start_engine()

        async def _fake_put(syms):
            # 到達したら test 失敗のシグナル (空 instrument_id は PUT 到達前に abort)。
            raise AssertionError(
                "PUT /register must not be reached when instrument_id is empty"
            )

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        request_id = "req-r7-m1-empty-iid"
        strategy_id = "strat-r7-m1-empty-iid"
        msg = {
            "op": "StartEngine",
            "request_id": request_id,
            "engine": "Live",
            "strategy_id": strategy_id,
            "config": {
                # 空 instrument_id を渡す (pydantic validator か runtime guard の
                # いずれかで invalid_config に落ちる必要あり)。
                "instrument_id": "",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch(
            "asyncio.to_thread", new=AsyncMock(return_value=None)
        ) as _to_thread:
            await DataEngineServer._handle_start_engine(s, msg)
            await asyncio.sleep(0)

        events = list(s._outbox._q)

        # (1) `invalid_config` error が emit されている
        # pydantic validation の場合は Error{code:"invalid_config"} (main-thread emit) /
        # runtime guard の場合は EngineError{code:"invalid_config"} (kabu_register_early_abort)
        # のいずれかになりうる。`code == "invalid_config"` を持つ event が
        # 少なくとも 1 件存在することを assert する。
        invalid_configs = [
            e for e in events
            if e.get("code") == "invalid_config"
        ]
        assert invalid_configs, (
            f"empty instrument_id must emit Error / EngineError with "
            f"code='invalid_config'; got events: {events!r}"
        )

        # (2) `_run` 未呼出 = asyncio.to_thread が呼ばれていない
        assert _to_thread.await_count == 0, (
            f"empty instrument_id must skip _run (asyncio.to_thread not awaited); "
            f"got await_count={_to_thread.await_count}"
        )

        # (3) `_live_state == CONNECTED` を維持 (TRADING に進まない)
        assert s._live_state == LiveState.CONNECTED, (
            f"empty instrument_id must keep _live_state at CONNECTED "
            f"(not TRADING); got: {s._live_state!r}"
        )

        # (4) early-abort 経路が確実に走る場合 (runtime guard) は EngineStopped が
        # 出る。pydantic 経路だけで止まる場合は EngineStopped は出ない (engine が
        # 起動していないため Rust 側も pending state にならない)。両経路許容するが、
        # `invalid_config` event は必ず存在する (上の assert で担保)。


@pytest.mark.asyncio
class TestKabuStartEngineEmptyRegisterSetLogsWarning:
    """R7 MEDIUM-2: `_kabu_register_set.all_symbols()` が空のときの
    PUT /register skip 経路で warning を残し observability を確保する。"""

    async def test_kabu_start_engine_empty_register_set_logs_warning(
        self, caplog
    ) -> None:
        """register_set が空のとき PUT /register skip + warning ログを出す。
        early abort はしない (instrument_id がある場合 MEDIUM-1 fix で register
        は走るはず) - warning だけで silent failure を観測可能化する。"""
        from engine.server import DataEngineServer
        from engine.exchanges.kabusapi_register import (
            RegisterSet as KabuRegisterSet,
        )

        s = _make_server_for_start_engine()

        # `_kabu_register_set.register` を no-op に差し替え + RegisterSet を
        # 空のまま PUT 経路に進ませる (MEDIUM-2 観測対象)。
        async def _fake_put(syms):
            # ここに来た場合は warning 経路が走らない = bug
            raise AssertionError(
                "PUT /register must NOT be called when register_set is empty"
            )

        s._kabu_put_register = _fake_put  # type: ignore[assignment]

        # register は呼ばれるが、all_symbols が空のままになる fake RegisterSet
        # (register が no-op で実際の集合に何も足さないケース)。
        class _EmptyRegisterSet(KabuRegisterSet):
            def register(self, symbol: str, exchange: int) -> None:  # type: ignore[override]
                # 何もせず silent no-op (外部 clear / race 後を模擬)
                pass

            def all_symbols(self):  # type: ignore[override]
                return []

        s._kabu_register_set = _EmptyRegisterSet()

        request_id = "req-r7-m2-empty-regset"
        strategy_id = "strat-r7-m2-empty-regset"
        msg = {
            "op": "StartEngine",
            "request_id": request_id,
            "engine": "Live",
            "strategy_id": strategy_id,
            "config": {
                "instrument_id": "9433.KabuStation Stock",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "examples/test_strategy_daily.py",
            },
        }

        import logging
        caplog.set_level(logging.WARNING, logger="engine.server")

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_live",
            MagicMock(return_value=None),
        ), patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
            await DataEngineServer._handle_start_engine(s, msg)
            await asyncio.sleep(0)

        # warning が記録されていることを assert (文言は実装依存だが
        # 「no symbols」「PUT /register skipped」を含むべき)。
        warning_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "no symbols" in r.getMessage().lower()
        ]
        assert warning_records, (
            f"empty register_set must log a WARNING that PUT /register was skipped "
            f"(silent live data start observability); "
            f"caplog records: {[(r.levelname, r.getMessage()) for r in caplog.records]!r}"
        )
