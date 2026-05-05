"""Phase 8 review-fix-loop R1 / Phase 3 — replay_session.py 振る舞い + helper attach login.

各項目は計画書末尾「レビュー反映 (2026-05-04, ラウンド 1 - 新規) / Phase 3」に対応する。
RED → GREEN を pin する単体テスト集 (TDD)。
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from typing import Any
from unittest.mock import patch

import orjson
import pytest

from engine.replay_session import (
    LiveSession,
    ReplaySession,
    _AttachClient,
    _PNoCounter,
    _ReplayStatus,
    _TokenMismatchError,
    _read_session_file,
)
from engine.schemas import AUTH_FAILED_CODE, SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# Helpers — minimal mock WS server reused across tests
# ---------------------------------------------------------------------------


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _MockEngineServer:
    """A controllable mock engine WS server used to drive _AttachClient tests.

    Sends a customizable sequence of frames and records received commands.
    """

    def __init__(
        self,
        port: int,
        *,
        send_ready: bool = True,
        ready_then_frames: list[dict] | None = None,
        reject_with_error: dict | None = None,
        raise_before_ready: bool = False,
    ) -> None:
        self.port = port
        self.send_ready = send_ready
        self.ready_then_frames = ready_then_frames or []
        self.reject_with_error = reject_with_error
        self.raise_before_ready = raise_before_ready
        self.received: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._ready_event = threading.Event()

    async def _handler(self, ws):
        # Read Hello
        try:
            hello_raw = await ws.recv()
            hello = orjson.loads(hello_raw)
            self.received.append(hello)
        except Exception:
            return

        if self.raise_before_ready:
            await ws.close()
            return

        if self.reject_with_error is not None:
            await ws.send(orjson.dumps(self.reject_with_error).decode())
            await ws.close()
            return

        if self.send_ready:
            ready = {
                "event": "Ready",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "engine_version": "test",
                "engine_session_id": "00000000-0000-0000-0000-000000000000",
                "capabilities": {},
            }
            await ws.send(orjson.dumps(ready).decode())

        for frame in self.ready_then_frames:
            await ws.send(orjson.dumps(frame).decode())

        try:
            async for raw in ws:
                self.received.append(orjson.loads(raw))
        except Exception:
            pass

    async def _run(self):
        import websockets
        self._server = await websockets.serve(
            self._handler, "127.0.0.1", self.port, compression=None
        )
        self._ready_event.set()
        try:
            await self._server.wait_closed()
        except Exception:
            pass

    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=lambda: self._loop.run_until_complete(self._run()), daemon=True
        )
        self._thread.start()
        assert self._ready_event.wait(timeout=5.0)

    def stop(self) -> None:
        if self._server is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._server.close)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# C-GP1 / H-Type3: status Literal に "stopping" を含める
# ---------------------------------------------------------------------------


def test_status_returns_stopping_during_transition() -> None:
    """STOPPING 中に status は文字列 'stopping' を返す。

    これは type ignore 削除と Literal 拡張のリグレッションガード。
    """
    s = ReplaySession()
    s._status = _ReplayStatus.STOPPING  # type: ignore[assignment]
    assert s.status == "stopping"


def test_status_literal_includes_all_six_states() -> None:
    """Literal 戻り値型に "stopping" が含まれている (annotation pin)。"""
    import typing
    hints = typing.get_type_hints(ReplaySession.status.fget)
    ret = hints.get("return")
    args = typing.get_args(ret)
    assert "stopping" in args, f"'stopping' not in {args!r}"
    # All six states
    for expected in ("idle", "loaded", "running", "stopping", "stopped", "errored"):
        assert expected in args, f"{expected!r} missing from {args!r}"


# ---------------------------------------------------------------------------
# C-GP2: EngineStopped 受信順序 — on_event raise しても status は STOPPED
# ---------------------------------------------------------------------------


def test_engine_stopped_marks_status_before_on_event(monkeypatch) -> None:
    """on_event 内で EngineStopped 処理中に raise しても status は STOPPED のまま。"""
    s = ReplaySession()
    s._mode = "attach"
    s._status = _ReplayStatus.LOADED
    s._load_params = {
        "instrument_id": "1301.TSE",
        "start_date": "2025-01-06",
        "end_date": "2025-03-31",
        "granularity": "Daily",
    }

    class _FakeClient:
        def send_command(self, cmd: dict) -> None:
            pass

        def events(self):
            yield {"event": "ReplayBuyingPower", "strategy_id": "x",
                   "cash": "0", "buying_power": "0", "equity": "0",
                   "ts_event_ms": 0}
            yield {"event": "EngineStopped", "strategy_id": "x",
                   "final_equity": "0", "ts_event_ms": 0}

    s._client = _FakeClient()  # type: ignore[assignment]

    def _on_event(evt: dict) -> None:
        if evt.get("event") == "EngineStopped":
            raise RuntimeError("user callback bug")

    # write a temp file so run() doesn't FileNotFoundError
    import tempfile, pathlib
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("# dummy\n")
        path = f.name
    try:
        with pytest.raises(RuntimeError, match="user callback bug"):
            s.run(strategy_file=path, on_event=_on_event)
    finally:
        os.unlink(path)

    assert s._status is _ReplayStatus.STOPPED, (
        f"expected STOPPED, got {s._status!r} — on_event raise must not "
        "overwrite STOPPED with ERRORED"
    )


# ---------------------------------------------------------------------------
# WS-MED3 / H-GP1 (helper 側): AUTH_FAILED_CODE 完全一致判定
# ---------------------------------------------------------------------------


def test_auth_failed_code_only_for_exact_match():
    """code='auth_failed' のみが _TokenMismatchError、'authentication_error' は普通の ConnectionRefusedError。"""
    port = _free_port()
    server = _MockEngineServer(
        port,
        reject_with_error={"event": "Error", "code": "authentication_error",
                           "message": "auth fail"},
    )
    server.start()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 2.0)
        with pytest.raises(ConnectionRefusedError) as ei:
            client.handshake()
        # Must NOT be _TokenMismatchError because code != AUTH_FAILED_CODE
        assert not isinstance(ei.value, _TokenMismatchError), (
            "code='authentication_error' should NOT trigger _TokenMismatchError "
            "(only exact AUTH_FAILED_CODE match should)"
        )
    finally:
        server.stop()


def test_auth_failed_code_exact_match_raises_token_mismatch():
    port = _free_port()
    server = _MockEngineServer(
        port,
        reject_with_error={"event": "Error", "code": AUTH_FAILED_CODE,
                           "message": "auth fail"},
    )
    server.start()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 2.0)
        with pytest.raises(_TokenMismatchError):
            client.handshake()
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# H-GP2: handshake 失敗時の thread cleanup
# ---------------------------------------------------------------------------


def test_handshake_failure_joins_thread():
    """handshake 失敗後、内部 thread が 5s 以内に終了する。"""
    port = _free_port()
    server = _MockEngineServer(port, raise_before_ready=True)
    server.start()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 2.0)
        with pytest.raises(ConnectionRefusedError):
            client.handshake()
        # After handshake() returns (raised), thread must be reaped.
        if client._thread is not None:
            client._thread.join(timeout=0.5)
            assert not client._thread.is_alive(), (
                "attach thread did not terminate after handshake failure cleanup"
            )
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# H-GP4: env-only 経路で FLOWSURFACE_ENGINE_PORT を尊重する
# ---------------------------------------------------------------------------


def test_resolve_endpoint_respects_engine_port_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))  # no session file
    monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok-x")
    monkeypatch.setenv("FLOWSURFACE_ENGINE_PORT", "29876")
    s = ReplaySession()
    endpoint, token = s._resolve_endpoint_and_token()
    assert endpoint == "ws://127.0.0.1:29876/", endpoint
    assert token == "tok-x"


def test_resolve_endpoint_default_port_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok-y")
    monkeypatch.delenv("FLOWSURFACE_ENGINE_PORT", raising=False)
    s = ReplaySession()
    endpoint, _ = s._resolve_endpoint_and_token()
    assert endpoint == "ws://127.0.0.1:19876/"


# ---------------------------------------------------------------------------
# H-GP8: post-handshake 例外時の sticky error
# ---------------------------------------------------------------------------


def test_post_handshake_failure_sticks_closed_event():
    """handshake 後に切断 → _closed_event がセットされ events() は ConnectionError。"""
    port = _free_port()
    server = _MockEngineServer(port, ready_then_frames=[])
    server.start()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 2.0)
        client.handshake()
        # Stop the mock server to force a graceful close
        server.stop()
        # Wait until the recv_loop notices the close
        deadline = time.monotonic() + 3.0
        while not client._closed_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client._closed_event.is_set(), (
            "_closed_event must be set after post-handshake disconnect"
        )
        # events() should now immediately raise ConnectionError
        with pytest.raises(ConnectionError):
            for _ in client.events():
                pass
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# H-GP5: PNoCounter 二重生成バグの修正 — 同一 instance を再利用
# ---------------------------------------------------------------------------


def test_login_nested_loop_fallback_reuses_pno_counter(monkeypatch):
    """LiveSession.login() が nested-loop fallback 経由でも同じ p_no_counter を再利用する。

    bug: 旧実装は fallback 内で `_PNoCounter()` を新規生成しており、最初に
    生成した counter が捨てられていた。
    """
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    seen_counters: list[int] = []
    call_state = {"first": True}

    async def _fake_login(user_id, password, *, is_demo, p_no_counter, http_client=None):
        seen_counters.append(id(p_no_counter))

        class _S:
            pass

        return _S()

    monkeypatch.setattr("engine.replay_session._tachibana_login_call", _fake_login)

    real_run = asyncio.run

    def _fake_asyncio_run(coro):
        if call_state["first"]:
            call_state["first"] = False
            # Close the coroutine so it doesn't leak (no warnings)
            try:
                coro.close()
            except Exception:
                pass
            raise RuntimeError("asyncio.run() cannot be called from a running event loop")
        return real_run(coro)

    monkeypatch.setattr("engine.replay_session.asyncio.run", _fake_asyncio_run)

    user_id = f"user-{uuid.uuid4().hex[:8]}"
    password = f"pw-{uuid.uuid4().hex[:8]}"
    with LiveSession(venue="tachibana", demo=True, force_mode="inprocess") as s:
        s.login(user_id=user_id, password=password)

    # _fake_login was only called once (the fallback)
    assert len(seen_counters) == 1, seen_counters
    # That single call must use the originally-built counter, not a freshly
    # constructed one. We pin this by checking the call site by patching
    # _PNoCounter and counting constructions.


def test_login_pno_counter_constructed_once(monkeypatch):
    """fallback 経路でも _PNoCounter は 1 度しか構築されない。"""
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    construct_count = {"n": 0}
    real_pno = _PNoCounter

    def _wrapped() -> Any:
        construct_count["n"] += 1
        return real_pno()

    monkeypatch.setattr("engine.replay_session._PNoCounter", _wrapped)

    async def _fake_login(user_id, password, *, is_demo, p_no_counter, http_client=None):
        class _S:
            pass

        return _S()

    monkeypatch.setattr("engine.replay_session._tachibana_login_call", _fake_login)

    real_run = asyncio.run
    state = {"first": True}

    def _fake_asyncio_run(coro):
        if state["first"]:
            state["first"] = False
            try:
                coro.close()
            except Exception:
                pass
            raise RuntimeError("asyncio.run() cannot be called from a running event loop")
        return real_run(coro)

    monkeypatch.setattr("engine.replay_session.asyncio.run", _fake_asyncio_run)

    user_id = f"user-{uuid.uuid4().hex[:8]}"
    password = f"pw-{uuid.uuid4().hex[:8]}"
    with LiveSession(venue="tachibana", demo=True, force_mode="inprocess") as s:
        s.login(user_id=user_id, password=password)

    assert construct_count["n"] == 1, (
        f"_PNoCounter constructed {construct_count['n']} times — fallback "
        "must reuse the original counter (bug H-GP5)"
    )


# ---------------------------------------------------------------------------
# H-Silent4: narrative_hook on_order_filled_sync の drop fallback
# ---------------------------------------------------------------------------


def test_narrative_hook_sync_falls_back_when_loop_running():
    """既存 event loop 中から sync を呼んでも ExecutionMarker が emit される。"""
    from engine.nautilus.narrative_hook import NarrativeHook

    collected: list[dict] = []
    hook = NarrativeHook(strategy_id="s1", on_event=collected.append)

    event = {
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "100.0",
        "ts_event_ms": 1,
    }

    async def _runner():
        # Inside a running loop — the original asyncio.run() inside
        # on_order_filled_sync would raise RuntimeError. The fallback must
        # still emit the marker.
        hook.on_order_filled_sync(event)

    asyncio.run(_runner())
    assert len(collected) == 1, f"expected 1 marker, got {collected!r}"
    assert collected[0]["event"] == "ExecutionMarker"


# ---------------------------------------------------------------------------
# Silent-M3: events() / wait_for() で Error 即 raise
# ---------------------------------------------------------------------------


def test_wait_for_raises_immediately_on_error_event():
    """wait_for() 中に Error event が来たら即 raise — 60s hang しない。"""
    port = _free_port()
    server = _MockEngineServer(
        port,
        ready_then_frames=[
            {"event": "Error", "request_id": "r1", "code": "load_replay_data_failed",
             "message": "missing data"},
        ],
    )
    server.start()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 2.0)
        client.handshake()
        start = time.monotonic()
        with pytest.raises(ConnectionError):
            client.wait_for("ReplayDataLoaded", timeout_s=10.0)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"wait_for should raise quickly, took {elapsed:.2f}s"
    finally:
        server.stop()


def test_events_raises_immediately_on_error_event():
    port = _free_port()
    server = _MockEngineServer(
        port,
        ready_then_frames=[
            {"event": "Error", "request_id": None, "code": "engine_run_failed",
             "message": "boom"},
        ],
    )
    server.start()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 2.0)
        client.handshake()
        start = time.monotonic()
        with pytest.raises(ConnectionError):
            for _ in client.events():
                pass
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"events should raise quickly, took {elapsed:.2f}s"
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# M-GP3: wait_for で BusyError raise 後 pending を queue に戻さない
# ---------------------------------------------------------------------------


def test_wait_for_busy_does_not_requeue_pending():
    """EngineBusy で BusyError 投げる場合、保留 event は破棄する。"""
    from engine.replay_session import BusyError

    port = _free_port()
    server = _MockEngineServer(
        port,
        ready_then_frames=[
            {"event": "ReplayBuyingPower", "strategy_id": "s1", "cash": "0",
             "buying_power": "0", "equity": "0", "ts_event_ms": 0},
            {"event": "EngineBusy", "current_state": "RUNNING",
             "attempted_command": "LoadReplayData", "reason": "wrong state"},
        ],
    )
    server.start()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 2.0)
        client.handshake()
        with pytest.raises(BusyError):
            client.wait_for("ReplayDataLoaded", timeout_s=5.0)
        # No pending should be re-queued. The recv queue should be empty
        # (or contain only late frames from server, but ReplayBuyingPower
        # must NOT be re-yielded by events() now).
        # Drain quickly with a small timeout.
        seen: list[dict] = []
        for _ in range(20):
            try:
                seen.append(client._recv_queue.get_nowait())
            except queue.Empty:
                break
        # The pending ReplayBuyingPower must not have been put back.
        kinds = [m.get("event") for m in seen if isinstance(m, dict)]
        assert "ReplayBuyingPower" not in kinds, (
            f"pending ReplayBuyingPower was re-queued (M-GP3 violation): {kinds!r}"
        )
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# M-GP1: endpoint/token 解決順 — 4 件 pin
# ---------------------------------------------------------------------------


def _write_session_file(tmp_path, *, port: int, token: str, pid: int):
    sess = {
        "port": port,
        "token": token,
        "pid": pid,
        "schema_major": SCHEMA_MAJOR,
        "started_at": "2026-05-04T00:00:00Z",
    }
    (tmp_path / "engine-session.json").write_text(json.dumps(sess), encoding="utf-8")


def test_resolve_priority_a_explicit_argument_wins(monkeypatch, tmp_path):
    """(a) 明示 attach_endpoint + env が最優先。session ファイル無視。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    _write_session_file(tmp_path, port=29999, token="from-session", pid=os.getpid())
    monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "from-env")

    s = ReplaySession(attach_endpoint="ws://127.0.0.1:11111/")
    endpoint, token = s._resolve_endpoint_and_token()
    assert endpoint == "ws://127.0.0.1:11111/"
    assert token == "from-env", (
        "explicit attach_endpoint must use env token, not session-file token "
        "(prevents (a) ↔ (b) mixing)"
    )


