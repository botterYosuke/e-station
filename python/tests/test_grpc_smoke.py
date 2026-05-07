"""G1 smoke test: server_grpc.py の起動・ハンドシェイク・Ping/Pong を検証する。"""
import json
import pytest
import asyncio
import grpc
from grpc import aio
from engine.proto import engine_pb2, engine_pb2_grpc
from engine.server_grpc import SCHEMA_MAJOR, SCHEMA_MINOR
from engine.server_grpc import GrpcDataEngineServer
from pathlib import Path
import tempfile

pytestmark = pytest.mark.smoke


def test_write_grpc_session_file_is_importable_and_callable(tmp_path, monkeypatch):
    """GrpcDataEngineServer.serve() 内で呼ばれる _write_grpc_session_file の smoke テスト。

    M-E: セッションファイルが正しく書き込まれることを確認する。
    """
    monkeypatch.setenv("FLOWSURFACE_DATA_PATH", str(tmp_path))
    from engine.server_grpc import _write_grpc_session_file

    expected_token = "smoke-token"
    _write_grpc_session_file(port=50098, token=expected_token)
    data = json.loads((tmp_path / "engine-session.json").read_text())
    assert data.get("transport") == "grpc"
    assert data.get("token") == expected_token


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
@pytest.mark.timeout(5)
async def test_schema_minor_mismatch_still_connects(grpc_server):
    """schema_minor が SCHEMA_MINOR と異なっても ReadyResponse が返ること (H13)。

    schema_minor の不一致は後方互換変更であり、接続を拒否してはならない。
    サーバーは警告ログを出力するが ReadyResponse を返し続ける。
    """
    channel = aio.insecure_channel(f"localhost:{grpc_server}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()

    await stream.write(engine_pb2.Command(
        hello=engine_pb2.HelloRequest(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR + 1,  # minor mismatch — should still connect
            token="test-token",
            mode=engine_pb2.APP_MODE_LIVE,
        )
    ))

    event = await stream.read()
    assert event != grpc.aio.EOF
    assert event.HasField("ready"), (
        f"Expected ReadyResponse even with schema_minor mismatch, got: {event}"
    )
    assert event.ready.schema_major == SCHEMA_MAJOR

    stream.cancel()
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
