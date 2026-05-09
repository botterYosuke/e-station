"""Issue #35 リグレッションテスト: PUT /register が実際に呼ばれることを検証する。

根本原因:
  Bug A: _kabu_put_register に HTTP 呼び出しがなく、ログのみで終わっていた。
  Bug B: _handle_subscribe_kabu_station で subscribe 後に PUT /register を呼ばなかった。

修正:
  - KabuRestClient.put_register() を追加（PUT /kabusapi/register の実装）
  - _kabu_put_register で put_register() を実際に呼ぶ
  - _handle_subscribe_kabu_station で subscribe 直後に _kabu_put_register を呼ぶ
"""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_httpx

from engine.exchanges.kabusapi_register import RegisterSet
from engine.exchanges.kabusapi_rest import KabuRestClient
from engine.exchanges.kabusapi_url import endpoint
from engine.server import DataEngineServer


# ---------------------------------------------------------------------------
# KabuRestClient.put_register テスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_put_register_sends_put_request(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """put_register() が PUT /kabusapi/register に HTTP リクエストを送ることを確認する。

    Regression: Bug A — _kabu_put_register に HTTP 呼び出しが存在しなかった。
    Fix: KabuRestClient.put_register() を追加して HTTP PUT を実装する。
    """
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("register", env="verify"),
        json={"RegistList": [{"Symbol": "7203", "Exchange": 1}]},
    )

    rs = RegisterSet(max_symbols=50)
    client = KabuRestClient(token="test-token", env="verify", register_set=rs)

    await client.put_register([("7203", 1)])

    requests = httpx_mock.get_requests()
    assert len(requests) == 1, (
        "put_register() は PUT /kabusapi/register を 1 回呼ぶ必要があります。"
        " KabuRestClient.put_register() が未実装か HTTP 呼び出しがありません。"
    )
    assert requests[0].method == "PUT"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_put_register_payload_format(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """put_register() が {"Symbols": [{"Symbol": ..., "Exchange": ...}]} 形式で送ることを確認する。

    kabuStation API 仕様: PUT /register の body は Symbols キー（複数形）で配列を渡す。
    """
    import json as _json

    httpx_mock.add_response(
        method="PUT",
        url=endpoint("register", env="verify"),
        json={"RegistList": [{"Symbol": "7203", "Exchange": 1}, {"Symbol": "9433", "Exchange": 1}]},
    )

    rs = RegisterSet(max_symbols=50)
    client = KabuRestClient(token="test-token", env="verify", register_set=rs)

    await client.put_register([("7203", 1), ("9433", 1)])

    req = httpx_mock.get_requests()[0]
    body = _json.loads(req.content)
    assert "Symbols" in body, (
        "PUT /register の body に 'Symbols' キーが必要です（複数形）。"
        f" 実際の body: {body}"
    )
    symbols = body["Symbols"]
    assert len(symbols) == 2
    assert {"Symbol": "7203", "Exchange": 1} in symbols
    assert {"Symbol": "9433", "Exchange": 1} in symbols


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_put_register_empty_symbols_skips_http(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """put_register([]) は HTTP を呼ばずに即リターンする。"""
    rs = RegisterSet(max_symbols=50)
    client = KabuRestClient(token="test-token", env="verify", register_set=rs)

    await client.put_register([])

    assert httpx_mock.get_requests() == [], (
        "put_register([]) は HTTP PUT を送るべきではありません。"
    )


# ---------------------------------------------------------------------------
# DataEngineServer._kabu_put_register テスト
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


def _make_server_with_kabu_venue() -> DataEngineServer:
    """_kabu_put_register テスト用の最小 DataEngineServer を返す。

    注意: このヘルパーと test_server_kabu_subscribe.py の _make_subscribe_server() は同期を保つこと。
    """
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _StubOutbox()
    server._outbox_event = asyncio.Event()
    server._engine_session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    server._stream_counter = 0
    server._streams: dict = {}
    server._workers = {}

    mock_venue = MagicMock()
    mock_venue.is_connected = True
    mock_venue._token = "test-session-token"
    server._kabu_venue = mock_venue
    server._kabu_env = "verify"

    register_set = RegisterSet(max_symbols=50)
    register_set.register("7203", 1)
    server._kabu_register_set = register_set

    server._kabu_push_ssid = "test-ssid"
    server._kabu_push_seq = 0
    server._kabu_adapter = MagicMock()
    return server


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_kabu_put_register_calls_http_put(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """_kabu_put_register() が実際に HTTP PUT /register を呼ぶことを確認する。

    Regression: Bug A — 関数はログを出力するだけで HTTP 呼び出しがなかった。
    Fix: _kabu_put_register() 内で KabuRestClient.put_register() を呼ぶ。
    """
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("register", env="verify"),
        json={"RegistList": [{"Symbol": "7203", "Exchange": 1}]},
    )

    server = _make_server_with_kabu_venue()
    await server._kabu_put_register([("7203", 1)])

    requests = httpx_mock.get_requests()
    assert len(requests) == 1, (
        "_kabu_put_register() は PUT /kabusapi/register を 1 回呼ぶ必要があります。"
        " HTTP 呼び出しが実装されていません（Bug A 未修正）。"
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_kabu_put_register_skips_when_not_connected() -> None:
    """_kabu_venue.is_connected=False のとき HTTP を呼ばない。"""
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._kabu_venue = MagicMock()
    server._kabu_venue.is_connected = False
    server._kabu_venue._token = "token"
    server._kabu_env = "verify"
    server._kabu_register_set = RegisterSet(max_symbols=50)

    mock_client = AsyncMock()
    with patch.object(server, "_make_kabu_rest_client", return_value=mock_client):
        await server._kabu_put_register([("7203", 1)])
        mock_client.put_register.assert_not_called()


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_kabu_put_register_skips_when_venue_is_none() -> None:
    """_kabu_venue=None のとき HTTP を呼ばない。"""
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._kabu_venue = None
    server._kabu_env = "verify"
    server._kabu_register_set = RegisterSet(max_symbols=50)

    mock_client = AsyncMock()
    with patch.object(server, "_make_kabu_rest_client", return_value=mock_client):
        await server._kabu_put_register([("7203", 1)])
        mock_client.put_register.assert_not_called()


# ---------------------------------------------------------------------------
# DataEngineServer._handle_subscribe_kabu_station テスト（Bug B）
# ---------------------------------------------------------------------------


def _make_subscribe_server() -> DataEngineServer:
    """_handle_subscribe_kabu_station テスト用の最小 DataEngineServer を返す。

    注意: このヘルパーと test_server_kabu_subscribe.py の _make_subscribe_server() は同期を保つこと。
    """
    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _StubOutbox()
    server._outbox_event = asyncio.Event()
    server._engine_session_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    server._stream_counter = 0
    server._streams: dict = {}
    server._workers = {}

    mock_venue = MagicMock()
    mock_venue.is_connected = True
    mock_venue._token = "test-session-token"
    server._kabu_venue = mock_venue
    server._kabu_env = "verify"

    register_set = MagicMock(spec=RegisterSet)
    register_set.all_symbols.return_value = [("7203", 1)]
    server._kabu_register_set = register_set

    server._kabu_push_ssid = "test-ssid"
    server._kabu_push_seq = 0
    server._kabu_adapter = MagicMock()
    return server


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_subscribe_kabu_station_calls_put_register_immediately() -> None:
    """Subscribe 後に _kabu_put_register が即座に呼ばれることを確認する。

    Regression: Bug B — Subscribe 後に PUT /register が呼ばれず、
    kabuStation が PUSH 配信対象として銘柄を知らないままだった。
    Fix: _handle_subscribe_kabu_station で register() 直後に _kabu_put_register を呼ぶ。
    """
    server = _make_subscribe_server()

    put_register_called_with: list[list[tuple[str, int]]] = []

    async def mock_put_register(symbols: list[tuple[str, int]]) -> bool:
        put_register_called_with.append(symbols)
        return True

    server._kabu_put_register = mock_put_register  # type: ignore[method-assign]

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    assert len(put_register_called_with) >= 1, (
        "_handle_subscribe_kabu_station() は subscribe 後に _kabu_put_register() を"
        " 呼ぶ必要があります（Bug B 未修正）。"
        " kabuStation は PUSH 配信対象を知らないままです。"
    )
    # all_symbols() の返り値が渡されていることを確認
    assert [("7203", 1)] in put_register_called_with, (
        "_kabu_put_register に _kabu_register_set.all_symbols() の結果を渡す必要があります。"
        f" 実際に渡された引数: {put_register_called_with}"
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_put_register_raises_on_http_error(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """put_register() が HTTP 4xx 時に KabuApiError を raise することを確認する。"""
    from engine.exchanges.kabusapi_auth import KabuApiError
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("register", env="verify"),
        status_code=400,
        json={"Code": 4001001, "Message": "Invalid parameter"},
    )
    rs = RegisterSet(max_symbols=50)
    client = KabuRestClient(token="test-token", env="verify", register_set=rs)
    with pytest.raises(KabuApiError):
        await client.put_register([("7203", 1)])


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_kabu_put_register_swallows_api_error(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """_kabu_put_register() が PUT /register の KabuApiError をログして握り潰すことを確認する。

    Regression: Fix H2 — 例外が WS タスクや subscribe ディスパッチループを破壊しないよう
    _kabu_put_register() 内でキャッチする。
    """
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("register", env="verify"),
        status_code=400,
        json={"Code": 4001001, "Message": "Invalid parameter"},
    )
    server = _make_server_with_kabu_venue()
    # 例外が上に伝播しないことを確認し、False が返ることをアサート（R2-H1）
    result = await server._kabu_put_register([("7203", 1)])
    assert result is False, "_kabu_put_register() は PUT 失敗時に False を返す必要があります"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_subscribe_kabu_station_put_register_not_called_on_unsupported_stream() -> None:
    """unsupported stream に対しては _kabu_put_register が呼ばれない。"""
    server = _make_subscribe_server()

    put_register_called = False

    async def mock_put_register(symbols: list[tuple[str, int]]) -> None:
        nonlocal put_register_called
        put_register_called = True

    server._kabu_put_register = mock_put_register  # type: ignore[method-assign]

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "unsupported_stream_type",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    assert not put_register_called, (
        "unsupported stream の場合は _kabu_put_register を呼ぶべきではありません。"
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_kabu_put_register_returns_false_on_error(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """_kabu_put_register() が PUT 失敗時に False を返すことを確認する（R2-H1）。"""
    httpx_mock.add_response(
        method="PUT",
        url=endpoint("register", env="verify"),
        status_code=400,
        json={"Code": 4001001, "Message": "Invalid parameter"},
    )
    server = _make_server_with_kabu_venue()
    result = await server._kabu_put_register([("7203", 1)])
    assert result is False, "_kabu_put_register() は PUT 失敗時に False を返す必要があります"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_subscribe_kabu_station_returns_error_on_put_register_failure() -> None:
    """PUT /register が失敗した場合、subscribe が Error を outbox に送ってセンチネルタスクを作らない（R2-H1）。"""
    server = _make_subscribe_server()

    async def mock_put_register_fail(symbols):
        return False

    server._kabu_put_register = mock_put_register_fail  # type: ignore[method-assign]
    server._kabu_register_set.all_symbols.return_value = [("7203", 1)]

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    # センチネルタスクが作られていないことを確認
    keys = list(server._streams.keys())
    assert not any(k[1] == "7203" for k in keys), (
        "PUT /register 失敗時にセンチネルタスクが作成されてはいけません"
    )
    # Error が outbox に送られていることを確認
    errors = [item for item in server._outbox._q if item.get("code") == "register_failed"]
    assert len(errors) == 1, "PUT /register 失敗時に register_failed Error を outbox に送る必要があります"


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_unsubscribe_kabu_station_calls_unregister_and_put_register() -> None:
    """Unsubscribe(venue='kabu_station') が _kabu_register_set.unregister() と
    _kabu_put_register() を呼ぶことを確認する（R2-M1）。"""
    server = _make_subscribe_server()

    # まず subscribe でストリームを登録する
    async def mock_put_register_success(symbols):
        return True

    server._kabu_put_register = mock_put_register_success  # type: ignore[method-assign]
    server._kabu_register_set.all_symbols.return_value = [("7203", 1)]

    sub_msg = {"venue": "kabu_station", "ticker": "7203", "stream": "depth", "market": "stock"}
    await server._handle_subscribe(sub_msg)

    # _kabu_put_register を追跡可能な mock に置き換え
    put_register_calls: list = []

    async def mock_put_register_track(symbols):
        put_register_calls.append(symbols)
        return True

    server._kabu_put_register = mock_put_register_track  # type: ignore[method-assign]

    # Unsubscribe
    unsub_msg = {"venue": "kabu_station", "ticker": "7203", "stream": "depth", "market": "stock"}
    await server._handle_unsubscribe(unsub_msg)

    # unregister が呼ばれたことを確認
    server._kabu_register_set.unregister.assert_called_once_with("7203", 1)

    # _kabu_put_register が呼ばれたことを確認
    assert len(put_register_calls) >= 1, (
        "Unsubscribe 後に _kabu_put_register() が呼ばれる必要があります（R2-M1）"
    )
