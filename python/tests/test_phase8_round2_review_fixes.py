"""Phase 8 review-fix-loop ラウンド 2 — silent-failure / multi-client 防御テスト。

各項目は計画書末尾「ラウンド 2 反映 / Round 2 silent-failure 修正」に対応する。
RED → GREEN を pin する単体テスト + multi-client 統合テスト。
"""

from __future__ import annotations

import asyncio
import os
import queue
import socket
import threading
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import orjson
import pytest

from engine.replay_session import (
    BusyError,
    LiveSession,
    ReplaySession,
    _AttachClient,
    _ReplayStatus,
)
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# CRITICAL-R2-1: _login_attach の VenueError broadcast (request_id=None) 取りこぼし
# ---------------------------------------------------------------------------


class _MockEngineForBroadcastVenueError:
    """RequestVenueLogin を受けたら request_id=None の VenueError を返す mock。"""

    def __init__(self, port: int) -> None:
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._ready = threading.Event()

    async def _handler(self, ws):
        try:
            await ws.recv()  # Hello
        except Exception:
            return
        ready = {
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "test",
            "engine_session_id": "00000000-0000-0000-0000-000000000000",
            "capabilities": {},
        }
        await ws.send(orjson.dumps(ready).decode())
        try:
            async for raw in ws:
                msg = orjson.loads(raw)
                if msg.get("op") == "RequestVenueLogin":
                    # CRITICAL-R2-1: broadcast 経路シミュレーション — request_id=None
                    resp = {
                        "event": "VenueError",
                        "venue": "tachibana",
                        "request_id": None,
                        "code": "login_failed",
                        "message": "broadcast venue error",
                    }
                    await ws.send(orjson.dumps(resp).decode())
        except Exception:
            pass

    async def _run(self):
        import websockets
        self._server = await websockets.serve(
            self._handler, "127.0.0.1", self.port, compression=None
        )
        self._ready.set()
        try:
            await self._server.wait_closed()
        except Exception:
            pass

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=lambda: self._loop.run_until_complete(self._run()), daemon=True
        )
        self._thread.start()
        assert self._ready.wait(timeout=5.0)

    def stop(self):
        if self._server is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._server.close)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)


def test_login_attach_handles_broadcast_venue_error(monkeypatch):
    """CRITICAL-R2-1: request_id=None の VenueError でも _login_attach は raise する。

    RED: 旧実装は request_id 完全一致のみ捕捉していたため 30s ハング。
    GREEN: broadcast 経路 (request_id is None) も捕捉し速やかに raise する。
    """
    port = _free_port()
    server = _MockEngineForBroadcastVenueError(port)
    server.start()
    try:
        monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok")
        with LiveSession(
            venue="tachibana",
            demo=True,
            force_mode="attach",
            attach_endpoint=f"ws://127.0.0.1:{port}/",
            attach_timeout_s=2.0,
        ) as s:
            # 30s timeout より圧倒的に短時間で raise されることを保証する。
            import time
            t0 = time.monotonic()
            with pytest.raises(ConnectionError) as ei:
                s.login()
            elapsed = time.monotonic() - t0
            assert elapsed < 5.0, (
                f"_login_attach must catch broadcast VenueError quickly, "
                f"took {elapsed:.1f}s"
            )
            assert "login_failed" in str(ei.value) or "broadcast" in str(ei.value)
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# HIGH-R2-2: _do_request_venue_login の早期 reject は unicast
# ---------------------------------------------------------------------------


@pytest.fixture
async def replay_running_server(unused_tcp_port):
    """replay モードで起動した DataEngineServer を提供する。"""
    from engine.server import DataEngineServer

    token = "r2-multi-client-token"
    noop_startup = AsyncMock(return_value=None)
    with patch.object(DataEngineServer, "_startup_tachibana", noop_startup):
        server = DataEngineServer(port=unused_tcp_port, token=token)
        # mode is set via the first Hello (we send mode="replay" in handshake)
        task = asyncio.create_task(server.serve())
        # wait for port
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while True:
            try:
                _, w = await asyncio.open_connection("127.0.0.1", unused_tcp_port)
                w.close()
                await w.wait_closed()
                break
            except OSError:
                if loop.time() >= deadline:
                    raise TimeoutError
                await asyncio.sleep(0.02)
        yield unused_tcp_port, token, server
        server.shutdown()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


async def _connect_replay_client(port: int, token: str):
    import websockets
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/", compression=None)
    await ws.send(
        orjson.dumps(
            {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "test",
                "token": token,
                "mode": "replay",
            }
        ).decode()
    )
    ready = orjson.loads(await ws.recv())
    assert ready["event"] == "Ready", f"got {ready}"
    return ws


