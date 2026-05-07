"""G0.5 完了条件テスト: MockGrpcServer の基本動作確認.

完了条件（ipc-grpc-migration.md G0.5 より）:
  - mock サーバーが起動できる
  - HelloRequest を送ると ReadyResponse が最初の Event として返ってくる
  - ストリームが確立したまま維持される
  - stop() を連続呼び出しても例外が発生しない（冪等性）

全テストに @pytest.mark.timeout(1) を付与し、1 秒以内に PASS することを確認する。
"""
from __future__ import annotations

import pytest
import grpc
from grpc import aio

from engine.proto import engine_pb2, engine_pb2_grpc
from tests.fixtures.mock_grpc_server import MockGrpcServer

pytestmark = pytest.mark.timeout(1)


@pytest.fixture
async def server() -> MockGrpcServer:
    async with MockGrpcServer() as srv:
        yield srv


@pytest.fixture
def channel(server: MockGrpcServer):
    ch = aio.insecure_channel(f"localhost:{server.port}")
    yield ch
    # channel cleanup happens implicitly


class TestMockGrpcServerLifecycle:
    async def test_server_starts_and_has_port(self, server: MockGrpcServer) -> None:
        assert server.port > 0

    async def test_stop_is_idempotent(self) -> None:
        srv = MockGrpcServer()
        await srv.start()
        await srv.stop()
        await srv.stop()  # 2 回目も例外なし

    async def test_context_manager_starts_and_stops(self) -> None:
        async with MockGrpcServer() as srv:
            assert srv.port > 0
        # __aexit__ 後は停止済み — 再 stop() も安全
        await srv.stop()


class TestHandshake:
    async def test_hello_returns_ready_as_first_event(
        self, server: MockGrpcServer, channel: aio.Channel
    ) -> None:
        stub = engine_pb2_grpc.DataEngineStub(channel)
        stream = stub.Session()

        await stream.write(
            engine_pb2.Command(
                hello=engine_pb2.HelloRequest(
                    schema_major=3,
                    schema_minor=18,
                    client_version="test-0.0.1",
                    token="test-token",
                    mode=engine_pb2.APP_MODE_LIVE,
                )
            )
        )

        event = await stream.read()
        assert event is not grpc.aio.EOF
        assert event.HasField("ready"), f"expected ready, got {event.WhichOneof('payload')}"

    async def test_ready_echoes_schema_version(
        self, server: MockGrpcServer, channel: aio.Channel
    ) -> None:
        stub = engine_pb2_grpc.DataEngineStub(channel)
        stream = stub.Session()

        await stream.write(
            engine_pb2.Command(
                hello=engine_pb2.HelloRequest(
                    schema_major=3,
                    schema_minor=18,
                    client_version="test-0.0.1",
                    token="tok",
                    mode=engine_pb2.APP_MODE_LIVE,
                )
            )
        )

        event = await stream.read()
        ready = event.ready
        assert ready.schema_major == 3
        assert ready.schema_minor == 18
        assert ready.engine_version == "mock-0.0.1"
        assert len(ready.engine_session_id) > 0

    async def test_ready_engine_session_id_is_unique_per_session(
        self, server: MockGrpcServer, channel: aio.Channel
    ) -> None:
        stub = engine_pb2_grpc.DataEngineStub(channel)

        async def do_handshake() -> str:
            stream = stub.Session()
            await stream.write(
                engine_pb2.Command(
                    hello=engine_pb2.HelloRequest(
                        schema_major=1,
                        schema_minor=0,
                        client_version="v",
                        token="t",
                        mode=engine_pb2.APP_MODE_LIVE,
                    )
                )
            )
            event = await stream.read()
            await stream.done_writing()
            return event.ready.engine_session_id

        id1 = await do_handshake()
        id2 = await do_handshake()
        assert id1 != id2

    async def test_stream_stays_open_after_handshake(
        self, server: MockGrpcServer, channel: aio.Channel
    ) -> None:
        stub = engine_pb2_grpc.DataEngineStub(channel)
        stream = stub.Session()

        await stream.write(
            engine_pb2.Command(
                hello=engine_pb2.HelloRequest(
                    schema_major=1,
                    schema_minor=0,
                    client_version="v",
                    token="t",
                    mode=engine_pb2.APP_MODE_REPLAY,
                )
            )
        )
        event = await stream.read()
        assert event.HasField("ready")

        # 追加コマンドを送っても例外なし（無視される）
        await stream.write(engine_pb2.Command(ping=engine_pb2.PingRequest(request_id="p1")))
        await stream.write(engine_pb2.Command(ping=engine_pb2.PingRequest(request_id="p2")))

        # done_writing でクライアント側を閉じてもサーバーは問題なし
        await stream.done_writing()


class TestHandshakeFailures:
    async def test_non_hello_first_message_aborts_with_invalid_argument(
        self, server: MockGrpcServer, channel: aio.Channel
    ) -> None:
        stub = engine_pb2_grpc.DataEngineStub(channel)
        stream = stub.Session()

        await stream.write(
            engine_pb2.Command(ping=engine_pb2.PingRequest(request_id="oops"))
        )

        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await stream.read()
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT

    async def test_schema_major_mismatch_aborts_with_failed_precondition(self) -> None:
        async with MockGrpcServer(expected_schema_major=3) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()

            await stream.write(
                engine_pb2.Command(
                    hello=engine_pb2.HelloRequest(
                        schema_major=99,  # wrong major
                        schema_minor=0,
                        client_version="v",
                        token="t",
                        mode=engine_pb2.APP_MODE_LIVE,
                    )
                )
            )

            with pytest.raises(grpc.aio.AioRpcError) as exc_info:
                await stream.read()
            assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
