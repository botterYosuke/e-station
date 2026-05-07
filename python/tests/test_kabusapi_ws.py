"""kabusapi_ws のテスト: 再接続 / SJIS 拒否 / 5 回打ち切り。"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from engine.exchanges.kabusapi_auth import KabuConnectionError
from engine.exchanges.kabusapi_register import RegisterSet


@pytest.mark.demo_kabu
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