def test_resolve_priority_b_session_file(monkeypatch, tmp_path):
    """(b) session ファイル — endpoint も token も session ファイルから。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    _write_session_file(tmp_path, port=22222, token="sess-token", pid=os.getpid())
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)

    s = ReplaySession()
    endpoint, token = s._resolve_endpoint_and_token()
    assert endpoint == "ws://127.0.0.1:22222/"
    assert token == "sess-token"


def test_resolve_priority_c_env_only(monkeypatch, tmp_path):
    """(c) env のみ — session ファイル無し / endpoint 引数無し。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "env-tok")
    monkeypatch.delenv("FLOWSURFACE_ENGINE_PORT", raising=False)
    s = ReplaySession()
    endpoint, token = s._resolve_endpoint_and_token()
    assert endpoint == "ws://127.0.0.1:19876/"
    assert token == "env-tok"


def test_resolve_priority_no_match_returns_none(monkeypatch, tmp_path):
    """全部欠けたら (None, None)。"""
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    monkeypatch.delenv("FLOWSURFACE_ENGINE_TOKEN", raising=False)
    s = ReplaySession()
    endpoint, token = s._resolve_endpoint_and_token()
    assert endpoint is None
    assert token is None


# ---------------------------------------------------------------------------
# M-GP2: pid フィールド欠損は invalid
# ---------------------------------------------------------------------------


