"""MockGrpcServer S4 — gRPC attach 解決シナリオテスト.

S4-A: 明示 gRPC エンドポイント指定で直接接続できること
S4-B: session ファイル (transport="grpc") 経由でエンドポイントを解決し接続できること
S4-C: stale pid の session ファイルは _read_session_file が None を返すこと
S4-D: session ファイルなし + token なし → _resolve_endpoint_and_token が (None, None) を返すこと

WS 版 MockIPCServer の S4 テストを gRPC (MockGrpcServer) ベースで書き直したもの（G3 完了後）。
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import grpc
import pytest
from grpc import aio

from engine.proto import engine_pb2, engine_pb2_grpc
from engine.replay_session import ReplaySession, _read_session_file
from tests.fixtures.mock_grpc_server import MockGrpcServer

pytestmark = pytest.mark.timeout(5)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _do_hello(stub: engine_pb2_grpc.DataEngineStub):
    """Session ストリームを開いて HelloRequest を送り、ストリームを返す。"""
    stream = stub.Session()
    return stream


async def _handshake(
    stub: engine_pb2_grpc.DataEngineStub,
    *,
    schema_major: int = 3,
    schema_minor: int = 0,
    token: str = "test-token",
) -> tuple:
    """HelloRequest を送り、ReadyResponse を受け取る。(stream, ready) を返す。"""
    stream = stub.Session()
    await stream.write(
        engine_pb2.Command(
            hello=engine_pb2.HelloRequest(
                schema_major=schema_major,
                schema_minor=schema_minor,
                client_version="test-0.0.1",
                token=token,
                mode=engine_pb2.APP_MODE_REPLAY,
            )
        )
    )
    event = await stream.read()
    return stream, event.ready


# ---------------------------------------------------------------------------
# S4-A: 明示 gRPC エンドポイントで直接接続
# ---------------------------------------------------------------------------


class TestS4aDirectGrpcEndpointConnect:
    """S4-A: 明示 attach_endpoint=f'127.0.0.1:{port}' で MockGrpcServer に直接接続できること。"""

    async def test_direct_grpc_channel_handshake(self) -> None:
        """grpcio チャネルで MockGrpcServer に接続し ReadyResponse を受け取ること。"""
        async with MockGrpcServer() as srv:
            channel = aio.insecure_channel(f"127.0.0.1:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            _, ready = await _handshake(stub)

            assert ready.schema_major == 3
            assert len(ready.engine_session_id) > 0
            await channel.close()

    async def test_direct_grpc_connection_ready_contains_engine_version(self) -> None:
        """ReadyResponse に engine_version が含まれること。"""
        async with MockGrpcServer() as srv:
            channel = aio.insecure_channel(f"127.0.0.1:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            _, ready = await _handshake(stub)

            assert len(ready.engine_version) > 0
            await channel.close()

    async def test_wrong_schema_major_aborts_with_failed_precondition(self) -> None:
        """schema_major 不一致は FAILED_PRECONDITION で弾かれること。"""
        async with MockGrpcServer(expected_schema_major=3) as srv:
            channel = aio.insecure_channel(f"127.0.0.1:{srv.port}")
            stub = engine_pb2_grpc.DataEngineStub(channel)
            stream = stub.Session()
            await stream.write(
                engine_pb2.Command(
                    hello=engine_pb2.HelloRequest(
                        schema_major=99,
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
            await channel.close()


# ---------------------------------------------------------------------------
# S4-B: session ファイル (transport="grpc") 経由でエンドポイントを解決し接続
# ---------------------------------------------------------------------------


class TestS4bSessionFileGrpcTransportConnect:
    """S4-B: engine-session.json の transport='grpc' からエンドポイントを解決し接続できること。"""

    async def test_session_file_grpc_endpoint_resolves_to_correct_format(
        self, tmp_path
    ) -> None:
        """transport='grpc' の session ファイルは '127.0.0.1:{port}' 形式を返すこと。"""
        async with MockGrpcServer() as srv:
            session_file = tmp_path / "engine-session.json"
            session_file.write_text(
                json.dumps(
                    {
                        "port": srv.port,
                        "token": "session-token",
                        "pid": os.getpid(),
                        "schema_major": 3,
                        "started_at": "2026-05-08T00:00:00Z",
                        "transport": "grpc",
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
                s = ReplaySession()
                endpoint, token = s._resolve_endpoint_and_token()

            assert endpoint == f"127.0.0.1:{srv.port}", (
                f"expected gRPC channel target format, got: {endpoint!r}"
            )
            assert token == "session-token"

    async def test_session_file_grpc_endpoint_can_connect_to_mock_server(
        self, tmp_path
    ) -> None:
        """session ファイルで解決した gRPC エンドポイントに実際に接続できること。"""
        async with MockGrpcServer() as srv:
            session_file = tmp_path / "engine-session.json"
            session_file.write_text(
                json.dumps(
                    {
                        "port": srv.port,
                        "token": "session-token",
                        "pid": os.getpid(),
                        "schema_major": 3,
                        "started_at": "2026-05-08T00:00:00Z",
                        "transport": "grpc",
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
                s = ReplaySession()
                endpoint, token = s._resolve_endpoint_and_token()

            # 解決したエンドポイントで実際に接続できることを確認する
            channel = aio.insecure_channel(endpoint)
            stub = engine_pb2_grpc.DataEngineStub(channel)
            _, ready = await _handshake(stub, token=token)
            assert ready.schema_major == 3
            await channel.close()


# ---------------------------------------------------------------------------
# S4-C: stale pid → session ファイルは None を返す
# ---------------------------------------------------------------------------


class TestS4cStalePidFallback:
    """S4-C: 存在しない pid の session ファイルは _read_session_file が None を返すこと。"""

    def test_stale_pid_session_file_returns_none(self, tmp_path) -> None:
        """dead pid を持つ session ファイルは None を返すこと。"""
        session_file = tmp_path / "engine-session.json"
        session_file.write_text(
            json.dumps(
                {
                    "port": 50051,
                    "token": "stale-token",
                    "pid": 999999999,  # 存在しない pid
                    "schema_major": 3,
                    "started_at": "2026-05-08T00:00:00Z",
                    "transport": "grpc",
                }
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
            result = _read_session_file()

        assert result is None, "stale pid の session ファイルは None を返すべき"

    def test_stale_pid_causes_no_endpoint_resolution(self, tmp_path) -> None:
        """dead pid → session ファイルが None → env-only か (None, None) にフォールバック。"""
        session_file = tmp_path / "engine-session.json"
        session_file.write_text(
            json.dumps(
                {
                    "port": 50051,
                    "token": "stale-token",
                    "pid": 999999999,
                    "schema_major": 3,
                    "started_at": "2026-05-08T00:00:00Z",
                    "transport": "grpc",
                }
            ),
            encoding="utf-8",
        )

        env = {
            "FLOWSURFACE_DATA_PATH": str(tmp_path),
        }
        with patch.dict(os.environ, env, clear=False):
            # FLOWSURFACE_ENGINE_TOKEN を削除して env-only パスを無効化
            env_without_token = {
                k: v for k, v in os.environ.items() if k != "FLOWSURFACE_ENGINE_TOKEN"
            }
            with patch.dict(os.environ, env_without_token, clear=True):
                s = ReplaySession()
                endpoint, token = s._resolve_endpoint_and_token()

        assert endpoint is None, "stale pid + no token env → endpoint は None のはず"
        assert token is None


# ---------------------------------------------------------------------------
# S4-D: session ファイルなし + token なし → (None, None)
# ---------------------------------------------------------------------------


class TestS4dNoSessionFileNoToken:
    """S4-D: session ファイルなし + FLOWSURFACE_ENGINE_TOKEN 未設定 → (None, None)。
    また session ファイルなし + TOKEN 設定あり → env-only パス (ws://...19876/) を返すこと。
    """

    def test_no_session_no_token_returns_none_none(self, tmp_path) -> None:
        """session ファイルも token env も存在しない場合 (None, None) を返すこと。"""
        env_without_token = {
            k: v
            for k, v in os.environ.items()
            if k not in ("FLOWSURFACE_ENGINE_TOKEN", "FLOWSURFACE_ENGINE_PORT")
        }
        env_without_token["FLOWSURFACE_DATA_PATH"] = str(tmp_path)

        with patch.dict(os.environ, env_without_token, clear=True):
            s = ReplaySession()
            endpoint, token = s._resolve_endpoint_and_token()

        assert endpoint is None
        assert token is None

    def test_env_only_token_set_returns_ws_endpoint(self, tmp_path) -> None:
        """session ファイルなし + TOKEN 設定あり → env-only パス (c) を返すこと。

        _resolve_endpoint_and_token() の優先順位 (c): session ファイルが存在せず
        FLOWSURFACE_ENGINE_TOKEN が設定されている場合、既定ポート 19876 で
        ws://127.0.0.1:19876/ 形式の endpoint を返す。
        """
        env_with_token = {
            k: v
            for k, v in os.environ.items()
            if k != "FLOWSURFACE_ENGINE_PORT"
        }
        env_with_token["FLOWSURFACE_DATA_PATH"] = str(tmp_path)
        env_with_token["FLOWSURFACE_ENGINE_TOKEN"] = "env-only-token"

        with patch.dict(os.environ, env_with_token, clear=True):
            s = ReplaySession()
            endpoint, token = s._resolve_endpoint_and_token()

        assert endpoint == "ws://127.0.0.1:19876/", (
            f"env-only path must return default WS endpoint, got: {endpoint!r}\n"
            "Fix: ensure FLOWSURFACE_ENGINE_TOKEN is read in the env-only branch of "
            "_resolve_endpoint_and_token() and port defaults to 19876."
        )
        assert token == "env-only-token"

    def test_force_attach_no_session_no_token_raises_connection_refused(
        self, tmp_path
    ) -> None:
        """force_mode='attach' でも session ファイルも token もなければ ConnectionRefusedError。"""
        env_without_token = {
            k: v
            for k, v in os.environ.items()
            if k not in ("FLOWSURFACE_ENGINE_TOKEN", "FLOWSURFACE_ENGINE_PORT")
        }
        env_without_token["FLOWSURFACE_DATA_PATH"] = str(tmp_path)

        with patch.dict(os.environ, env_without_token, clear=True):
            with pytest.raises(ConnectionRefusedError):
                with ReplaySession(force_mode="attach"):
                    pass
