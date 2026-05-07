"""G0.9 — EngineSession.transport フィールドと _resolve_endpoint_and_token() gRPC 分岐テスト.

writer 側 acceptance pin:
  - セッションファイルに transport="grpc" が書き込めること（Rust が書く契約を
    Python 側で手動再現して読み取り経路を検証する）

reader 側 acceptance pin:
  - _resolve_endpoint_and_token() が transport="grpc" のとき gRPC チャネル URI を返すこと
  - ReplaySession / LiveSession 両方で同様に動作すること
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from engine.replay_session import (
    ReplaySession,
    LiveSession,
    _read_session_file,
)


# ---------------------------------------------------------------------------
# _read_session_file: transport フィールドのパス
# ---------------------------------------------------------------------------


def test_read_session_file_returns_transport_ws(tmp_path):
    """transport="ws" のセッションファイルは transport フィールドが読み取れること。"""
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(
        json.dumps({
            "port": 19876,
            "token": "tok-ws",
            "pid": os.getpid(),
            "schema_major": 3,
            "started_at": "2026-05-08T00:00:00Z",
            "transport": "ws",
        }),
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is not None
    assert result.get("transport") == "ws"


def test_read_session_file_returns_transport_grpc(tmp_path):
    """transport="grpc" のセッションファイルは transport フィールドが読み取れること。"""
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(
        json.dumps({
            "port": 19876,
            "token": "tok-grpc",
            "pid": os.getpid(),
            "schema_major": 3,
            "started_at": "2026-05-08T00:00:00Z",
            "transport": "grpc",
        }),
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is not None
    assert result.get("transport") == "grpc"


# ---------------------------------------------------------------------------
# ReplaySession._resolve_endpoint_and_token() gRPC 分岐
# ---------------------------------------------------------------------------


def test_replay_session_resolve_endpoint_ws_from_session_file(tmp_path):
    """transport="ws" のセッションファイルは ws:// エンドポイントを返すこと。"""
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(
        json.dumps({
            "port": 19876,
            "token": "tok-ws",
            "pid": os.getpid(),
            "schema_major": 3,
            "started_at": "2026-05-08T00:00:00Z",
            "transport": "ws",
        }),
        encoding="utf-8",
    )

    session = ReplaySession.__new__(ReplaySession)
    session._attach_endpoint = None

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        endpoint, token = session._resolve_endpoint_and_token()

    assert endpoint is not None
    assert endpoint.startswith("ws://"), f"ws transport must return ws:// URL, got {endpoint!r}"
    assert token == "tok-ws"


def test_replay_session_resolve_endpoint_grpc_from_session_file(tmp_path):
    """transport="grpc" のセッションファイルは gRPC チャネル URI を返すこと。

    G0.9 reader 側 acceptance pin: external mode で transport="grpc" を読み取り、
    gRPC エンドポイントが返ることを確認する。
    """
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(
        json.dumps({
            "port": 19876,
            "token": "tok-grpc",
            "pid": os.getpid(),
            "schema_major": 3,
            "started_at": "2026-05-08T00:00:00Z",
            "transport": "grpc",
        }),
        encoding="utf-8",
    )

    session = ReplaySession.__new__(ReplaySession)
    session._attach_endpoint = None

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        endpoint, token = session._resolve_endpoint_and_token()

    assert endpoint is not None
    assert "ws://" not in endpoint, (
        f"grpc transport must NOT return ws:// URL, got {endpoint!r}"
    )
    assert "19876" in endpoint, f"endpoint must include port 19876, got {endpoint!r}"
    assert token == "tok-grpc"


def test_replay_session_resolve_endpoint_no_transport_defaults_to_ws(tmp_path):
    """transport フィールドが無い旧形式は ws:// エンドポイントを返すこと（後方互換）。"""
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(
        json.dumps({
            "port": 19876,
            "token": "tok-old",
            "pid": os.getpid(),
            "schema_major": 3,
            "started_at": "2026-05-08T00:00:00Z",
            # transport フィールド無し（旧形式）
        }),
        encoding="utf-8",
    )

    session = ReplaySession.__new__(ReplaySession)
    session._attach_endpoint = None

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        endpoint, token = session._resolve_endpoint_and_token()

    assert endpoint is not None
    assert endpoint.startswith("ws://"), (
        f"missing transport must default to ws://, got {endpoint!r}"
    )


# ---------------------------------------------------------------------------
# LiveSession._resolve_endpoint_and_token() gRPC 分岐
# ---------------------------------------------------------------------------


def test_live_session_resolve_endpoint_grpc_from_session_file(tmp_path):
    """LiveSession も transport="grpc" で gRPC URI を返すこと。

    G0.9 reader 側 acceptance pin: LiveSession 版。
    """
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(
        json.dumps({
            "port": 19876,
            "token": "tok-grpc-live",
            "pid": os.getpid(),
            "schema_major": 3,
            "started_at": "2026-05-08T00:00:00Z",
            "transport": "grpc",
        }),
        encoding="utf-8",
    )

    live = LiveSession.__new__(LiveSession)
    live._attach_endpoint = None

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        endpoint, token = live._resolve_endpoint_and_token()

    assert endpoint is not None
    assert "ws://" not in endpoint, (
        f"grpc transport must NOT return ws:// URL, got {endpoint!r}"
    )
    assert "19876" in endpoint, f"endpoint must include port 19876, got {endpoint!r}"
    assert token == "tok-grpc-live"


def test_live_session_resolve_endpoint_ws_from_session_file(tmp_path):
    """LiveSession も transport="ws" で ws:// URL を返すこと。"""
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(
        json.dumps({
            "port": 19876,
            "token": "tok-ws-live",
            "pid": os.getpid(),
            "schema_major": 3,
            "started_at": "2026-05-08T00:00:00Z",
            "transport": "ws",
        }),
        encoding="utf-8",
    )

    live = LiveSession.__new__(LiveSession)
    live._attach_endpoint = None

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        endpoint, token = live._resolve_endpoint_and_token()

    assert endpoint is not None
    assert endpoint.startswith("ws://"), f"ws transport must return ws:// URL, got {endpoint!r}"
    assert token == "tok-ws-live"


# ---------------------------------------------------------------------------
# writer 側 acceptance pin: セッションファイルに transport="grpc" が書き込まれること
# ---------------------------------------------------------------------------


def test_writer_acceptance_pin_grpc_transport_in_session_file(tmp_path):
    """G0.9 writer 側 acceptance pin.

    server_grpc.py（G1 で実装）が書くセッションファイルの構造を手動で再現し、
    transport="grpc" フィールドが正しく保存・読み取れることを確認する。

    G1 完了後はこのテストを実際の server_grpc.py サブプロセスを使うものに
    置き換えることが推奨される（G1 writer-side acceptance pin）。
    """
    session_file = tmp_path / "engine-session.json"

    # server_grpc.py が書くであろう内容を手動で再現する
    session_data = {
        "port": 50051,
        "token": "grpc-session-token",
        "pid": os.getpid(),
        "schema_major": 3,
        "started_at": "2026-05-08T12:00:00Z",
        "transport": "grpc",  # G0.9 の核心: このフィールドが必要
    }
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    # 書き込み内容を直接検証
    raw = json.loads(session_file.read_text(encoding="utf-8"))
    assert raw.get("transport") == "grpc", (
        "session file must contain transport='grpc' — "
        "this simulates what server_grpc.py will write in G1"
    )

    # Python helper が正しく読み取れることも確認
    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is not None
    assert result.get("transport") == "grpc"
    assert result.get("port") == 50051