async def _drain_event(ws, event_name: str, timeout: float = 3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"{event_name} not received within {timeout}s")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = orjson.loads(raw)
        if msg.get("event") == event_name:
            return msg


async def _drain_event_or_none(ws, event_name: str, timeout: float = 0.5) -> dict | None:
    """short timeout: returns msg if received within ``timeout``, else None."""
    try:
        return await asyncio.wait_for(_drain_event(ws, event_name, timeout=timeout), timeout=timeout + 0.5)
    except (asyncio.TimeoutError, TimeoutError):
        return None


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_request_venue_login_mode_mismatch_is_unicast(replay_running_server):
    """HIGH-R2-2: replay mode reject の VenueError は要求した接続にだけ unicast。

    RED: 旧実装は broadcast (`_emit`) で VenueError を全 client に流していた。
    GREEN: send_to(ws, ...) で要求接続のみに unicast。他 client には届かない。
    """
    port, token, _server = replay_running_server
    ws_a = await _connect_replay_client(port, token)
    ws_b = await _connect_replay_client(port, token)

    try:
        # Drain ClientConnected events on both sides.
        # ws_b joined → ws_a should see ClientConnected count=2.
        await _drain_event(ws_a, "ClientConnected", timeout=3.0)
        await _drain_event(ws_a, "ClientConnected", timeout=3.0)
        await _drain_event(ws_b, "ClientConnected", timeout=3.0)

        rid = f"r2-uni-{uuid.uuid4().hex[:6]}"
        await ws_a.send(
            orjson.dumps(
                {
                    "op": "RequestVenueLogin",
                    "request_id": rid,
                    "venue": "tachibana",
                }
            ).decode()
        )

        # ws_a MUST receive VenueError with the same request_id.
        msg = await _drain_event(ws_a, "VenueError", timeout=3.0)
        assert msg.get("code") == "mode_mismatch"
        assert msg.get("request_id") == rid

        # ws_b MUST NOT receive any VenueError (drain should timeout).
        leaked = await _drain_event_or_none(ws_b, "VenueError", timeout=0.5)
        assert leaked is None, (
            f"VenueError leaked to other client (broadcast bug): {leaked!r}"
        )
    finally:
        await ws_a.close()
        await ws_b.close()


# ---------------------------------------------------------------------------
# HIGH-R2-3: _AttachClient.events()/wait_for() は他 request_id の Error を無視
# ---------------------------------------------------------------------------


def test_events_ignores_error_from_other_request_id():
    """HIGH-R2-3: `events()` は別 client/別 request_id の Error を無視して継続する。

    RED: 旧実装は受信した Error を即 raise していたため、別 client が起こした
    Error で run() が中断していた。
    GREEN: `_current_request_id` 一致 or broadcast (None) のみ raise。
    """
    client = _AttachClient.__new__(_AttachClient)
    # init queues without running real handshake
    client._recv_queue = queue.Queue()
    client._closed_event = threading.Event()
    client._current_request_id = "my-rid-001"

    # Inject events: another client's Error then EngineStopped
    client._recv_queue.put_nowait(
        {"event": "Error", "request_id": "other-rid", "code": "x", "message": "y"}
    )
    client._recv_queue.put_nowait({"event": "ReplayBuyingPower", "x": 1})
    client._recv_queue.put_nowait(
        {"event": "EngineStopped", "strategy_id": "s", "final_equity": "0", "ts_event_ms": 0}
    )

    received = list(client.events())
    kinds = [e.get("event") for e in received]
    assert "Error" not in kinds, f"Error from another request_id should be ignored: {kinds!r}"
    assert "ReplayBuyingPower" in kinds
    assert kinds[-1] == "EngineStopped"


def test_events_raises_on_own_request_id_error():
    """HIGH-R2-3: 自分の request_id の Error は即 raise する (旧挙動の保護)。"""
    client = _AttachClient.__new__(_AttachClient)
    client._recv_queue = queue.Queue()
    client._closed_event = threading.Event()
    client._current_request_id = "my-rid-002"

    client._recv_queue.put_nowait(
        {"event": "Error", "request_id": "my-rid-002", "code": "x", "message": "boom"}
    )

    with pytest.raises(ConnectionError, match="boom"):
        list(client.events())


