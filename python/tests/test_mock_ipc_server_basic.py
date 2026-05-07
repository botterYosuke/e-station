"""MockIPCServer S1 basic test.

完了条件: 1 秒以内に PASS、`stop()` 冪等性、Hello → Ready ハンドシェイク。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR
from tests.fixtures.mock_ipc_server import MockIPCServer


@pytest.mark.asyncio
async def test_mock_ipc_server_hello_ready_within_1s() -> None:
    """Hello → Ready ハンドシェイクが 1 秒以内に往復することを確認する。"""
    start = time.monotonic()
    with MockIPCServer() as srv:
        async with websockets.connect(srv.url, compression=None) as ws:
            hello = {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "test",
                "token": srv.token,
                "mode": "replay",
            }
            await ws.send(json.dumps(hello))
            raw = await asyncio.wait_for(ws.recv(), timeout=0.8)
            msg = json.loads(raw)
            assert msg["event"] == "Ready"
            assert msg["schema_major"] == SCHEMA_MAJOR
            assert msg["schema_minor"] == SCHEMA_MINOR
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"handshake took {elapsed:.3f}s (>= 1s budget)"


@pytest.mark.asyncio
async def test_mock_ipc_server_received_records_inbound_messages() -> None:
    with MockIPCServer() as srv:
        async with websockets.connect(srv.url, compression=None) as ws:
            await ws.send(json.dumps({"op": "Hello", "schema_major": SCHEMA_MAJOR}))
            await asyncio.wait_for(ws.recv(), timeout=0.8)
        # 接続クローズ後でも received は残る
        assert len(srv.received) >= 1
        assert srv.received[0]["op"] == "Hello"


def test_mock_ipc_server_stop_is_idempotent() -> None:
    """stop() を複数回呼んでも例外にならない（冪等）。"""
    srv = MockIPCServer()
    srv.start()
    srv.stop()
    srv.stop()  # 2 回目もエラーなし
    srv.stop()


def test_mock_ipc_server_random_port() -> None:
    """ランダムポート割り当て (port=0) が機能していること。"""
    with MockIPCServer() as a, MockIPCServer() as b:
        assert a.port != b.port
        assert a.port > 0 and b.port > 0


@pytest.mark.asyncio
async def test_mock_ipc_server_uses_compression_none() -> None:
    """RSV1 バグ回避のため、サーバ・クライアント双方で compression=None。

    websockets ライブラリがデフォルトの permessage-deflate を有効にすると
    Sec-WebSocket-Extensions ヘッダにそれが現れる。compression=None なら現れない。
    """
    with MockIPCServer() as srv:
        async with websockets.connect(srv.url, compression=None) as ws:
            # Subprotocol/extension ハンドシェイクの結果を確認
            # websockets の API: response_headers 経由で確認
            ext_header = ws.response.headers.get("Sec-WebSocket-Extensions", "")
            assert "permessage-deflate" not in ext_header, (
                f"server must disable compression (got: {ext_header!r})"
            )
