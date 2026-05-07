"""kabuステーション WebSocket PUSH 接続モジュール。

再接続後は RegisterSet 全件を PUT /register で再登録する (U6)。
再接続連続失敗の打ち切りは 5s × 5 回 (U22)。
上限到達で reconnect ループを抜け KabuConnectionError を raise する。

ping/pong: library 任せ (ping_interval=20, ping_timeout=10)。
メッセージ: WebSocket frame 単位で 1 JSON (UTF-8)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets

from engine.exchanges.kabusapi_auth import KabuConnectionError
from engine.exchanges.kabusapi_register import RegisterSet
from engine.exchanges.kabusapi_url import KabuEnv, ws_url

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_S = 5.0
_MAX_RECONNECT_ATTEMPTS = 5


MessageHandler = Callable[[dict], Awaitable[None] | None]


async def connect(
    *,
    env: KabuEnv,
    on_message: MessageHandler,
    register_set: RegisterSet,
    put_register: Callable[[list[tuple[str, int]]], Awaitable[None]],
) -> None:
    """WebSocket に接続して受信ループを実行する。

    切断時は register_set 全件を再登録してから再接続する。
    5s × 5 回連続失敗で KabuConnectionError を raise する。

    Args:
        env: "prod" or "verify"
        on_message: メッセージ受信時に呼ぶコールバック
        register_set: PUSH 銘柄登録セット
        put_register: 再登録を実行する非同期コールバック
    """
    url = ws_url(env)
    consecutive_failures = 0

    while True:
        try:
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=10,
                compression=None,  # kabuStation が permessage-deflate を受け入れた場合の RSV1 バグ回避
            ) as ws:
                consecutive_failures = 0
                # 再接続後は全件 re-register (U6)
                symbols = register_set.all_symbols()
                if symbols:
                    await put_register(symbols)

                async for raw in ws:
                    if isinstance(raw, bytes):
                        # SJIS バイト列拒否 (INV-K2-SJIS-REJECT と整合)
                        text = raw.decode("utf-8")
                    else:
                        text = raw
                    msg = json.loads(text)
                    result = on_message(msg)
                    if asyncio.iscoroutine(result):
                        await result

        except (
            OSError,
            ConnectionRefusedError,
        ):
            consecutive_failures += 1
            if consecutive_failures >= _MAX_RECONNECT_ATTEMPTS:
                logger.error(
                    "kabu WS: reconnect aborted after %d consecutive failures",
                    _MAX_RECONNECT_ATTEMPTS,
                )
                raise KabuConnectionError(
                    0,
                    f"WebSocket reconnect failed {_MAX_RECONNECT_ATTEMPTS} times",
                )
            logger.warning(
                "kabu WS: connection failed (attempt %d/%d), retrying in %ss",
                consecutive_failures, _MAX_RECONNECT_ATTEMPTS, _RECONNECT_DELAY_S,
            )
            await asyncio.sleep(_RECONNECT_DELAY_S)

        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures >= _MAX_RECONNECT_ATTEMPTS:
                logger.error("kabu WS: reconnect aborted: %s", exc)
                raise KabuConnectionError(0, str(exc)) from exc
            logger.warning("kabu WS: disconnected (%s), reconnecting...", exc)
            await asyncio.sleep(_RECONNECT_DELAY_S)
