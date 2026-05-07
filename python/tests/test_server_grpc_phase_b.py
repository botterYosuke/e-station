"""Phase B タスク (C2 / H7 / H14) の TDD テスト。

- C2: schema_minor 不一致時に warning ログが出るが接続継続
- H7: server_grpc.py が schemas.py から SCHEMA_MAJOR/SCHEMA_MINOR を import しないこと
- H14: MAX_CONNECTIONS 超過時に log.warning が呼ばれること
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import grpc
import pytest
from grpc import aio

from engine.proto import engine_pb2, engine_pb2_grpc
from engine.server_grpc import SCHEMA_MAJOR, SCHEMA_MINOR
from engine.server_grpc import MAX_CONNECTIONS, _GrpcDataEngineServicer


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def grpc_server_fixture():
    """テスト用 gRPC サーバーをランダムポートで起動する。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        from engine.server import DataEngineServer

        inner = DataEngineServer(
            port=0,
            token="test-token",
            cache_dir=Path(tmpdir),
            config_dir=Path(tmpdir),
        )
        srv = aio.server()
        servicer = _GrpcDataEngineServicer(inner)
        engine_pb2_grpc.add_DataEngineServicer_to_server(servicer, srv)
        port = srv.add_insecure_port("[::]:0")
        await srv.start()
        yield port, inner
        await srv.stop(grace=0)


async def _handshake(
    port: int,
    token: str = "test-token",
    schema_major: int | None = None,
    schema_minor: int | None = None,
    mode: int = engine_pb2.APP_MODE_LIVE,
) -> tuple:
    if schema_major is None:
        schema_major = SCHEMA_MAJOR
    if schema_minor is None:
        schema_minor = SCHEMA_MINOR

    channel = aio.insecure_channel(f"localhost:{port}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()
    await stream.write(
        engine_pb2.Command(
            hello=engine_pb2.HelloRequest(
                schema_major=schema_major,
                schema_minor=schema_minor,
                token=token,
                mode=mode,
            )
        )
    )
    event = await asyncio.wait_for(stream.read(), timeout=5.0)
    assert event.HasField("ready"), f"Expected ready, got {event}"
    return channel, stream


# ---------------------------------------------------------------------------
# C2: schema_minor 不一致時 warning ログ & 接続継続
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_c2_schema_minor_mismatch_logs_warning_and_continues(grpc_server_fixture, caplog):
    """schema_minor が server 側と異なる場合、warning ログが出るが接続は継続すること。"""
    port, _ = grpc_server_fixture

    mismatched_minor = SCHEMA_MINOR + 1

    with caplog.at_level(logging.WARNING, logger="engine.server_grpc"):
        channel, stream = await _handshake(port, schema_minor=mismatched_minor)

    # 接続が継続していること: ReadyResponse は既に受け取っている
    # さらに Ping を送って Pong が返れば接続継続を確認できる
    await stream.write(engine_pb2.Command(ping=engine_pb2.PingRequest(request_id="c2-ping")))
    received_pong = False
    for _ in range(5):
        try:
            ev = await asyncio.wait_for(stream.read(), timeout=2.0)
            if ev.HasField("pong"):
                received_pong = True
                break
        except asyncio.TimeoutError:
            break

    assert received_pong, "Connection should continue after schema_minor mismatch"

    # warning ログが出ていること
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("schema_minor mismatch" in m for m in warning_msgs), (
        f"Expected 'schema_minor mismatch' warning; got: {warning_msgs}"
    )

    stream.cancel()
    await channel.close()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_c2_schema_minor_match_no_warning(grpc_server_fixture, caplog):
    """schema_minor が一致する場合は warning ログが出ないこと。"""
    port, _ = grpc_server_fixture

    with caplog.at_level(logging.WARNING, logger="engine.server_grpc"):
        channel, stream = await _handshake(port, schema_minor=SCHEMA_MINOR)

    warning_msgs = [
        r.message
        for r in caplog.records
        if r.levelno == logging.WARNING and "schema_minor" in r.message
    ]
    assert len(warning_msgs) == 0, f"Unexpected schema_minor warning: {warning_msgs}"

    stream.cancel()
    await channel.close()


# ---------------------------------------------------------------------------
# H7: server_grpc.py が schemas.py から SCHEMA_MAJOR/SCHEMA_MINOR を import しないこと
# ---------------------------------------------------------------------------


def test_h7_server_grpc_does_not_import_schema_constants_from_schemas():
    """server_grpc モジュールが schemas.SCHEMA_MAJOR/SCHEMA_MINOR を import せずに定数を持つこと。"""
    import importlib
    import importlib.util

    # server_grpc のソースを読んでチェック
    spec = importlib.util.find_spec("engine.server_grpc")
    assert spec is not None
    source_path = spec.origin
    source = Path(source_path).read_text(encoding="utf-8")

    # 廃止予定 import が存在しないこと
    assert "from engine.schemas import SCHEMA_MAJOR" not in source, (
        "server_grpc.py must not import SCHEMA_MAJOR from engine.schemas"
    )
    assert "from engine.schemas import SCHEMA_MINOR" not in source, (
        "server_grpc.py must not import SCHEMA_MINOR from engine.schemas"
    )

    # モジュールが SCHEMA_MAJOR / SCHEMA_MINOR 定数を自前で持つこと
    import engine.server_grpc as sg

    assert hasattr(sg, "SCHEMA_MAJOR"), "engine.server_grpc must define SCHEMA_MAJOR"
    assert hasattr(sg, "SCHEMA_MINOR"), "engine.server_grpc must define SCHEMA_MINOR"
    assert isinstance(sg.SCHEMA_MAJOR, int)
    assert isinstance(sg.SCHEMA_MINOR, int)


# ---------------------------------------------------------------------------
# H14: MAX_CONNECTIONS 超過時に log.warning が呼ばれること
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_h14_max_connections_exceeded_logs_warning(grpc_server_fixture, caplog):
    """MAX_CONNECTIONS 超過時に log.warning が呼ばれること。"""
    port, _ = grpc_server_fixture

    channels = []
    streams = []

    try:
        # MAX_CONNECTIONS まで接続を確立する
        for _ in range(MAX_CONNECTIONS):
            ch, st = await _handshake(port)
            channels.append(ch)
            streams.append(st)

        # MAX_CONNECTIONS + 1 番目の接続を試みる
        extra_ch = aio.insecure_channel(f"localhost:{port}")
        extra_stub = engine_pb2_grpc.DataEngineStub(extra_ch)
        extra_stream = extra_stub.Session()

        with caplog.at_level(logging.WARNING, logger="engine.server_grpc"):
            await extra_stream.write(
                engine_pb2.Command(
                    hello=engine_pb2.HelloRequest(
                        schema_major=SCHEMA_MAJOR,
                        schema_minor=SCHEMA_MINOR,
                        token="test-token",
                        mode=engine_pb2.APP_MODE_LIVE,
                    )
                )
            )

            with pytest.raises(grpc.aio.AioRpcError) as exc_info:
                await asyncio.wait_for(extra_stream.read(), timeout=5.0)
            assert exc_info.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED

        # warning ログに MAX_CONNECTIONS 関連メッセージが出ていること
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "MAX_CONNECTIONS" in m or "max connections" in m.lower()
            for m in warning_msgs
        ), f"Expected MAX_CONNECTIONS warning; got: {warning_msgs}"

        await extra_ch.close()
    finally:
        for st in streams:
            try:
                st.cancel()
            except Exception:
                pass
        for ch in channels:
            try:
                await ch.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# R2-H2: server_grpc.SCHEMA_MAJOR/MINOR と schemas.SCHEMA_MAJOR/MINOR のドリフト検知
