"""G1 統合テスト: server_grpc.py の multi-client 動作検証。"""
import pytest
import asyncio
import grpc
from grpc import aio
from engine.proto import engine_pb2, engine_pb2_grpc
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR
from engine.server_grpc import _GrpcDataEngineServicer, GrpcDataEngineServer
from pathlib import Path
import tempfile


@pytest.fixture
async def multi_client_server():
    """multi-client テスト用サーバー。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        from engine.server import DataEngineServer
        inner = DataEngineServer(
            port=0,
            token="test-token",
            cache_dir=Path(tmpdir),
            config_dir=Path(tmpdir),
        )
        grpc_srv = aio.server()
        servicer = _GrpcDataEngineServicer(inner)
        engine_pb2_grpc.add_DataEngineServicer_to_server(servicer, grpc_srv)
        port = grpc_srv.add_insecure_port("[::]:0")
        await grpc_srv.start()
        yield port, inner
        await grpc_srv.stop(grace=0)


async def _connect_and_handshake(port: int, token: str = "test-token") -> tuple:
    """ハンドシェイクまで完了した gRPC ストリームを返す。"""
    channel = aio.insecure_channel(f"localhost:{port}")
    stub = engine_pb2_grpc.DataEngineStub(channel)
    stream = stub.Session()
    await stream.write(engine_pb2.Command(
        hello=engine_pb2.HelloRequest(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            token=token,
            mode=engine_pb2.APP_MODE_LIVE,
        )
    ))
    event = await asyncio.wait_for(stream.read(), timeout=5.0)
    assert event.HasField("ready"), f"Expected ready, got {event}"
    return channel, stream


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_two_clients_both_receive_client_connected(multi_client_server):
    """2 クライアント同時接続時に両方が ClientConnected を受け取ること。"""
    port, _ = multi_client_server

    ch1, stream1 = await _connect_and_handshake(port)
    ch2, stream2 = await _connect_and_handshake(port)

    await asyncio.sleep(0.2)  # broadcast が届くまで待つ

    # drain events
    async def drain_for_event(stream, event_type: str, timeout=3.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                ev = await asyncio.wait_for(stream.read(), timeout=0.5)
                if ev.HasField(event_type):
                    return ev
            except asyncio.TimeoutError:
                break
        return None

    ev1 = await drain_for_event(stream1, "client_connected")
    ev2 = await drain_for_event(stream2, "client_connected")

    assert ev1 is not None and ev2 is not None, "Not all clients received ClientConnected"

    stream1.cancel()
    stream2.cancel()
    await ch1.close()
    await ch2.close()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_max_connections_exceeded_returns_resource_exhausted(multi_client_server):
    """MAX_CONNECTIONS を超えた接続が RESOURCE_EXHAUSTED で拒否されること。"""
    from engine.server_grpc import MAX_CONNECTIONS
    port, _ = multi_client_server

    channels = []
    streams = []

    try:
        # MAX_CONNECTIONS まで接続
        for _ in range(MAX_CONNECTIONS):
            ch, st = await _connect_and_handshake(port)
            channels.append(ch)
            streams.append(st)

        await asyncio.sleep(0.1)

        # MAX_CONNECTIONS + 1 番目の接続
        extra_ch = aio.insecure_channel(f"localhost:{port}")
        extra_stub = engine_pb2_grpc.DataEngineStub(extra_ch)
        extra_stream = extra_stub.Session()

        await extra_stream.write(engine_pb2.Command(
            hello=engine_pb2.HelloRequest(
                schema_major=SCHEMA_MAJOR,
                schema_minor=SCHEMA_MINOR,
                token="test-token",
                mode=engine_pb2.APP_MODE_LIVE,
            )
        ))

        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await asyncio.wait_for(extra_stream.read(), timeout=5.0)
        assert exc_info.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED

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


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_duplicate_hello_aborts_session(multi_client_server):
    """Session 中に 2 回目の HelloRequest を受信した場合 INVALID_ARGUMENT でストリームが終了すること。"""
    port, _ = multi_client_server
    ch, stream = await _connect_and_handshake(port)

    # 2回目の HelloRequest を送信
    await stream.write(engine_pb2.Command(
        hello=engine_pb2.HelloRequest(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            token="test-token",
            mode=engine_pb2.APP_MODE_LIVE,
        )
    ))

    # 数回 read してエラーを確認
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        for _ in range(10):
            ev = await asyncio.wait_for(stream.read(), timeout=2.0)
            if ev == grpc.aio.EOF:
                break
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    await ch.close()
