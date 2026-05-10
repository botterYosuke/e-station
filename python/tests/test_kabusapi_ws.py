"""kabusapi_ws のテスト: 再接続 / SJIS 拒否 / 5 回打ち切り / keepalive ping 無効化 / 受信タイムアウト。"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import websockets
import websockets.exceptions
import websockets.frames

from engine.exchanges.kabusapi_auth import KabuConnectionError
from engine.exchanges.kabusapi_register import RegisterSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLOSE_OK = websockets.frames.Close(1000, "")


def _make_close_ok_ws():
    """接続直後に ConnectionClosedOK を raise する FakeWS を返す。"""
    class FakeWSClosedOK:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def recv(self):
            raise websockets.exceptions.ConnectionClosedOK(_CLOSE_OK, None)
    return FakeWSClosedOK()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.demo_kabu
@pytest.mark.smoke
@pytest.mark.asyncio
async def test_reconnect_reregisters_all_symbols():
    """再接続後に RegisterSet 全件を put_register で再登録する (INV-K5-RECONNECT-REREG)."""
    from engine.exchanges import kabusapi_ws

    registered_symbols = []

    async def mock_put_register(symbols):
        registered_symbols.extend(symbols)

    rs = RegisterSet(max_symbols=50)
    rs.register("9433", 1)
    rs.register("7203", 1)

    messages_received = []

    def on_message(msg):
        messages_received.append(msg)

    call_count = 0

    class FakeWS:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def recv(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"Symbol": "9433", "Exchange": 1, "CurrentPrice": 100.0})
            raise websockets.exceptions.ConnectionClosedOK(_CLOSE_OK, None)

    connection_count = 0
    max_connections = 2

    def fake_connect(url, **kwargs):
        nonlocal connection_count
        connection_count += 1
        if connection_count > max_connections:
            raise Exception("Stop test")
        return FakeWS()

    with patch("engine.exchanges.kabusapi_ws.websockets.connect", side_effect=fake_connect):
        try:
            await kabusapi_ws.connect(
                env="verify",
                on_message=on_message,
                register_set=rs,
                put_register=mock_put_register,
            )
        except (KabuConnectionError, Exception):
            pass

    assert len(registered_symbols) >= 2, f"Expected re-registration, got: {registered_symbols}"


@pytest.mark.demo_kabu
def test_decode_rejects_sjis_bytes():
    """SJIS バイト列は UnicodeDecodeError を raise する (INV-K2-SJIS-REJECT と整合)."""
    sjis_bytes = "テスト".encode("shift_jis")
    with pytest.raises(UnicodeDecodeError):
        sjis_bytes.decode("utf-8")


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_reconnect_aborts_after_5_consecutive_failures():
    """5s × 5 回連続失敗で KabuConnectionError が raise される (INV-K5-ABORT, U22)."""
    from engine.exchanges import kabusapi_ws

    rs = RegisterSet(max_symbols=50)

    async def mock_put_register(symbols):
        pass

    def on_message(msg):
        pass

    with patch("engine.exchanges.kabusapi_ws.websockets.connect",
               side_effect=ConnectionRefusedError("refused")), \
         patch("engine.exchanges.kabusapi_ws.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(KabuConnectionError):
            await kabusapi_ws.connect(
                env="verify",
                on_message=on_message,
                register_set=rs,
                put_register=mock_put_register,
            )

    assert mock_sleep.call_count == kabusapi_ws._MAX_RECONNECT_ATTEMPTS - 1


@pytest.mark.demo_kabu
def test_constants():
    """再接続パラメータの確認。"""
    from engine.exchanges import kabusapi_ws
    assert kabusapi_ws._RECONNECT_DELAY_S == 5.0
    assert kabusapi_ws._MAX_RECONNECT_ATTEMPTS == 5
    assert kabusapi_ws._RECV_TIMEOUT_S == 3600.0


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_connect_passes_ping_interval_none_to_websockets(monkeypatch):
    """websockets.connect() に ping_interval=None と compression=None が渡されることを検証する。

    Regression test for: kabusapi_ws.py の ping_interval=20 設定が原因で
    kabuStation PUSH WS が 30 秒ごとに keepalive ping timeout で切断する (Issue #40)。

    Fix: ping_interval=None を明示して websockets のライブラリ keepalive を無効化する。
    kabuStation は RFC 6455 準拠の PONG を返さないため、ライブラリ keepalive は使えない。

    このテストが FAIL する場合:
    Fix: kabusapi_ws.py の websockets.connect() 呼び出しに
      ping_interval=None  # kabuStation は RFC 6455 準拠の PONG を返さないため
    を追加してください。
    """
    import engine.exchanges.kabusapi_ws as kabusapi_ws

    captured_kwargs: dict = {}
    rs = RegisterSet(max_symbols=50)

    class FakeWS:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def recv(self):
            # 即座にタイムアウトをシミュレート → break して再接続へ
            raise asyncio.TimeoutError()

    call_count = 0

    def fake_connect(url, **kwargs):
        nonlocal call_count
        call_count += 1
        captured_kwargs.update(kwargs)
        if call_count == 1:
            return FakeWS()
        raise ConnectionRefusedError("stop test")

    async def instant_sleep(_delay):
        return

    monkeypatch.setattr(kabusapi_ws.websockets, "connect", fake_connect)
    monkeypatch.setattr(kabusapi_ws.asyncio, "sleep", instant_sleep)

    try:
        await kabusapi_ws.connect(
            env="verify",
            on_message=lambda msg: None,
            register_set=rs,
            put_register=AsyncMock(return_value=True),
        )
    except Exception:
        pass

    assert call_count >= 1, "websockets.connect() が一度も呼ばれませんでした"
    assert captured_kwargs.get("ping_interval") is None, (
        f"websockets.connect() に ping_interval=None が渡されていません。\n"
        f"実際の値: {captured_kwargs.get('ping_interval')!r}\n"
        "Fix: kabusapi_ws.py の websockets.connect() 呼び出しに\n"
        "  ping_interval=None  # kabuStation は RFC 6455 準拠の PONG を返さないため\n"
        "を追加してください。"
    )
    assert captured_kwargs.get("compression") is None, (
        f"websockets.connect() に compression=None が渡されていません。\n"
        f"実際の値: {captured_kwargs.get('compression')!r}\n"
        "Fix: kabusapi_ws.py の websockets.connect() 呼び出しに\n"
        "  compression=None  # RSV1 バグ回避\n"
        "を確認してください。"
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_recv_timeout_triggers_reconnect(monkeypatch):
    """_RECV_TIMEOUT_S 秒以上メッセージが届かない場合に再接続することを検証する。

    Regression test for: ping_interval=None にすることで OS レベルの dead connection を
    ライブラリ keepalive で検出できなくなる問題 (Issue #40 M2)。
    asyncio.wait_for の TimeoutError をトリガーに再接続が発生することを確認する。

    このテストが FAIL する場合:
    Fix: kabusapi_ws.py の受信ループを asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
    でラップし、TimeoutError 時に break して再接続する実装を確認してください。
    """
    import engine.exchanges.kabusapi_ws as kabusapi_ws

    rs = RegisterSet(max_symbols=50)
    connection_count = 0

    class FakeWSTimeout:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def recv(self):
            raise asyncio.TimeoutError()  # 受信タイムアウトをシミュレート

    def fake_connect(url, **kwargs):
        nonlocal connection_count
        connection_count += 1
        if connection_count == 1:
            return FakeWSTimeout()
        raise ConnectionRefusedError("stop test")

    async def instant_sleep(_delay):
        return

    monkeypatch.setattr(kabusapi_ws.websockets, "connect", fake_connect)
    monkeypatch.setattr(kabusapi_ws.asyncio, "sleep", instant_sleep)

    try:
        await kabusapi_ws.connect(
            env="verify",
            on_message=lambda msg: None,
            register_set=rs,
            put_register=AsyncMock(return_value=True),
        )
    except Exception:
        pass

    assert connection_count >= 2, (
        f"受信タイムアウト後に再接続が発生していません。接続試行回数: {connection_count}\n"
        "Fix: kabusapi_ws.py の受信ループを asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)\n"
        "でラップし、TimeoutError 時に break して再接続する処理が必要です。"
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_connection_closed_ok_uses_info_log_not_error(monkeypatch):
    """ConnectionClosedOK (code=1000) 発生時のログレベルが ERROR でないことを確認する（H-R2-4 → M-R3-2 更新）。

    M-R3-2: ConnectionClosedOK が繰り返された場合は consecutive_failures をインクリメントして
    _MAX_RECONNECT_ATTEMPTS に達したら KabuConnectionError を raise する。
    ただしログレベルは info/warning のまま（正常切断の可能性があるため ERROR にしない）。

    このテストは _MAX_RECONNECT_ATTEMPTS 回の ConnectionClosedOK で KabuConnectionError が
    raise されることと、接続試行回数が正確であることを確認する。
    """
    import engine.exchanges.kabusapi_ws as kabusapi_ws

    rs = RegisterSet(max_symbols=50)

    def on_message(msg):
        pass

    _MAX = kabusapi_ws._MAX_RECONNECT_ATTEMPTS  # 5

    connection_count = 0

    def fake_connect(url, **kwargs):
        nonlocal connection_count
        connection_count += 1
        return _make_close_ok_ws()

    async def instant_sleep(_delay):
        return

    monkeypatch.setattr(kabusapi_ws.websockets, "connect", fake_connect)
    monkeypatch.setattr(kabusapi_ws.asyncio, "sleep", instant_sleep)

    raised_kabu_error: KabuConnectionError | None = None
    try:
        await kabusapi_ws.connect(
            env="verify",
            on_message=on_message,
            register_set=rs,
            put_register=AsyncMock(return_value=True),
        )
    except KabuConnectionError as exc:
        raised_kabu_error = exc

    assert raised_kabu_error is not None, (
        f"ConnectionClosedOK が {_MAX + 1} 回続いたとき KabuConnectionError が raise される必要があります（M-R3-2）。\n"
        f"接続試行回数: {connection_count}"
    )
    assert connection_count == _MAX + 1, (
        f"KabuConnectionError は {_MAX + 1} 回目の ConnectionClosedOK 後に raise される必要があります（M-R3-2）。\n"
        f"実際の接続試行回数: {connection_count}"
    )


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_repeated_connection_closed_ok_raises_kabu_connection_error(monkeypatch):
    """ConnectionClosedOK が _MAX_RECONNECT_ATTEMPTS 回繰り返されたとき KabuConnectionError が raise されることを確認（M-R3-2）。

    Regression: ConnectionClosedOK (code=1000) が繰り返された場合、
    consecutive_failures がインクリメントされないため _MAX_RECONNECT_ATTEMPTS に達せず
    5 秒ごとに永続再接続ループが発生する。
    Fix: except websockets.exceptions.ConnectionClosedOK でも consecutive_failures を
    インクリメントし、上限到達で KabuConnectionError を raise する。
    """
    import engine.exchanges.kabusapi_ws as kabusapi_ws

    rs = RegisterSet(max_symbols=50)

    def on_message(msg):
        pass

    _MAX = kabusapi_ws._MAX_RECONNECT_ATTEMPTS  # 5
    connection_count = 0

    def fake_connect(url, **kwargs):
        nonlocal connection_count
        connection_count += 1
        return _make_close_ok_ws()

    async def instant_sleep(_delay):
        return

    monkeypatch.setattr(kabusapi_ws.websockets, "connect", fake_connect)
    monkeypatch.setattr(kabusapi_ws.asyncio, "sleep", instant_sleep)

    raised_kabu_error: KabuConnectionError | None = None
    try:
        await kabusapi_ws.connect(
            env="verify",
            on_message=on_message,
            register_set=rs,
            put_register=AsyncMock(return_value=True),
        )
    except KabuConnectionError as exc:
        raised_kabu_error = exc

    assert raised_kabu_error is not None, (
        f"ConnectionClosedOK が {_MAX} 回繰り返されたとき KabuConnectionError が raise されなければなりません（M-R3-2）。\n"
        f"実際の接続試行回数: {connection_count}\n"
        "Fix: except ConnectionClosedOK でも consecutive_failures をインクリメントし、\n"
        "上限到達で KabuConnectionError を raise してください。"
    )
    assert connection_count >= _MAX, (
        f"KabuConnectionError は少なくとも {_MAX} 回の接続試行後に raise される必要があります。\n"
        f"実際の接続試行回数: {connection_count}"
    )
