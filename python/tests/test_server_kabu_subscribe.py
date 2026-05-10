"""server.py _handle_subscribe() で venue=kabu_station を受け入れるテスト（Issue #28 層2）。

問題:
  _workers dict に kabu_station が含まれていないため、
  Subscribe(venue='kabu_station') を受け取ると即 Error{code='unknown_venue'} を返す。

期待動作:
  Subscribe(venue='kabu_station', stream='depth') は unknown_venue エラーを返さない。
"""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from engine.server import DataEngineServer


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


class _StubOutbox:
    def __init__(self) -> None:
        self._q: deque[dict] = deque()

    def append(self, item: dict) -> None:
        self._q.append(item)

    def send_to(self, _ws: object, item: dict) -> None:
        self._q.append(item)

    def count(self) -> int:
        return 0

    def __len__(self) -> int:
        return len(self._q)

    def __iter__(self):
        return iter(list(self._q))


def _make_subscribe_server() -> DataEngineServer:
    """_handle_subscribe テスト用の最小 DataEngineServer インスタンスを返す。

    注意: このヘルパーと test_kabu_put_register.py の _make_subscribe_server() は同期を保つこと。
    """
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _StubOutbox()  # type: ignore[assignment]
    server._outbox_event = asyncio.Event()
    server._engine_session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    server._stream_counter = 0
    server._streams: dict = {}  # type: ignore[attr-defined]
    # kabu_station は _workers に含まれない（問題の核心）
    server._workers = {}  # type: ignore[attr-defined]
    server._kabu_push_ssid = "test-ssid"
    server._kabu_push_seq = 0
    server._kabu_adapter = MagicMock()
    server._kabu_venue = MagicMock()
    server._kabu_venue._token = "test-session-token"
    server._kabu_env = "verify"
    server._kabu_register_set = MagicMock()  # type: ignore[attr-defined]
    server._kabu_register_set.register = MagicMock()
    server._kabu_register_set.all_symbols = MagicMock(return_value=[])
    # Issue #35: _kabu_put_register は HTTP を呼ぶようになったが、
    # このテストは subscribe ルーティングのみを検証するため no-op に差し替える。
    from unittest.mock import AsyncMock
    server._kabu_put_register = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return server


# ---------------------------------------------------------------------------
# テスト本体
# ---------------------------------------------------------------------------


class TestSubscribeKabuStation:
    @pytest.mark.asyncio
    async def test_subscribe_kabu_station_does_not_return_unknown_venue(self) -> None:
        """Subscribe(venue='kabu_station') が Error{code='unknown_venue'} を
        返さないことを確認する（Issue #28 層2）。"""
        server = _make_subscribe_server()

        msg = {
            "venue": "kabu_station",
            "ticker": "1234",
            "stream": "depth",
            "market": "stock",
        }
        await server._handle_subscribe(msg)

        # unknown_venue エラーが outbox に書かれていないことを確認
        for item in server._outbox._q:
            assert not (
                item.get("event") == "Error" and item.get("code") == "unknown_venue"
            ), f"Should not get unknown_venue error for kabu_station, got: {item}"

    @pytest.mark.asyncio
    async def test_subscribe_kabu_station_depth_starts_stream_task(self) -> None:
        """Subscribe(venue='kabu_station', stream='depth') がストリームタスクを登録する。"""
        server = _make_subscribe_server()

        msg = {
            "venue": "kabu_station",
            "ticker": "1234",
            "stream": "depth",
            "market": "stock",
        }
        await server._handle_subscribe(msg)

        # ストリームキーが _streams に登録されていることを確認
        keys = list(server._streams.keys())
        assert any(
            k[0] == "kabu_station" and k[1] == "1234" and k[3] == "depth"
            for k in keys
        ), f"Expected kabu_station depth stream in _streams, got keys: {keys}"

    @pytest.mark.asyncio
    async def test_subscribe_unknown_venue_still_returns_error(self) -> None:
        """既存の動作：未知の venue は引き続き unknown_venue エラーを返す。

        kabu_station の特別扱いが他 venue の既存動作を壊さないことを確認する。
        """
        server = _make_subscribe_server()

        msg = {
            "venue": "nonexistent_venue",
            "ticker": "1234",
            "stream": "depth",
            "market": "stock",
        }
        await server._handle_subscribe(msg)

        errors = [
            item for item in server._outbox._q
            if item.get("event") == "Error" and item.get("code") == "unknown_venue"
        ]
        assert len(errors) == 1, (
            f"Expected exactly one unknown_venue error for nonexistent_venue, got: {list(server._outbox._q)}"
        )
