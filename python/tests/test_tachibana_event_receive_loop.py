"""Group F: tachibana_event.py receive_loop + _parse_p_od_to_utc_ms テスト。

F-1: サーバーが正常クローズした場合に reconnect_fn が呼ばれること。
F-2: p_OD に不正な値を渡したとき ts_event_ms が 0 ではなく現在時刻に近い値であること。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# F-2: _parse_p_od_to_utc_ms の不正値フォールバック
# ---------------------------------------------------------------------------


class TestParsePOdFallback:
    def test_invalid_p_od_returns_current_time_not_zero(self):
        """不正な p_OD（14 文字以上だが不正フォーマット）→ 0 ではなく現在時刻に近い値を返す。"""
        from engine.exchanges.tachibana_event import _parse_p_od_to_utc_ms

        before = int(time.time() * 1000)
        # 14 文字以上あるが strptime では解析できないもの
        result = _parse_p_od_to_utc_ms("NOTADATESTRING")
        after = int(time.time() * 1000)

        assert result != 0, "invalid p_OD must not return 0"
        assert before <= result <= after + 100, (
            f"result {result} should be close to current time [{before}, {after}]"
        )

    def test_empty_p_od_returns_zero(self):
        """空文字は長さ < 14 で早期リターン → 0。"""
        from engine.exchanges.tachibana_event import _parse_p_od_to_utc_ms

        assert _parse_p_od_to_utc_ms("") == 0

    def test_valid_p_od_returns_nonzero(self):
        """有効な p_OD は正常変換される。"""
        from engine.exchanges.tachibana_event import _parse_p_od_to_utc_ms

        result = _parse_p_od_to_utc_ms("20240101090000")
        assert result > 0


# ---------------------------------------------------------------------------
# F-1: 正常クローズ後に reconnect_fn が呼ばれること
# ---------------------------------------------------------------------------


class _NormalCloseWs:
    """AsyncIterator として振る舞い、1 フレームを yield してすぐ終了する疑似 WebSocket。"""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class TestReceiveLoopNormalClose:
    @pytest.mark.asyncio
    async def test_normal_close_calls_reconnect_fn(self):
        """サーバーが正常クローズした後に reconnect_fn が呼ばれること。"""
        from engine.exchanges.tachibana_event import TachibanaEventClient

        client = TachibanaEventClient()

        reconnect_ws = _NormalCloseWs()
        reconnect_fn = AsyncMock(return_value=reconnect_ws)
        on_event = AsyncMock()

        # 元の ws も正常クローズする疑似 WebSocket
        ws = _NormalCloseWs()

        # max_retries=1 + base_backoff=0 で即終了させる
        await client.receive_loop(
            ws,
            on_event,
            reconnect_fn=reconnect_fn,
            max_retries=1,
            base_backoff=0.0,
        )

        reconnect_fn.assert_called()

    @pytest.mark.asyncio
    async def test_no_reconnect_fn_does_not_reconnect(self):
        """reconnect_fn=None のとき正常クローズ後に再接続しない（従来動作）。"""
        from engine.exchanges.tachibana_event import TachibanaEventClient

        client = TachibanaEventClient()
        on_event = AsyncMock()
        ws = _NormalCloseWs()

        # reconnect_fn=None → 即終了、例外なし
        await client.receive_loop(ws, on_event, reconnect_fn=None)
        # Just assert it completed without error


# ---------------------------------------------------------------------------
# M-2: reconnect_fn が 1 回失敗して 2 回目に成功した場合の retry_count 二重インクリメント修正
# ---------------------------------------------------------------------------


class _ErrorWs:
    """AsyncIterator として振る舞い、イテレーション開始時に例外を raise する疑似 WebSocket。"""

    def __init__(self):
        self.iterate_count = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.iterate_count += 1
        raise ConnectionError("ws disconnected")


class TestReceiveLoopRetryCount:
    @pytest.mark.asyncio
    async def test_reconnect_failure_does_not_reiterate_stale_ws(self):
        """reconnect_fn が失敗したとき、stale な current_ws を再イテレーションしないこと。

        バグ再現シナリオ（修正前）:
            1. 元の ws が切断 → except Exception → retry_count = 1
            2. reconnect_fn() が例外 → current_ws は stale のまま
            3. ループ先頭に戻り、stale ws を async for → 即例外（余分な受信エラーログ）
            4. また except Exception → retry_count += 1（二重インクリメント）

        修正後: reconnect_fn 失敗後は stale ws に戻らず直接再試行する。
        初期 ws は 1 回だけイテレーションされ、2 回目はない。
        """
        from engine.exchanges.tachibana_event import TachibanaEventClient

        client = TachibanaEventClient()
        on_event = AsyncMock()

        # 元の ws: すぐに切断（ConnectionError）。イテレーション回数を追跡する。
        ws = _ErrorWs()

        # reconnect_fn: 1 回目は例外、2 回目は正常クローズ ws を返す
        success_ws = _NormalCloseWs()
        reconnect_call_count = 0

        async def _reconnect_fn():
            nonlocal reconnect_call_count
            reconnect_call_count += 1
            if reconnect_call_count == 1:
                raise ConnectionError("reconnect failed")
            return success_ws

        # max_retries=2 で実行
        await client.receive_loop(
            ws,
            on_event,
            reconnect_fn=_reconnect_fn,
            max_retries=2,
            base_backoff=0.0,
        )

        # 修正後: 元の ws は最初の 1 回のみイテレーションされる（stale ws の再イテレーションなし）
        # バグがある場合: reconnect_fn 失敗後にループ先頭に戻り、
        # stale ws が再びイテレーションされるため ws.iterate_count が 2 以上になる
        assert ws.iterate_count == 1, (
            f"初期 ws は 1 回だけイテレーションされるべきだが {ws.iterate_count} 回イテレーションされた"
            f"（バグがある場合は reconnect 失敗後に stale ws が再イテレーションされる）"
        )