# ---------------------------------------------------------------------------


def test_r2m3_dict_to_proto_event_unknown_key_returns_none_and_logs_warning(caplog):
    """R2-M3: _dict_to_proto_event に proto に存在しないキーを渡したとき None が返り warning ログが出ること。

    ignore_unknown_fields=False のため、不明キーは ParseDict が例外を raise し
    _dict_to_proto_event が None を返して warning ログを出力することを確認する。
    """
    import logging

    from engine.server_grpc import _dict_to_proto_event

    event_dict = {
        "event": "Pong",
        "request_id": "test-pong",
        "unknown_field_that_does_not_exist_in_proto": "surprise",
    }

    with caplog.at_level(logging.WARNING, logger="engine.server_grpc"):
        result = _dict_to_proto_event(event_dict)

    assert result is None, "_dict_to_proto_event must return None for unknown fields"
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Pong" in m or "drop" in m.lower() or "Failed" in m for m in warning_msgs), (
        f"Expected warning about drop; got: {warning_msgs}"
    )


# ---------------------------------------------------------------------------
# R2-H2: server_grpc.SCHEMA_MAJOR/MINOR と schemas.SCHEMA_MAJOR/MINOR のドリフト検知
# ---------------------------------------------------------------------------


def test_r2h2_server_grpc_schema_constants_match_schemas():
    """server_grpc.SCHEMA_MAJOR/MINOR が schemas.SCHEMA_MAJOR/MINOR と一致すること。

    R2-H2: server_grpc.py が schemas.py の定数を import せず独自定数を持ちつつ、
    実際の値は一致していることを保証するドリフト検知テスト。
    """
    import engine.server_grpc as sg
    import engine.schemas as s

    assert sg.SCHEMA_MAJOR == s.SCHEMA_MAJOR, (
        f"server_grpc.SCHEMA_MAJOR={sg.SCHEMA_MAJOR} != schemas.SCHEMA_MAJOR={s.SCHEMA_MAJOR}"
    )
    assert sg.SCHEMA_MINOR == s.SCHEMA_MINOR, (
        f"server_grpc.SCHEMA_MINOR={sg.SCHEMA_MINOR} != schemas.SCHEMA_MINOR={s.SCHEMA_MINOR}"
    )
