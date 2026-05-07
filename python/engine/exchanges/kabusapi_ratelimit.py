"""kabuステーション API 流量制限 token-bucket。

SKILL R5 に従い 3 種の bucket を定義する:
- OrderBucket(5):  発注系 5 req/sec (/sendorder / /cancelorder)
- WalletBucket(10): 余力系 10 req/sec (/wallet/*)
- InfoBucket(10):   情報系 10 req/sec (/board / /symbol / /orders / /positions)

使い方:
    bucket = InfoBucket()
    async with bucket:
        response = await client.get(...)
"""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """シンプルな token-bucket rate limiter。

    `async with bucket:` で 1 トークンを消費する。
    トークンが尽きた場合は補充まで待機する。
    """

    def __init__(self, rate_per_sec: float) -> None:
        self._rate = rate_per_sec
        self._tokens = rate_per_sec  # 初期フル
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> None:
        async with self._lock:
            await self._acquire()

    async def __aexit__(self, *_: object) -> None:
        pass

    async def _acquire(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # 次のトークンが来るまでの待機時間
            wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)


def OrderBucket() -> TokenBucket:  # noqa: N802
    """発注系 5 req/sec."""
    return TokenBucket(5.0)


def WalletBucket() -> TokenBucket:  # noqa: N802
    """余力系 10 req/sec."""
    return TokenBucket(10.0)


def InfoBucket() -> TokenBucket:  # noqa: N802
    """情報系 10 req/sec."""
    return TokenBucket(10.0)