def test_events_raises_on_broadcast_error():
    """HIGH-R2-3: request_id=None の broadcast Error は raise する (silent 防止)。"""
    client = _AttachClient.__new__(_AttachClient)
    client._recv_queue = queue.Queue()
    client._closed_event = threading.Event()
    client._current_request_id = "my-rid-003"

    client._recv_queue.put_nowait(
        {"event": "Error", "request_id": None, "code": "x", "message": "global"}
    )
    with pytest.raises(ConnectionError, match="global"):
        list(client.events())


def test_wait_for_ignores_other_request_id_error():
    """HIGH-R2-3: `wait_for()` も別 request_id の Error を無視して待機継続。"""
    client = _AttachClient.__new__(_AttachClient)
    client._recv_queue = queue.Queue()
    client._closed_event = threading.Event()
    client._current_request_id = "my-rid-004"

    client._recv_queue.put_nowait(
        {"event": "Error", "request_id": "other", "code": "x", "message": "ignored"}
    )
    client._recv_queue.put_nowait({"event": "ReplayDataLoaded", "n_bars": 10})

    msg = client.wait_for("ReplayDataLoaded", timeout_s=2.0)
    assert msg["n_bars"] == 10


# ---------------------------------------------------------------------------
# MEDIUM-R2-6: EngineBusy も request_id 不一致なら無視
# ---------------------------------------------------------------------------


def test_events_ignores_engine_busy_from_other_request_id():
    """MEDIUM-R2-6: 別 request_id の EngineBusy は無視して継続。"""
    client = _AttachClient.__new__(_AttachClient)
    client._recv_queue = queue.Queue()
    client._closed_event = threading.Event()
    client._current_request_id = "my-rid-005"

    client._recv_queue.put_nowait(
        {
            "event": "EngineBusy",
            "current_state": "RUNNING",
            "attempted_command": "LoadReplayData",
            "reason": "other client tried to load while running",
            "request_id": "other-rid",
        }
    )
    client._recv_queue.put_nowait(
        {"event": "EngineStopped", "strategy_id": "s", "final_equity": "0", "ts_event_ms": 0}
    )

    received = list(client.events())
    kinds = [e.get("event") for e in received]
    assert "EngineBusy" not in kinds
    assert kinds[-1] == "EngineStopped"


def test_events_raises_busy_on_own_engine_busy():
    """MEDIUM-R2-6: 自分宛の EngineBusy は BusyError を raise する。"""
    client = _AttachClient.__new__(_AttachClient)
    client._recv_queue = queue.Queue()
    client._closed_event = threading.Event()
    client._current_request_id = "my-rid-006"

    client._recv_queue.put_nowait(
        {
            "event": "EngineBusy",
            "current_state": "RUNNING",
            "attempted_command": "LoadReplayData",
            "reason": "wrong state",
            "request_id": "my-rid-006",
        }
    )

    with pytest.raises(BusyError):
        list(client.events())


def test_engine_busy_schema_accepts_request_id():
    """MEDIUM-R2-6: EngineBusy schema に request_id フィールドが追加されていること。"""
    from engine.schemas import EngineBusy
    e = EngineBusy(
        current_state="RUNNING",
        attempted_command="LoadReplayData",
        reason="r",
        request_id="rid-007",
    )
    assert e.request_id == "rid-007"
    # Optional — None でも通る (後方互換)
    e2 = EngineBusy(current_state="RUNNING", attempted_command="LoadReplayData", reason="r")
    assert e2.request_id is None


# ---------------------------------------------------------------------------
# HIGH-R2-4: 全接続が切れたとき replay/live state は IDLE / DISCONNECTED に戻る
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_full_disconnect_resets_replay_state(replay_running_server):
    """HIGH-R2-4: 1 client が LoadReplayData → 切断後、再接続で再 LoadReplayData できる。

    RED: 旧実装は全断後も _replay_state が LOADED のまま残り、再接続後に
    LoadReplayData を投げると EngineBusy で reject されていた。
    GREEN: 全断時に IDLE に reset し、再接続後の LoadReplayData が通る。
    """
    from pathlib import Path

    port, token, server = replay_running_server

    ws1 = await _connect_replay_client(port, token)
    try:
        await _drain_event(ws1, "ClientConnected", timeout=3.0)

        # ws1 が LoadReplayData → server _replay_state = LOADED に遷移
        # (実際にデータを load する代わりに、state を直接観察する)
        # サーバーの内部 state を直接いじって LOADED を再現
        from engine.server import ReplayState
        server._replay_state = ReplayState.LOADED
    finally:
        await ws1.close()

    # 全断が反映されるまで少し待つ (finally の cleanup は async)
    await asyncio.sleep(0.5)

    # HIGH-R2-4: _replay_state は IDLE に戻っている
    from engine.server import ReplayState, LiveState
    assert server._replay_state == ReplayState.IDLE, (
        f"全断後に _replay_state は IDLE に戻る必要がある: got {server._replay_state}"
    )
    assert server._live_state == LiveState.DISCONNECTED, (
        f"全断後に _live_state は DISCONNECTED に戻る必要がある: got {server._live_state}"
    )

    # 再接続して LoadReplayData (相当の state チェック) を試行
    ws2 = await _connect_replay_client(port, token)
    try:
        await _drain_event(ws2, "ClientConnected", timeout=3.0)
        # 直接 _check_replay_state を使った方が誤魔化しが減る
        ok = server._check_replay_state("LoadReplayData", ReplayState.IDLE, ws=None)
        assert ok, "再接続後の LoadReplayData は IDLE state guard を通る必要がある"
    finally:
        await ws2.close()


