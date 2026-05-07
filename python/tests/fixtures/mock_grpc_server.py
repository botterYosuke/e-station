"""gRPC mock server for DataEngine.Session — G0.5 / S4-S5 テスト用フィクスチャ.

grpcio.aio ベースの最小実装:
  - Session 冒頭の HelloRequest を受け付け ReadyResponse を返す
  - 以降の Command はすべて無視してストリームを保持する
  - stop() は冪等（複数回呼んでも例外なし）

使い方:
    async with MockGrpcServer() as srv:
        channel = grpc.aio.insecure_channel(f"localhost:{srv.port}")
        stub = engine_pb2_grpc.DataEngineStub(channel)
        stream = stub.Session()
        await stream.write(Command(hello=HelloRequest(...)))
        event = await stream.read()
        assert event.HasField("ready")

S5 depth continuity テスト向け ScriptedMockGrpcServer:
    async with ScriptedMockGrpcServer(
        events_after_hello=[depth_snapshot_event, depth_diff_event],
    ) as srv:
        ...
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import grpc
from grpc import aio

from engine.proto import engine_pb2, engine_pb2_grpc

_ENGINE_VERSION = "mock-0.0.1"


class _DataEngineServicer(engine_pb2_grpc.DataEngineServicer):
    """最小限の Session RPC 実装.

    ハンドシェイク契約:
      - 最初の Command が HelloRequest であれば ReadyResponse を最初の Event として返す
      - schema_major 不一致は FAILED_PRECONDITION でストリームを終了する
      - HelloRequest 以外が最初のメッセージであれば INVALID_ARGUMENT で終了する
      - 以降の Command はすべて無視してストリームを保持する
    """

    def __init__(self, *, expected_schema_major: int = 0) -> None:
        # 0 = 何でも受け付ける（テスト用デフォルト）
        self._expected_schema_major = expected_schema_major

    async def Session(
        self,
        request_iterator: AsyncIterator[engine_pb2.Command],
        context: aio.ServicerContext,
    ) -> AsyncIterator[engine_pb2.Event]:
        handshake_done = False

        async for command in request_iterator:
            if not handshake_done:
                if not command.HasField("hello"):
                    await context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        "first message must be HelloRequest",
                    )
                    return

                hello = command.hello

                if (
                    self._expected_schema_major != 0
                    and hello.schema_major != self._expected_schema_major
                ):
                    await context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        f"schema_major mismatch: expected {self._expected_schema_major}, got {hello.schema_major}",
                    )
                    return

                yield engine_pb2.Event(
                    ready=engine_pb2.ReadyResponse(
                        schema_major=hello.schema_major,
                        schema_minor=hello.schema_minor,
                        engine_version=_ENGINE_VERSION,
                        engine_session_id=str(uuid.uuid4()),
                        capabilities=engine_pb2.EngineCapabilities(
                            supported_venues=[],
                            supports_bulk_trades=False,
                            supports_depth_binary=False,
                        ),
                    )
                )
                handshake_done = True
            # Subsequent commands are silently ignored — stream stays alive.


class MockGrpcServer:
    """テスト用 gRPC サーバーのライフサイクル管理.

    async context manager として使うか、start() / stop() を明示的に呼ぶ。

    Attributes:
        port: サーバーが Listen しているポート番号（start() 後に確定）。
    """

    def __init__(self, *, expected_schema_major: int = 0) -> None:
        self._servicer = _DataEngineServicer(
            expected_schema_major=expected_schema_major
        )
        self._server: aio.Server | None = None
        self.port: int = 0

    async def start(self) -> "MockGrpcServer":
        server = aio.server()
        engine_pb2_grpc.add_DataEngineServicer_to_server(self._servicer, server)
        # port=0 → OS がランダムポートを割り当てる
        self.port = server.add_insecure_port("[::]:0")
        await server.start()
        self._server = server
        return self

    async def stop(self) -> None:
        """サーバーを停止する。複数回呼んでも例外なし（冪等）。"""
        if self._server is None:
            return
        server, self._server = self._server, None
        await server.stop(grace=0)

    async def __aenter__(self) -> "MockGrpcServer":
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.stop()


# ---------------------------------------------------------------------------
# ScriptedMockGrpcServer — S5 depth continuity テスト用
# ---------------------------------------------------------------------------


class _ScriptedDataEngineServicer(engine_pb2_grpc.DataEngineServicer):
    """HandshakeRequest → ReadyResponse + scripted event stream + command reactions.

    Attributes:
        events_after_hello: HelloRequest に応答した直後に yield するイベント列。
        on_request_depth_snapshot: RequestDepthSnapshot を受信したときに yield するイベント列。
    """

    def __init__(
        self,
        *,
        events_after_hello: list,
        on_request_depth_snapshot: list,
    ) -> None:
        self._events_after_hello = events_after_hello
        self._on_request_depth_snapshot = on_request_depth_snapshot

    async def Session(
        self,
        request_iterator: AsyncIterator[engine_pb2.Command],
        context: aio.ServicerContext,
    ) -> AsyncIterator[engine_pb2.Event]:
        handshake_done = False

        async for command in request_iterator:
            if not handshake_done:
                if not command.HasField("hello"):
                    await context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        "first message must be HelloRequest",
                    )
                    return

                hello = command.hello
                yield engine_pb2.Event(
                    ready=engine_pb2.ReadyResponse(
                        schema_major=hello.schema_major,
                        schema_minor=hello.schema_minor,
                        engine_version=_ENGINE_VERSION,
                        engine_session_id=str(uuid.uuid4()),
                        capabilities=engine_pb2.EngineCapabilities(
                            supported_venues=[],
                            supports_bulk_trades=False,
                            supports_depth_binary=False,
                        ),
                    )
                )
                handshake_done = True

                # ReadyResponse の直後にスクリプト済みイベントを送出する
                for event in self._events_after_hello:
                    yield event
            else:
                # RequestDepthSnapshot に対してリアクション
                if (
                    command.HasField("request_depth_snapshot")
                    and self._on_request_depth_snapshot
                ):
                    for event in self._on_request_depth_snapshot:
                        yield event
                # その他のコマンドは無視してストリームを保持する


class ScriptedMockGrpcServer:
    """スクリプト済みイベントで応答する gRPC テストサーバー.

    MockGrpcServer の拡張版。ハンドシェイク後に ``events_after_hello`` を
    ストリームに送出し、``RequestDepthSnapshot`` を受信したときは
    ``on_request_depth_snapshot`` のイベントを返す。

    使い方:
        snapshot = engine_pb2.Event(depth_snapshot=...)
        diff = engine_pb2.Event(depth_diff=...)
        async with ScriptedMockGrpcServer(events_after_hello=[snapshot, diff]) as srv:
            channel = aio.insecure_channel(f"localhost:{srv.port}")
            ...

    Attributes:
        port: サーバーが Listen しているポート番号（start() 後に確定）。
    """

    def __init__(
        self,
        *,
        events_after_hello: list | None = None,
        on_request_depth_snapshot: list | None = None,
    ) -> None:
        self._servicer = _ScriptedDataEngineServicer(
            events_after_hello=events_after_hello or [],
            on_request_depth_snapshot=on_request_depth_snapshot or [],
        )
        self._server: aio.Server | None = None
        self.port: int = 0

    async def start(self) -> "ScriptedMockGrpcServer":
        server = aio.server()
        engine_pb2_grpc.add_DataEngineServicer_to_server(self._servicer, server)
        self.port = server.add_insecure_port("[::]:0")
        await server.start()
        self._server = server
        return self

    async def stop(self) -> None:
        if self._server is None:
            return
        server, self._server = self._server, None
        await server.stop(grace=0)

    async def __aenter__(self) -> "ScriptedMockGrpcServer":
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
