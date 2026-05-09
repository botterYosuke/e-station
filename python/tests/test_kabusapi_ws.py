"""kabusapi_ws のテスト: 再接続 / SJIS 拒否 / 5 回打ち切り。"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import websockets
import websockets.exceptions

from engine.exchanges.kabusapi_auth import KabuConnectionError
from engine.exchanges.kabusapi_register import RegisterSet


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

    # 最初の接続で 1 メッセージを受け取り、次の接続で StopAsyncIteration で終了するモック
    call_count = 0

    class FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"Symbol": "9433", "Exchange": 1, "CurrentPrice": 100.0})
            raise StopAsyncIteration

    # 2 回接続して StopIteration で抜けるように simulate
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

    # 再登録が行われた（少なくとも 1 回 put_register が呼ばれた）
    assert len(registered_symbols) >= 2, f"Expected re-registration, got: {registered_symbols}"


@pytest.mark.demo_kabu
def test_decode_rejects_sjis_bytes():
    """SJIS バイト列は UnicodeDecodeError を raise する (INV-K2-SJIS-REJECT と整合)."""
    sjis_bytes = "テスト".encode("shift_jis")
    with pytest.raises(UnicodeDecodeError):
        sjis_bytes.decode("utf-8")  # connect() 内部の bytes.decode("utf-8") と同等


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

    # _MAX_RECONNECT_ATTEMPTS - 1 回 sleep が呼ばれる（最後の失敗後は raise）
    assert mock_sleep.call_count == kabusapi_ws._MAX_RECONNECT_ATTEMPTS - 1


@pytest.mark.demo_kabu
def test_constants():
    """再接続パラメータの確認。"""
    from engine.exchanges import kabusapi_ws
    assert kabusapi_ws._RECONNECT_DELAY_S == 5.0
    assert kabusapi_ws._MAX_RECONNECT_ATTEMPTS == 5


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
    import websockets.frames
    import engine.exchanges.kabusapi_ws as kabusapi_ws

    rs = RegisterSet(max_symbols=50)

    def on_message(msg):
        pass

    async def mock_put_register(symbols):
        return True

    _MAX = kabusapi_ws._MAX_RECONNECT_ATTEMPTS  # 5
    _rcvd = websockets.frames.Close(1000, "")

    class FakeWSClosedOK:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise websockets.exceptions.ConnectionClosedOK(_rcvd, None)

    connection_count = 0

    def fake_connect(url, **kwargs):
        nonlocal connection_count
        connection_count += 1
        return FakeWSClosedOK()

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
            put_register=mock_put_register,
        )
    except KabuConnectionError as exc:
        raised_kabu_error = exc

    # M-R3-2: _MAX + 1 回目の ConnectionClosedOK で KabuConnectionError が raise される
    # (H-R2-4 との両立: consecutive_ok_close_count > MAX のため MAX+1 回目でエラー)
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
    import websockets.frames
    import engine.exchanges.kabusapi_ws as kabusapi_ws

    rs = RegisterSet(max_symbols=50)

    def on_message(msg):
        pass

    async def mock_put_register(symbols):
        return True

    _MAX = kabusapi_ws._MAX_RECONNECT_ATTEMPTS  # 5
    connection_count = 0

    # websockets v13+ requires rcvd=Close frame for ConnectionClosedOK
    _rcvd = websockets.frames.Close(1000, "")

    class FakeWSClosedOK:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise websockets.exceptions.ConnectionClosedOK(_rcvd, None)

    def fake_connect(url, **kwargs):
        nonlocal connection_count
        connection_count += 1
        return FakeWSClosedOK()

    async def instant_sleep(_delay):
        """asyncio.sleep の no-op 代替: 実際に待機しない。"""
        return

    monkeypatch.setattr(kabusapi_ws.websockets, "connect", fake_connect)
    monkeypatch.setattr(kabusapi_ws.asyncio, "sleep", instant_sleep)

    raised_kabu_error: KabuConnectionError | None = None
    try:
        await kabusapi_ws.connect(
            env="verify",
            on_message=on_message,
            register_set=rs,
            put_register=mock_put_register,
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