# ---------------------------------------------------------------------------
# MEDIUM-R2-5: narrative_hook thread fallback の例外伝播
# ---------------------------------------------------------------------------


def test_narrative_hook_thread_fallback_propagates_exception():
    """MEDIUM-R2-5: thread fallback 内で例外が出たら caller に propagate する。

    RED: 旧実装は `log.error` の後 silent return (drop) だった。
    GREEN: log.error 後に raise し、caller が drop を検知できる。
    """
    from engine.nautilus.narrative_hook import NarrativeHook

    # on_event が常に例外を出す → thread fallback も失敗する
    def _bad_on_event(evt: dict) -> None:
        raise ValueError("forced failure for R2-5 test")

    hook = NarrativeHook(strategy_id="t", on_event=_bad_on_event)

    # asyncio loop が走っている文脈をシミュレートするため、ループ内で呼ぶ。
    async def _call_inside_loop() -> bool:
        # asyncio.run のネスト不可エラーを誘発するため、一度 await を挟む。
        await asyncio.sleep(0)
        try:
            hook.on_order_filled_sync(
                {
                    "instrument_id": "1301.TSE",
                    "side": "BUY",
                    "price": "100",
                    "ts_event_ms": 0,
                }
            )
            return False  # 例外が出なかった = silent drop = 旧バグ
        except Exception:
            return True

    # 直接 async 文脈外から hook を呼んでも _emit_execution_marker が
    # ValueError を出すケースは _emit_execution_marker 内で握り潰される
    # (warning ログのみ)。そのため thread fallback パス自体は raise しない。
    # 代わりに直接 thread fallback パスで例外が伝播することを確認する。
    # シンプル化: on_order_filled_sync が直接 caller に raise することを
    # 確認する別経路 — concurrent.futures の result が timeout を吐くケース。

    import concurrent.futures
    import unittest.mock

    # asyncio.run(coro) を最初は RuntimeError で蹴り、
    # ThreadPoolExecutor.submit(...).result() を例外で蹴るように patch する。
    fake_executor = unittest.mock.MagicMock()
    fake_future = unittest.mock.MagicMock()
    fake_future.result.side_effect = TimeoutError("simulated thread fallback failure")
    fake_executor.__enter__ = lambda self: self
    fake_executor.__exit__ = lambda self, *a: False
    fake_executor.submit.return_value = fake_future

    real_run = asyncio.run

    def _fake_run(coro, *args, **kwargs):
        # 1度目の呼び出しで RuntimeError を投げて fallback パスへ誘導
        coro.close()
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    with patch("asyncio.run", _fake_run):
        with patch.object(
            concurrent.futures, "ThreadPoolExecutor", return_value=fake_executor
        ):
            with pytest.raises(TimeoutError, match="simulated thread fallback failure"):
                hook.on_order_filled_sync(
                    {
                        "instrument_id": "1301.TSE",
                        "side": "BUY",
                        "price": "100",
                        "ts_event_ms": 0,
                    }
                )


def test_narrative_hook_returns_true_on_success():
    """MEDIUM-R2-5: 成功時に True を返す (drop 検知用 bool 戻り値)。"""
    from engine.nautilus.narrative_hook import NarrativeHook

    received: list[dict] = []
    hook = NarrativeHook(strategy_id="t", on_event=received.append)
    rv = hook.on_order_filled_sync(
        {
            "instrument_id": "1301.TSE",
            "side": "BUY",
            "price": "100",
            "ts_event_ms": 0,
        }
    )
    assert rv is True
    assert len(received) == 1
    assert received[0]["event"] == "ExecutionMarker"


