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


# ---------------------------------------------------------------------------
# Bug C: _on_kabu_board_push が market="spot" を outbox に書くため
# Rust depth_stream が MarketKind::Stock="stock" と不一致でスキップしていた。
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_register_set_unexpected_exception_sends_error_to_outbox() -> None:
    """_kabu_register_set.register が予期しない例外を投げたとき outbox に Error が積まれることを確認する（H-2）。

    Regression: 予期しない Exception で return してしまうと、Rust は Subscribe 応答なしで宙ぶらりん。
    Fix: except Exception 節で outbox に Error{code='register_error'} を積む。
    """
    server = _make_subscribe_server()

    # register が予期しない例外を投げるよう設定
    server._kabu_register_set.register.side_effect = RuntimeError("unexpected db error")

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    errors = [item for item in server._outbox._q if item.get("code") == "register_error"]
    assert len(errors) == 1, (
        "register_set.register が予期しない例外を投げたとき outbox に register_error Error を送る必要があります（H-2）。"
        f" 実際の outbox: {list(server._outbox._q)}"
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_subscribe_kabu_station_not_connected_returns_error() -> None:
    """_kabu_venue.is_connected=False のとき Subscribe が kabu_not_connected Error を返すことを確認する（H-3）。

    Regression: 未接続時に _kabu_put_register が True を返し、
    センチネルタスクが作成されるがデータが来ない問題を防ぐ。
    Fix: _handle_subscribe_kabu_station の先頭で未接続チェックを行う。
    """
    server = _make_subscribe_server()
    server._kabu_venue.is_connected = False

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    errors = [item for item in server._outbox._q if item.get("code") == "kabu_not_connected"]
    assert len(errors) == 1, (
        "kabu が未接続のとき Subscribe は kabu_not_connected Error を返す必要があります（H-3）。"
        f" 実際の outbox: {list(server._outbox._q)}"
    )
    # センチネルタスクが作られていないことを確認
    assert len(server._streams) == 0, (
        "未接続時にセンチネルタスクが作成されてはいけません（H-3）。"
    )


@pytest.mark.demo_kabu
def test_on_kabu_board_push_emits_market_stock() -> None:
    """_on_kabu_board_push が outbox に market="stock" を書くことを確認する。

    Regression: Bug C — market 引数が省略されデフォルト "spot" が使われていたため、
    Rust depth_stream が ev_market="spot" != "stock" でイベントをスキップしていた。
    Fix: kabu_board_to_wire_dict(..., market="stock") を明示する。
    """
    from engine.exchanges.kabusapi_adapter import KabuStationAdapter

    server = _make_server_with_kabu_venue()
    server._kabu_adapter = KabuStationAdapter([])

    raw = {
        "Symbol": "7203",
        "CurrentPrice": 3000.0,
        "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
        "Buy1": {"Price": 2999.0, "Qty": 100, "Time": "2024-01-01T15:00:00+09:00", "Sign": "0101"},
        "Sell1": {"Price": 3001.0, "Qty": 100, "Time": "2024-01-01T15:00:00+09:00", "Sign": "0101"},
    }
    server._on_kabu_board_push(raw)

    items = list(server._outbox)
    # C-R2-1 修正後: _on_kabu_board_push は DepthSnapshot と Trades の 2 件を outbox に書く
    depth_items = [item for item in items if item.get("event") == "DepthSnapshot"]
    assert len(depth_items) == 1, (
        "_on_kabu_board_push は outbox に DepthSnapshot を 1 件書く必要があります"
    )
    assert depth_items[0].get("market") == "stock", (
        f"outbox の market は 'stock' である必要があります（Bug C 未修正: {depth_items[0].get('market')!r}）。\n"
        "Rust depth_stream は MarketKind::Stock → 'stock' で待機しているため、\n"
        "'spot' を送ると ev_market != 'stock' でスキップされ 'Waiting for data...' が永続します。\n"
        "Fix: server.py の _on_kabu_board_push で kabu_board_to_wire_dict(..., market='stock') を明示してください。"
    )


@pytest.mark.demo_kabu
def test_on_kabu_trade_push_emits_market_stock() -> None:
    """_on_kabu_trade_push が outbox に market="stock" を書くことを確認する。

    _on_kabu_board_push と同様に Bug C が存在する。
    Fix: kabu_execution_to_wire_dict(..., market="stock") を明示する。
    """
    from engine.exchanges.kabusapi_adapter import KabuStationAdapter

    server = _make_server_with_kabu_venue()
    server._kabu_adapter = KabuStationAdapter([])

    raw = {
        "Symbol": "7203",
        "CurrentPrice": 3000.0,
        "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
    }
    server._on_kabu_trade_push(raw)

    items = list(server._outbox)
    assert len(items) == 1, (
        "_on_kabu_trade_push は outbox に 1 件書く必要があります"
    )
    assert items[0].get("market") == "stock", (
        f"outbox の market は 'stock' である必要があります（Bug C 未修正: {items[0].get('market')!r}）。\n"
        "Rust trade_stream は MarketKind::Stock → 'stock' で待機しているため、\n"
        "'spot' を送るとイベントがスキップされます。"
    )


# ---------------------------------------------------------------------------
# C-R2-1: _on_kabu_board_push が _on_kabu_trade_push も呼ぶことを確認
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
def test_on_kabu_board_push_also_emits_trades() -> None:
    """_on_kabu_board_push(raw) を呼んだ後 outbox に event=="Trades" が存在することを確認する。

    Regression: C-R2-1 — kabusapi_ws.connect() は on_message=_on_kabu_board_push のみ渡され、
    _on_kabu_trade_push が呼ばれないため約定ストリームが無音になっていた。
    Fix: _on_kabu_board_push の処理完了後に _on_kabu_trade_push(raw) も呼ぶ。
    """
    from engine.exchanges.kabusapi_adapter import KabuStationAdapter

    server = _make_server_with_kabu_venue()
    server._kabu_adapter = KabuStationAdapter([])

    raw = {
        "Symbol": "7203",
        "CurrentPrice": 3000.0,
        "CurrentPriceTime": "2024-01-01T15:00:00+09:00",
        "Buy1": {"Price": 2999.0, "Qty": 100, "Time": "2024-01-01T15:00:00+09:00", "Sign": "0101"},
        "Sell1": {"Price": 3001.0, "Qty": 100, "Time": "2024-01-01T15:00:00+09:00", "Sign": "0101"},
    }
    server._on_kabu_board_push(raw)

    items = list(server._outbox)
    trade_items = [item for item in items if item.get("event") == "Trades"]
    assert len(trade_items) >= 1, (
        "_on_kabu_board_push は DepthSnapshot に加えて Trades も outbox に書く必要があります（C-R2-1）。\n"
        f"実際の outbox events: {[item.get('event') for item in items]}"
    )


# ---------------------------------------------------------------------------
# C-R2-2: _on_kabu_board_push の except で ticker が UnboundLocalError にならない
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
def test_on_kabu_board_push_non_dict_raw_does_not_raise() -> None:
    """raw=None を渡したとき例外が上に伝播しないことを確認する。

    Regression: C-R2-2 — ticker が try ブロック内で定義されているため、
    try ブロック開始直後に例外が発生すると except 節の ticker 参照が UnboundLocalError になる。
    Fix: ticker = "" を try ブロック外（前）で初期化する。
    """
    server = _make_server_with_kabu_venue()
    from engine.exchanges.kabusapi_adapter import KabuStationAdapter
    server._kabu_adapter = KabuStationAdapter([])

    # None は raw.get() を呼べないため try ブロック冒頭で AttributeError が発生する
    try:
        server._on_kabu_board_push(None)  # type: ignore[arg-type]
    except Exception as exc:
        raise AssertionError(
            f"_on_kabu_board_push(None) が例外を上に伝播させました（C-R2-2）: {exc!r}\n"
            "Fix: ticker = '' を try ブロックの外（前）で初期化してください。"
        ) from exc


# ---------------------------------------------------------------------------
# H-R2-1: _default_market("kabu_station") が "stock" を返すことを確認
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_handle_subscribe_kabu_station_default_market_is_stock() -> None:
    """market フィールドなしの Subscribe で _streams キーの market が "stock" であることを確認する。

    Regression: H-R2-1 — _default_market("kabu_station") が "linear_perp" を返し、
    market フィールドなしの Subscribe では IPC market フィールドが "linear_perp" になって
    Bug C と同じ「IPC market フィールド不一致」が再発する。
    Fix: _default_market で "kabu_station" の場合は "stock" を返す。
    """
    from unittest.mock import AsyncMock

    server = _make_subscribe_server()
    # PUT /register を no-op に差し替え（このテストは market フィールドの確認のみ）
    server._kabu_put_register = AsyncMock(return_value=True)  # type: ignore[method-assign]

    # market フィールドを省略した Subscribe メッセージ
    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        # "market" を意図的に省略
    }
    await server._handle_subscribe(msg)

    keys = list(server._streams.keys())
    kabu_keys = [k for k in keys if k[0] == "kabu_station" and k[1] == "7203"]
    assert len(kabu_keys) == 1, (
        f"kabu_station 7203 のストリームキーが _streams に登録されていません: {keys}"
    )
    stream_key = kabu_keys[0]
    # key = ("kabu_station", ticker, market, stream, None) の market は index 2
    actual_market = stream_key[2]
    assert actual_market == "stock", (
        f"_default_market('kabu_station') は 'stock' を返す必要があります（H-R2-1）。\n"
        f"実際の market: {actual_market!r}\n"
        "Fix: _default_market で 'kabu_station' の場合は 'stock' を返すよう分岐を追加してください。"
    )


