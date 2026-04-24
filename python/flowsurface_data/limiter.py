"""Exchange rate limiter stubs — ported from exchange/src/adapter/limiter.rs"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Simple token-bucket rate limiter."""

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill_rate = refill_per_second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: int = 1) -> None:
        async with self._lock:
            self._refill()
            while self._tokens < cost:
                wait = (cost - self._tokens) / self._refill_rate
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= cost

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now


class BinanceLimiter:
    """Binance rate limits: 1200 weight/min for REST, 300 raw requests/5min."""

    def __init__(self) -> None:
        self._weight = TokenBucket(capacity=1200, refill_per_second=1200 / 60)
        self._raw = TokenBucket(capacity=300, refill_per_second=300 / 300)

    async def acquire_rest(self, weight: int = 1) -> None:
        await asyncio.gather(
            self._weight.acquire(weight),
            self._raw.acquire(1),
        )