def test_session_file_missing_pid_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    sess = {
        "port": 19876,
        "token": "tok",
        # pid omitted intentionally
        "schema_major": SCHEMA_MAJOR,
        "started_at": "2026-05-04T00:00:00Z",
    }
    (tmp_path / "engine-session.json").write_text(json.dumps(sess), encoding="utf-8")
    assert _read_session_file() is None


def test_session_file_null_pid_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    sess = {
        "port": 19876,
        "token": "tok",
        "pid": None,
        "schema_major": SCHEMA_MAJOR,
        "started_at": "2026-05-04T00:00:00Z",
    }
    (tmp_path / "engine-session.json").write_text(json.dumps(sess), encoding="utf-8")
    assert _read_session_file() is None


# ---------------------------------------------------------------------------
# M-GP5: LiveSession 2 回目 login() は no-op
# ---------------------------------------------------------------------------


def test_login_second_call_is_noop(monkeypatch):
    monkeypatch.delenv("DEV_TACHIBANA_USER_ID", raising=False)
    monkeypatch.delenv("DEV_TACHIBANA_PASSWORD", raising=False)

    call_count = {"n": 0}

    async def _fake_login(user_id, password, *, is_demo, p_no_counter, http_client=None):
        call_count["n"] += 1

        class _S:
            pass

        return _S()

    monkeypatch.setattr("engine.replay_session._tachibana_login_call", _fake_login)

    user_id = f"user-{uuid.uuid4().hex[:8]}"
    password = f"pw-{uuid.uuid4().hex[:8]}"
    with LiveSession(venue="tachibana", demo=True, force_mode="inprocess") as s:
        s.login(user_id=user_id, password=password)
        s.login(user_id=user_id, password=password)
        s.login()

    assert call_count["n"] == 1, (
        f"second login() must be no-op; got {call_count['n']} internal calls"
    )


# ---------------------------------------------------------------------------
# L-GP2: CLI --mode choices uses _ForceMode.__args__
# ---------------------------------------------------------------------------


def test_cli_mode_choices_match_force_mode_alias():
    """CLI argparse の choices が _ForceMode の Literal 値と一致する。

    L-GP2: ハードコードされた `["auto","inprocess","attach"]` ではなく
    `list(typing.get_args(_ForceMode))` または `list(_ForceMode.__args__)`
    を使うことで、_ForceMode の追加・削除に CLI が自動追従する。
    """
    import re
    import typing
    from engine.replay_session import _ForceMode

    expected = set(typing.get_args(_ForceMode))
    assert expected == {"auto", "inprocess", "attach"}, expected

    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "engine",
        "replay_session.py",
    )
    with open(src_path, encoding="utf-8") as f:
        text = f.read()
    # Look for derived choices (one of the supported forms).
    pattern = r"choices=list\(\s*(?:[a-zA-Z_][\w.]*\.get_args\(\s*_ForceMode\s*\)|_ForceMode\.__args__)\s*\)"
    assert re.search(pattern, text), (
        "CLI --mode choices must be derived from _ForceMode (DRY pin); "
        f"_ForceMode args = {sorted(expected)}"
    )