# ---------------------------------------------------------------------------
# H-R2-2: except Exception が log.error + exc_info=True を使うことを確認
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_register_set_unexpected_exception_logs_error_level(caplog) -> None:
    """register_set.register が例外を投げたとき ログレベルが ERROR であることを確認する（H-R2-2）。

    Regression: H-R2-2 — except Exception ブロックが log.warning を使っており、
    ERROR レベルのログが記録されないため問題の深刻さが伝わらない。
    Fix: log.warning → log.error(..., exc_info=True) に変更する。
    """
    import logging
    server = _make_subscribe_server()
    server._kabu_register_set.register.side_effect = RuntimeError("unexpected db error")

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        "market": "stock",
    }
    with caplog.at_level(logging.ERROR, logger="engine.server"):
        await server._handle_subscribe(msg)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 1, (
        "register_set.register が例外を投げたとき ERROR レベルのログが記録される必要があります（H-R2-2）。\n"
        f"実際のログレコード: {[(r.levelname, r.message) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# H-R2-5: エラーパスで _stream_counter がインクリメントされないことを確認
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_register_error_does_not_increment_stream_counter() -> None:
    """register_set.register が例外を投げたとき _stream_counter が増加しないことを確認する（H-R2-5）。

    Regression: H-R2-5 — _stream_counter += 1 が register() 呼び出し前に実行されるため、
    エラーパスでもカウンターが増加する。
    Fix: _stream_counter += 1 を register() 成功確認後に移動する。
    """
    server = _make_subscribe_server()
    server._kabu_register_set.register.side_effect = RuntimeError("unexpected db error")

    initial_counter = server._stream_counter

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    assert server._stream_counter == initial_counter, (
        f"エラーパスで _stream_counter が増加してはいけません（H-R2-5）。\n"
        f"initial={initial_counter}, actual={server._stream_counter}"
    )


# ---------------------------------------------------------------------------
# M-R2-1: KabuRegisterFullError のテスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_subscribe_kabu_station_register_full_sends_error_to_outbox() -> None:
    """_kabu_register_set.register が KabuRegisterFullError を raise したとき
    outbox に {"event": "Error", "code": "register_full"} が入ることを確認する（M-R2-1）。
    """
    from engine.exchanges.kabusapi_auth import KabuRegisterFullError

    server = _make_subscribe_server()
    server._kabu_register_set.register.side_effect = KabuRegisterFullError("50 symbols already registered")

    msg = {
        "venue": "kabu_station",
        "ticker": "9999",
        "stream": "depth",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    errors = [item for item in server._outbox._q if item.get("code") == "register_full"]
    assert len(errors) == 1, (
        "KabuRegisterFullError 時に outbox に register_full Error を送る必要があります（M-R2-1）。\n"
        f"実際の outbox: {list(server._outbox._q)}"
    )


# ---------------------------------------------------------------------------
# M-R2-2: _kabu_venue is None のテスト
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_subscribe_kabu_station_venue_none_returns_error() -> None:
    """server._kabu_venue = None の状態で Subscribe すると
    outbox に {"event": "Error", "code": "kabu_not_connected"} が入ることを確認する（M-R2-2）。
    """
    server = _make_subscribe_server()
    server._kabu_venue = None

    msg = {
        "venue": "kabu_station",
        "ticker": "7203",
        "stream": "depth",
        "market": "stock",
    }
    await server._handle_subscribe(msg)

    errors = [item for item in server._outbox._q if item.get("code") == "kabu_not_connected"]
    assert len(errors) == 1, (
        "_kabu_venue=None のとき Subscribe は kabu_not_connected Error を返す必要があります（M-R2-2）。\n"
        f"実際の outbox: {list(server._outbox._q)}"
    )


# ---------------------------------------------------------------------------
# M-R3-1: _on_kabu_board_push で非 dict raw の二重エラーログ防止
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
def test_on_kabu_board_push_non_dict_raw_does_not_call_trade_push() -> None:
    """raw=None のとき _on_kabu_trade_push が呼ばれないことを確認する（M-R3-1）。

    Regression: _on_kabu_board_push の try ブロックで raw が dict でない場合、
    except でエラーログを出した後に末尾の _on_kabu_trade_push(raw) が呼ばれ、
    _on_kabu_trade_push もまた except でエラーログを出す。同一エラーが2回記録される。
    Fix: 関数先頭に `if not isinstance(raw, dict): log.error(...); return` を追加する。
    """
    from engine.exchanges.kabusapi_adapter import KabuStationAdapter

    server = _make_server_with_kabu_venue()
    server._kabu_adapter = KabuStationAdapter([])

    trade_push_calls: list = []
    original_trade_push = server._on_kabu_trade_push

    def _spy_trade_push(raw):
        trade_push_calls.append(raw)
        return original_trade_push(raw)

    server._on_kabu_trade_push = _spy_trade_push  # type: ignore[method-assign]

    # raw=None を渡す（dict でない）
    server._on_kabu_board_push(None)  # type: ignore[arg-type]

    assert len(trade_push_calls) == 0, (
        "raw が dict でない場合、_on_kabu_trade_push は呼ばれてはいけません（M-R3-1）。\n"
        "同一エラーが2回記録される二重ログが発生します。\n"
        "Fix: _on_kabu_board_push の先頭に isinstance チェックとアーリーリターンを追加してください。\n"
        f"実際の呼び出し回数: {len(trade_push_calls)}"
    )


# ---------------------------------------------------------------------------
# M-R3-3: _default_market("tachibana") が "stock" を返す
# ---------------------------------------------------------------------------


@pytest.mark.demo_kabu
def test_default_market_tachibana_is_stock() -> None:
    """_default_market("tachibana") が "stock" を返すことを確認する（M-R3-3）。

    Regression: Tachibana は東証現物株を扱う。_default_market("kabu_station") は
    R2 で "stock" に修正されたが "tachibana" は "linear_perp" のまま。
    Fix: _default_market で "tachibana" も "stock" を返すよう分岐を拡張する。
    """
    from engine.server import _default_market

    result = _default_market("tachibana")
    assert result == "stock", (
        f"_default_market('tachibana') は 'stock' を返す必要があります（M-R3-3）。\n"
        f"実際の値: {result!r}\n"
        "Rust の depth_stream は market_kind_to_ipc(MarketKind::Stock) = 'stock' を期待しており、\n"
        "'linear_perp' が返ると IPC market フィールド不一致で Bug C と同型の不具合が発生します。"
    )


@pytest.mark.demo_kabu
def test_market_from_msg_tachibana_no_market_field_defaults_to_stock() -> None:
    """market フィールドなしの Subscribe で tachibana の market が "stock" であることを確認（M-R3-3）。"""
    from engine.server import _market_from_msg

    result = _market_from_msg({"event": "Subscribe"}, "tachibana")
    assert result == "stock", (
        f"_market_from_msg({{'event': 'Subscribe'}}, 'tachibana') は 'stock' を返す必要があります（M-R3-3）。\n"
        f"実際の値: {result!r}"
    )
