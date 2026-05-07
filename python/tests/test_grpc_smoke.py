"""G1 smoke test: server_grpc.py の起動・ハンドシェイク・Ping/Pong を検証する。"""
import pytest
import asyncio
import grpc
from grpc import aio
from engine.proto import engine_pb2, engine_pb2_grpc
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR
from engine.server_grpc import GrpcDataEngineServer
from pathlib import Path
import tempfile


@pytest.fixture
async def grpc_server():
    """テスト用 GrpcDataEngineServer をランダムポートで起動するフィクスチャ。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        srv = GrpcDataEngineServer(
            port=0,  # ランダムポート
            token="test-token",
            cache_dir=Path(tmpdir),
            config_dir=Path(tmpdir),
        )
        # サーバーを内部的に起動
        grpc_server_instance = aio.server()
        from engine.server_grpc import _GrpcDataEngineServicer
        from engine.proto import engine_pb2_grpc as pb2_grpc
        servicer = _GrpcDataEngineServicer(srv._inner)
        pb2_grpc.add_DataEngineServicer_to_server(servicer, grpc_server_instance)
        port = grpc_server_instance.add_insecure_port("[::]:0")
        await grpc_server_instance.start()
        yield port
        await grpc_server_instance.stop(grace=0)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_handshake_returns_ready(grpc_server):
    """HelloRequest に対して ReadyResponse が返ること。"""
    channel = aio.insecure_channel(f"localhost:{grpc_server}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()

    await stream.write(engine_pb2.Command(
        hello=engine_pb2.HelloRequest(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            token="test-token",
            mode=engine_pb2.APP_MODE_LIVE,
        )
    ))

    event = await stream.read()
    assert event != grpc.aio.EOF
    assert event.HasField("ready")
    assert event.ready.schema_major == SCHEMA_MAJOR
    assert event.ready.schema_minor == SCHEMA_MINOR

    stream.cancel()
    await channel.close()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_wrong_token_aborts_with_unauthenticated(grpc_server):
    """不正トークンで UNAUTHENTICATED が返ること。"""
    channel = aio.insecure_channel(f"localhost:{grpc_server}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()

    await stream.write(engine_pb2.Command(
        hello=engine_pb2.HelloRequest(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            token="wrong-token",
            mode=engine_pb2.APP_MODE_LIVE,
        )
    ))

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stream.read()
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED

    await channel.close()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_schema_major_mismatch_aborts_with_failed_precondition(grpc_server):
    """schema_major 不一致で FAILED_PRECONDITION が返ること。"""
    channel = aio.insecure_channel(f"localhost:{grpc_server}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()

    await stream.write(engine_pb2.Command(
        hello=engine_pb2.HelloRequest(
            schema_major=SCHEMA_MAJOR + 99,
            schema_minor=SCHEMA_MINOR,
            token="test-token",
            mode=engine_pb2.APP_MODE_LIVE,
        )
    ))

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stream.read()
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    await channel.close()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_non_hello_first_message_aborts_with_invalid_argument(grpc_server):
    """最初のメッセージが HelloRequest でない場合 INVALID_ARGUMENT。"""
    channel = aio.insecure_channel(f"localhost:{grpc_server}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()

    await stream.write(engine_pb2.Command(
        ping=engine_pb2.PingRequest(request_id="test")
    ))

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stream.read()
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    await channel.close()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_ping_returns_pong(grpc_server):
    """Ping コマンドに対して Pong イベントが返ること。"""
    channel = aio.insecure_channel(f"localhost:{grpc_server}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()

    await stream.write(engine_pb2.Command(
        hello=engine_pb2.HelloRequest(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            token="test-token",
            mode=engine_pb2.APP_MODE_LIVE,
        )
    ))

    # ReadyResponse
    event = await stream.read()
    assert event.HasField("ready")

    # Ping
    await stream.write(engine_pb2.Command(
        ping=engine_pb2.PingRequest(request_id="ping-001")
    ))

    # ClientConnected か Pong を受け取る
    received_pong = False
    for _ in range(5):
        event = await asyncio.wait_for(stream.read(), timeout=3.0)
        if event.HasField("pong"):
            assert event.pong.request_id == "ping-001"
            received_pong = True
            break
        elif event.HasField("client_connected"):
            continue

    assert received_pong, "Pong not received"

    stream.cancel()
    await channel.close()