# ---------------------------------------------------------------------------
# MEDIUM-R2-7: 2回目 login の credential 変更検知
# ---------------------------------------------------------------------------


def test_second_login_with_changed_credentials_raises(monkeypatch):
    """MEDIUM-R2-7: ログイン済みで credential が変わると RuntimeError。

    RED: 旧実装は credential 変更を検知せず黙って no-op していた。
    GREEN: SHA-256 fingerprint で credential 変更を検知し RuntimeError。
    """
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    async def _fake_login(user_id, password, *, is_demo, p_no_counter, http_client=None):
        class _S:
            pass
        return _S()

    monkeypatch.setattr("engine.replay_session._tachibana_login_call", _fake_login)

    with LiveSession(venue="tachibana", demo=True, force_mode="inprocess") as s:
        s.login(user_id="user-A", password="pw-A")
        # 同じ credential での再 login は no-op (既存契約)
        s.login(user_id="user-A", password="pw-A")
        # 異なる credential での再 login は RuntimeError
        with pytest.raises(RuntimeError, match="credentials cannot change"):
            s.login(user_id="user-B", password="pw-B")


# ---------------------------------------------------------------------------
# MEDIUM-R2-8: EngineStopped 二重送出抑制
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_stopped_emitted_only_once_on_runner_failure(monkeypatch, tmp_path):
    """MEDIUM-R2-8: runner が EngineStopped 後に raise しても EngineStopped は 1 回だけ。

    RED: 旧実装は except 経路で無条件に補完送出していたため、runner が emit 済み
    でも 2 回 EngineStopped が流れていた。
    GREEN: engine_stopped_emitted フラグで 2 回目をスキップ。
    """
    from engine.server import DataEngineServer

    server = DataEngineServer.__new__(DataEngineServer)
    # 必要な最小限の attribute を初期化
    from collections import deque
    from engine.server import _Broadcaster, ReplayState, LiveState, StartupLatch
    server._mode = "replay"
    server._outbox = _Broadcaster()
    server._engine_tasks = {}
    server._engine_stop_events = {}
    server._replay_state = ReplayState.LOADED
    server._live_state = LiveState.DISCONNECTED
    server._replay_streaming_fills = []
    server._replay_strategy_id = ""
    server._replay_speed_multiplier = 1
    server._replay_portfolio = type("P", (), {"reset": lambda *a, **k: None, "on_fill": lambda *a, **k: None})()
    server._scheduled_callbacks = deque()
    server._tachibana_startup_latch = StartupLatch()
    import threading
    server._replay_snapshots = deque(maxlen=1000)
    server._replay_snapshots_lock = threading.Lock()
    server._notify_history_fn = None
    server._restore_strategy_holder = [None]

    # NautilusRunner を mock — start_backtest_replay_streaming の中で
    # EngineStopped を emit してから例外を投げる。
    class _MockRunner:
        def __init__(self):
            self.stopped = False
        def stop(self):
            self.stopped = True
        def start_backtest_replay_streaming(self, *, on_event, **kwargs):
            on_event({
                "event": "EngineStarted",
                "strategy_id": kwargs.get("strategy_id", "s"),
                "ts_event_ms": 0,
            })
            on_event({
                "event": "EngineStopped",
                "strategy_id": kwargs.get("strategy_id", "s"),
                "final_equity": "0",
                "ts_event_ms": 0,
            })
            raise RuntimeError("simulated post-stop failure")

    monkeypatch.setattr("engine.nautilus.engine_runner.NautilusRunner", _MockRunner)

    # 戦略ファイル (空でよい)
    strategy_file = tmp_path / "strat.py"
    strategy_file.write_text("# dummy\n")

    msg = {
        "request_id": "r2-8",
        "engine": "Backtest",
        "strategy_id": "test-s",
        "config": {
            "instrument_id": "1301.TSE",
            "start_date": "2025-01-06",
            "end_date": "2025-01-31",
            "initial_cash": "1000000",
            "granularity": "Daily",
            "strategy_file": str(strategy_file),
            "strategy_init_kwargs": None,
        },
    }
    await server._handle_start_engine(msg)

    # outbox の `_q` (compat deque) を観測して EngineStopped が 1 回だけ
    events = list(server._outbox._q)
    stopped_events = [e for e in events if e.get("event") == "EngineStopped"]
    assert len(stopped_events) == 1, (
        f"EngineStopped must be emitted exactly once; got {len(stopped_events)} "
        f"in events: {[e.get('event') for e in events]}"
    )
