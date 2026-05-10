"""kabuステーション WebSocket PUSH 接続モジュール。

再接続後は RegisterSet 全件を PUT /register で再登録する (U6)。
再接続連続失敗の打ち切りは 5s × 5 回 (U22)。
上限到達で reconnect ループを抜け KabuConnectionError を raise する。

ping/pong: 無効化 (ping_interval=None)。kabuStation は RFC 6455 準拠の PONG を返さない
（PING payload と不一致の空 PONG を返す）ため、ライブラリの keepalive は使えない。
ping_interval=None を指定すると websockets は keepalive coroutine を起動しないため
ping_timeout は参照されない（明示的に渡す必要なし）。
接続の生存確認: _RECV_TIMEOUT_S 秒以上メッセージが届かない場合に再接続する (Issue #40)。
メッセージ: WebSocket frame 単位で 1 JSON (UTF-8)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import websockets
import websockets.exceptions

from engine.exchanges.kabusapi_auth import KabuConnectionError
from engine.exchanges.kabusapi_register import RegisterSet
from engine.exchanges.kabusapi_url import KabuEnv, ws_url

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_S = 5.0
_MAX_RECONNECT_ATTEMPTS = 5
_RECV_TIMEOUT_S = 3600.0  # 1h 無メッセージで再接続（OS レベル dead connection を検出）


MessageHandler = Callable[[dict], Awaitable[None] | None]


async def connect(
    *,
    env: KabuEnv,
    on_message: MessageHandler,
    register_set: RegisterSet,
    put_register: Callable[[list[tuple[str, int]]], Awaitable[bool]],
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
    # H-R2-4 / M-R3-2: consecutive_failures とは独立したカウンタ。
    # async with 内でリセットすると ConnectionClosedOK が続いても毎回 0 に戻るため、
    # 実メッセージ受信時にのみリセットし、> MAX で永久切断と判定する。
    consecutive_ok_close_count = 0

    while True:
        try:
            async with websockets.connect(
                url,
                ping_interval=None,  # kabuStation は RFC 6455 準拠の PONG を返さないため無効化 (Issue #40)
                compression=None,  # kabuStation が permessage-deflate を受け入れた場合の RSV1 バグ回避
            ) as ws:
                consecutive_failures = 0
                # 再接続後は全件 re-register (U6)
                symbols = register_set.all_symbols()
                if symbols:
                    if not await put_register(symbols):
                        logger.warning("kabusapi_ws: put_register failed after reconnect (%d symbols)", len(symbols))

                # asyncio.wait_for で受信タイムアウトを設ける。
                # _RECV_TIMEOUT_S 秒以上メッセージが来なければ break → 再接続。
                # ws.recv() が ConnectionClosedOK/Error を raise した場合は外側の except に伝播。
                # (websockets 16.0 の __aiter__ は ConnectionClosedOK を StopAsyncIteration に
                #  変換するため async for では except ConnectionClosedOK に到達しない。
                #  ws.recv() を直接呼び出すことで例外を正しく伝播させる。)
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
                    except TimeoutError:
                        logger.warning(
                            "kabu WS: no message for %.0fs, reconnecting...",
                            _RECV_TIMEOUT_S,
                        )
                        break
                    consecutive_ok_close_count = 0  # 実メッセージ受信 → 正常接続とみなしリセット
                    if isinstance(raw, bytes):
                        # SJIS バイト列拒否 (INV-K2-SJIS-REJECT と整合)
                        text = raw.decode("utf-8")
                    else:
                        text = raw
                    msg = json.loads(text)
                    result = on_message(msg)
                    if asyncio.iscoroutine(result):
                        await result

            # async with が正常終了（_RECV_TIMEOUT_S による break）→ sleep して再接続。
            # __aexit__ がクローズ中に ConnectionClosedError を raise した場合は
            # 外側の except ConnectionClosedError に捕捉されて consecutive_failures がインクリメントされる。
            # これは意図通り（クローズ失敗はネットワーク障害とみなす）。
            await asyncio.sleep(_RECONNECT_DELAY_S)

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

        except websockets.exceptions.ConnectionClosedOK as exc:
            # H-R2-4: consecutive_failures はインクリメントしない（正常切断は失敗ではない）。
            # M-R3-2: 別カウンタで追跡し > MAX（= MAX+1 回目）で永久切断と判定してループを打ち切る。
            # > MAX（6 回目）と >= MAX（5 回目）の非対称は意図的:
            # ConnectionClosedError は「明らかな失敗」として 5 回で打ち切るが、
            # ConnectionClosedOK は「サーバー側の都合による正常終了」として 1 回余裕を持たせる。
            consecutive_ok_close_count += 1
            logger.info(
                "kabu WS: connection closed normally (count=%d), reconnecting...",
                consecutive_ok_close_count,
            )
            if consecutive_ok_close_count > _MAX_RECONNECT_ATTEMPTS:
                raise KabuConnectionError(
                    0, f"repeated ConnectionClosedOK ({consecutive_ok_close_count} times)"
                ) from exc
            await asyncio.sleep(_RECONNECT_DELAY_S)

        except websockets.exceptions.ConnectionClosedError as exc:
            consecutive_failures += 1
            if consecutive_failures >= _MAX_RECONNECT_ATTEMPTS:
                logger.error(
                    "kabu WS: connection closed permanently: code=%s reason=%s",
                    exc.code, exc.reason,
                )
                raise KabuConnectionError(0, str(exc)) from exc
            logger.warning(
                "kabu WS: connection closed (code=%s reason=%s, attempt %d/%d), reconnecting...",
                exc.code, exc.reason, consecutive_failures, _MAX_RECONNECT_ATTEMPTS,
            )
            await asyncio.sleep(_RECONNECT_DELAY_S)

        except Exception as exc:
            # asyncio.CancelledError は Python 3.8+ で BaseException 直系のため
            # ここには到達しない。タスクキャンセル時は自動的に伝播する。
            consecutive_failures += 1
            if consecutive_failures >= _MAX_RECONNECT_ATTEMPTS:
                logger.error("kabu WS: reconnect aborted: %s", exc, exc_info=True)
                raise KabuConnectionError(0, str(exc)) from exc
            logger.warning("kabu WS: disconnected (%s), reconnecting...", exc, exc_info=True)
            await asyncio.sleep(_RECONNECT_DELAY_S)
