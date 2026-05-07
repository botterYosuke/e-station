"""Phase 8 review-fix-loop R1 / Phase 2 — server.py 振る舞い RED tests.

Covers:
- WS-MED1: handshake() タイムアウト
- WS-MED2 + L-WS-INFO: auth_failed / schema_mismatch reject の close code 統一 (1008)
- WS-MED3 / H-GP1: AUTH_FAILED_CODE 定数共有
- Silent-M1: EngineStopped 補完送出 (started_marker 無視)
- Silent-M2: orjson decode エラーで接続切断しない
- M-GP8: EngineBusy を当該接続のみに送る
- H-GP7: send_loop shutdown ドレイン
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def running_server():
    from engine.server import DataEngineServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    token = "phase2-review-token"
    mock_worker = MagicMock()
    mock_worker.prepare = AsyncMock(return_value=None)
    mock_worker.capabilities = MagicMock(return_value={})

    patches = [
        patch("engine.server.BinanceWorker", return_value=mock_worker),
        patch("engine.server.BybitWorker", return_value=mock_worker),
        patch("engine.server.HyperliquidWorker", return_value=mock_worker),
        patch("engine.server.MexcWorker", return_value=mock_worker),
        patch("engine.server.OkexWorker", return_value=mock_worker),
        patch.object(DataEngineServer, "_startup_tachibana", AsyncMock(return_value=None)),
    ]
    for p in patches:
        p.start()

    server = DataEngineServer(port=port, token=token)
    task = asyncio.create_task(server.serve())
    # Wait for port
    for _ in range(50):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            await w.wait_closed()
            break
        except OSError:
            await asyncio.sleep(0.02)

    yield port, token, server

    server.shutdown()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    for p in patches:
        p.stop()


async def _connect_replay(port: int, token: str) -> websockets.ClientConnection:
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/", compression=None)
    await ws.send(
        orjson.dumps(
            {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "phase2-review",
                "token": token,
                "mode": "replay",
            }
        ).decode()
    )
    ready = orjson.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
    assert ready["event"] == "Ready"
    # Drain ClientConnected broadcast
    cc = orjson.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
    assert cc["event"] == "ClientConnected"
    return ws


# ── WS-MED3 / H-GP1: AUTH_FAILED_CODE constant import-pin ─────────────────────


def test_auth_failed_code_is_imported_from_schemas():
    """server.py must reference engine.schemas.AUTH_FAILED_CODE (no hardcode)."""
    import engine.server as server_mod
    from engine.schemas import AUTH_FAILED_CODE

    assert AUTH_FAILED_CODE == "auth_failed"
    # Source-level pin: the literal "auth_failed" must NOT appear in server.py
    # outside the import statement (defensive against regression that re-introduces
    # the hardcoded literal).
    import inspect
    src = inspect.getsource(server_mod)
    # Find non-import occurrences of bare "auth_failed" string literal.
    # Allow it inside the import line and inside test/log identifiers (not present).
    lines = src.splitlines()
    offending = []
    for i, line in enumerate(lines, 1):
        if '"auth_failed"' in line and "import" not in line and "AUTH_FAILED_CODE" not in line:
            offending.append((i, line.strip()))
    assert not offending, (
        f"server.py still hardcodes \"auth_failed\" string literal: {offending}.\n"
        "Use AUTH_FAILED_CODE from engine.schemas instead."
    )


# ── WS-MED1: handshake timeout ────────────────────────────────────────────────


@pytest.mark.skip(reason="WS transport removed in G3 — _HANDSHAKE_TIMEOUT_S deleted with WS serve()")
@pytest.mark.asyncio
async def test_handshake_timeout_closes_connection(monkeypatch):
    """If client does not send Hello within the timeout, server closes the WS."""
    import engine.server as server_mod

    # Shorten the timeout for the test.
    monkeypatch.setattr(server_mod, "_HANDSHAKE_TIMEOUT_S", 0.5, raising=True)

    from engine.server import DataEngineServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    mock_worker = MagicMock()
    mock_worker.prepare = AsyncMock(return_value=None)
    mock_worker.capabilities = MagicMock(return_value={})
    patches = [
        patch("engine.server.BinanceWorker", return_value=mock_worker),
        patch("engine.server.BybitWorker", return_value=mock_worker),
        patch("engine.server.HyperliquidWorker", return_value=mock_worker),
        patch("engine.server.MexcWorker", return_value=mock_worker),
        patch("engine.server.OkexWorker", return_value=mock_worker),
        patch.object(DataEngineServer, "_startup_tachibana", AsyncMock(return_value=None)),
    ]
    for p in patches:
        p.start()
    try:
        server = DataEngineServer(port=port, token="ht-token")
        srv_task = asyncio.create_task(server.serve())
        # Wait for listen socket
        for _ in range(50):
            try:
                r, w = await asyncio.open_connection("127.0.0.1", port)
                w.close()
                await w.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.02)

        ws = await websockets.connect(f"ws://127.0.0.1:{port}/", compression=None)
        # Do NOT send Hello — wait for the server to close us out within ~0.5s.
        with pytest.raises(websockets.exceptions.ConnectionClosed) as excinfo:
            await asyncio.wait_for(ws.recv(), timeout=3.0)

        # The close should be 1002 (protocol error / handshake timeout).
        assert excinfo.value.code == 1002, f"expected close code 1002, got {excinfo.value.code}"

        server.shutdown()
        srv_task.cancel()
        try:
            await srv_task
        except (asyncio.CancelledError, Exception):
            pass
    finally:
        for p in patches:
            p.stop()


# ── WS-MED2: auth_failed close code is 1008 ───────────────────────────────────


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_auth_failed_uses_close_code_1008(running_server):
    port, _token, _server = running_server

    ws = await websockets.connect(f"ws://127.0.0.1:{port}/", compression=None)
    await ws.send(
        orjson.dumps(
            {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "auth-test",
                "token": "WRONG-TOKEN",
                "mode": "replay",
            }
        ).decode()
    )

    # First we expect EngineError, then close.
    with pytest.raises(websockets.exceptions.ConnectionClosed) as excinfo:
        # Drain frames until close
        while True:
            await asyncio.wait_for(ws.recv(), timeout=2.0)

    assert excinfo.value.code == 1008, f"expected 1008, got {excinfo.value.code}"


# ── L-WS-INFO: schema_mismatch close code is 1008 ─────────────────────────────


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_schema_mismatch_uses_close_code_1008(running_server):
    port, token, _server = running_server

    ws = await websockets.connect(f"ws://127.0.0.1:{port}/", compression=None)
    await ws.send(
        orjson.dumps(
            {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR + 99,  # mismatch
                "schema_minor": SCHEMA_MINOR,
                "client_version": "schema-test",
                "token": token,
                "mode": "replay",
            }
        ).decode()
    )

    with pytest.raises(websockets.exceptions.ConnectionClosed) as excinfo:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=2.0)

    assert excinfo.value.code == 1008, f"expected 1008, got {excinfo.value.code}"


# ── Silent-M2: malformed JSON does not drop the connection ────────────────────


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_malformed_json_keeps_connection_alive(running_server):
    port, token, _server = running_server

    ws = await _connect_replay(port, token)
    try:
        # Send malformed JSON.
        await ws.send(b"this-is-not-json{{{")

        # Expect Error event, NOT a close.
        msg = orjson.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
        assert msg.get("event") == "Error", f"unexpected msg: {msg!r}"
        assert msg.get("code") == "malformed_json"

        # Connection should still be alive — send a benign valid op.
        await ws.send(orjson.dumps({"op": "Ping", "request_id": "post-bad"}).decode())
        msg2 = orjson.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
        assert msg2.get("event") == "Pong", f"unexpected: {msg2!r}"
    finally:
        await ws.close()


# ── Silent-M1: EngineStopped is always emitted on error path ──────────────────


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_engine_stopped_emitted_when_runner_raises_before_started(running_server):
    """If the NautilusRunner raises BEFORE EngineStarted is emitted, the server
    must still broadcast EngineStopped so the Rust state machine does not stall."""
    port, token, server = running_server

    ws = await _connect_replay(port, token)
    try:
        # Move the replay state to LOADED so StartEngine will be accepted.
        from engine.server import ReplayState
        server._replay_state = ReplayState.LOADED

        # Patch NautilusRunner so .start_backtest_replay_streaming raises immediately.
        from unittest.mock import MagicMock
        broken_runner = MagicMock()
        broken_runner.start_backtest_replay_streaming = MagicMock(
            side_effect=RuntimeError("boom-before-started")
        )
        broken_runner.start_backtest_replay = MagicMock(
            side_effect=RuntimeError("boom-before-started")
        )
        broken_runner.stop = MagicMock(return_value=None)

        with patch("engine.nautilus.engine_runner.NautilusRunner", return_value=broken_runner):
            await ws.send(
                orjson.dumps(
                    {
                        "op": "StartEngine",
                        "request_id": "se-error-before-start",
                        "engine": "Backtest",
                        "strategy_id": "sid-error-1",
                        "config": {
                            "instrument_id": "1301.TSE",
                            "start_date": "2025-01-06",
                            "end_date": "2025-01-07",
                            "initial_cash": "1000000",
                            "granularity": "Daily",
                            "strategy_file": "examples/test_strategy_daily.py",
                        },
                    }
                ).decode()
            )

            # Drain events until we either see EngineStopped or time out.
            saw_stopped = False
            saw_error = False
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                msg = orjson.loads(raw)
                ev = msg.get("event")
                if ev == "EngineStopped" and msg.get("strategy_id") == "sid-error-1":
                    saw_stopped = True
                if ev == "Error" and msg.get("code") == "engine_run_failed":
                    saw_error = True
                if saw_stopped and saw_error:
                    break

            assert saw_stopped, "EngineStopped must be emitted even when runner raises before EngineStarted"
            assert saw_error, "Error{request_id} must still be emitted"
    finally:
        await ws.close()


# ── M-GP8: EngineBusy is sent only to the offending connection ────────────────


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_engine_busy_is_unicast_to_offending_connection(running_server):
    port, token, _server = running_server

    ws_a = await _connect_replay(port, token)
    # Drain ClientConnected on A from B's join (will be sent below).
    ws_b = await _connect_replay(port, token)
    # Now both sockets see ClientConnected count=2 events.
    # Drain pending lifecycle frames on both.
    async def drain_lifecycle(ws):
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
                m = orjson.loads(raw)
                if m.get("event") not in ("ClientConnected", "ClientDisconnected"):
                    # Push back not possible — but for our test we don't expect non-lifecycle here.
                    return m
        except asyncio.TimeoutError:
            return None

    await drain_lifecycle(ws_a)
    await drain_lifecycle(ws_b)

    try:
        # A sends a SetReplaySpeed while replay state is IDLE → EngineBusy
        await ws_a.send(
            orjson.dumps(
                {"op": "SetReplaySpeed", "request_id": "rs-1", "multiplier": 2}
            ).decode()
        )

        # A should receive EngineBusy.
        a_busy = None
        try:
            for _ in range(10):
                raw = await asyncio.wait_for(ws_a.recv(), timeout=2.0)
                m = orjson.loads(raw)
                if m.get("event") == "EngineBusy":
                    a_busy = m
                    break
        except asyncio.TimeoutError:
            pass
        assert a_busy is not None, "client A must receive EngineBusy"
        assert a_busy["attempted_command"] == "SetReplaySpeed"

        # B should NOT receive EngineBusy within a small window.
        b_got_busy = False
        try:
            while True:
                raw = await asyncio.wait_for(ws_b.recv(), timeout=0.5)
                m = orjson.loads(raw)
                if m.get("event") == "EngineBusy":
                    b_got_busy = True
                    break
        except asyncio.TimeoutError:
            pass
        assert not b_got_busy, "client B must NOT receive EngineBusy (unicast)"
    finally:
        await ws_a.close()
        await ws_b.close()


# ── H-GP7: send_loop drains queue on shutdown ─────────────────────────────────


@pytest.mark.skip(reason="WS transport removed in G3")
@pytest.mark.asyncio
async def test_send_loop_drains_pending_events_on_shutdown(running_server):
    """An event appended just before shutdown must reach the client before close."""
    port, token, server = running_server

    ws = await _connect_replay(port, token)
    try:
        # Append a sentinel event then immediately shutdown.
        sentinel = {"event": "EngineStopped", "strategy_id": "drain-test", "final_equity": "0", "ts_event_ms": 0}
        server._outbox.append(sentinel)
        server.shutdown()

        # We must receive the sentinel before the connection closes.
        saw = False
        try:
            for _ in range(20):
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                m = orjson.loads(raw)
                if m.get("event") == "EngineStopped" and m.get("strategy_id") == "drain-test":
                    saw = True
                    break
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass
        assert saw, "send_loop must drain pending events before exiting on shutdown"
    finally:
        try:
            await ws.close()
        except Exception:
            pass
