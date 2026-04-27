"""Tests for rate limiter."""

import asyncio
import pytest
from engine.limiter import TokenBucket, BinanceLimiter


@pytest.mark.asyncio
async def test_token_bucket_allows_within_capacity():
    bucket = TokenBucket(capacity=10, refill_per_second=10)
    # Should not raise or block for costs within capacity
    for _ in range(10):
        await bucket.acquire(1)


@pytest.mark.asyncio
async def test_token_bucket_refills_over_time():
    bucket = TokenBucket(capacity=1, refill_per_second=100)
    await bucket.acquire(1)
    await asyncio.sleep(0.02)  # wait for ~2 tokens to refill
    await bucket.acquire(1)  # should succeed after refill


@pytest.mark.asyncio
async def test_binance_limiter_acquires():
    limiter = BinanceLimiter()
    # Should complete without error for low-weight requests
    await limiter.acquire_rest(weight=1)
