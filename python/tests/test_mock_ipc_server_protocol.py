"""MockIPCServer S2 protocol coverage.

Hello / Subscribe / FetchKlines / Unsubscribe / Shutdown / SCHEMA_MAJOR_MISMATCH
を script 形式で網羅する。
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR
from tests.fixtures.mock_ipc_server import MockIPCServer


def _hello_payload() -> dict:
    return {
        "op": "Hello",
        "schema_major": SCHEMA_MAJOR,
        "schema_minor": SCHEMA_MINOR,
        "client_version": "test",
        "token": "test-token",
        "mode": "replay",
    }


@pytest.mark.asyncio
async def test_subscribe_returns_depth_snapshot() -> None:
    script = [
        {
            "on": "Hello",
            "reply": [
                {
                    "event": "Ready",
                    "schema_major": SCHEMA_MAJOR,
                    "schema_minor": SCHEMA_MINOR,
                    "engine_version": "mock",
                    "engine_session_id": "sess-mock",
                    "capabilities": {},
                }
            ],
        },
        {
            "on": "Subscribe",
            "reply": [
                {
                    "event": "DepthSnapshot",
                    "request_id": None,
                    "venue": "binance",
                    "ticker": "BTCUSDT",
                    "market": "spot",
                    "stream_session_id": "sess-1",
                    "sequence_id": 100,
                    "bids": [{"price": "30000", "qty": "1.0"}],
                    "asks": [{"price": "30001", "qty": "0.5"}],
                }
            ],
        },
    ]
    with MockIPCServer(script) as srv:
        async with websockets.connect(srv.url, compression=None) as ws:
            await ws.send(json.dumps(_hello_payload()))
            ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.8))
            assert ready["event"] == "Ready"

            await ws.send(
                json.dumps(
                    {
                        "op": "Subscribe",
                        "venue": "binance",
                        "ticker": "BTCUSDT",
                        "market": "spot",
                    }
                )
            )
            snap = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.8))
            assert snap["event"] == "DepthSnapshot"
            assert snap["sequence_id"] == 100


@pytest.mark.asyncio
async def test_fetch_klines_returns_klines() -> None:
    script = [
        {"on": "Hello", "reply": [
            {
                "event": "Ready",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "engine_version": "mock",
                "engine_session_id": "s",
                "capabilities": {},
            }
        ]},
        {"on": "FetchKlines", "reply": [
            {
                "event": "Klines",
                "request_id": "req-1",
                "venue": "binance",
                "ticker": "BTCUSDT",
                "timeframe": "1m",
                "klines": [],
            }
        ]},
    ]
    with MockIPCServer(script) as srv:
        async with websockets.connect(srv.url, compression=None) as ws:
            await ws.send(json.dumps(_hello_payload()))
            await asyncio.wait_for(ws.recv(), timeout=0.8)
            await ws.send(json.dumps({"op": "FetchKlines", "request_id": "req-1"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.8))
            assert msg["event"] == "Klines"
            assert msg["request_id"] == "req-1"


@pytest.mark.asyncio
async def test_schema_major_mismatch_emits_engine_error() -> None:
    """SCHEMA_MAJOR 不一致を `EngineError` で表現する（HelloReject ではない）。"""
    script = [
        {"on": "Hello", "reply": [
            {
                "event": "EngineError",
                "code": "schema_mismatch",
                "message": "SCHEMA_MAJOR_MISMATCH",
            }
        ]},
    ]
    with MockIPCServer(script) as srv:
        async with websockets.connect(srv.url, compression=None) as ws:
            await ws.send(json.dumps(_hello_payload()))
            err = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.8))
            assert err["event"] == "EngineError"
            assert err["code"] == "schema_mismatch"


@pytest.mark.asyncio
async def test_unsubscribe_and_shutdown_recorded() -> None:
    """Unsubscribe / Shutdown も script でハンドル可能。"""
    script = [
        {"on": "Hello", "reply": [
            {
                "event": "Ready",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "engine_version": "mock",
                "engine_session_id": "s",
                "capabilities": {},
            }
        ]},
        {"on": "Unsubscribe", "reply": []},
        {"on": "Shutdown", "reply": []},
    ]
    with MockIPCServer(script) as srv:
        async with websockets.connect(srv.url, compression=None) as ws:
            await ws.send(json.dumps(_hello_payload()))
            await asyncio.wait_for(ws.recv(), timeout=0.8)
            await ws.send(json.dumps({"op": "Unsubscribe"}))
            await ws.send(json.dumps({"op": "Shutdown"}))
            # サーバ側は reply 無し。少し待って received を確認する。
            await asyncio.sleep(0.05)
        # close 後に received を確認
        ops = [m.get("op") for m in srv.received]
        assert ops == ["Hello", "Unsubscribe", "Shutdown"]
