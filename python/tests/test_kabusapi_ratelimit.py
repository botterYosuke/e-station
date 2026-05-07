"""OrderBucket の 6 件目ブロックテスト (INV-K4-RATELIMIT)。"""
import asyncio
import time

import pytest

from engine.exchanges.kabusapi_ratelimit import OrderBucket, WalletBucket, InfoBucket, TokenBucket


@pytest.mark.demo_kabu
@pytest.mark.asyncio
async def test_order_bucket_blocks_at_6th_req():
    """OrderBucket(5) は 6 件目でブロックする (INV-K4-RATELIMIT)."""
    bucket = OrderBucket()
    # 最初の 5 件は即座に通過
    for _ in range(5):
        async with bucket:
            pass

    # 6 件目は待機が発生するはず
    start = time.monotonic()
    async with bucket:
        pass
    elapsed = time.monotonic() - start
    # 少なくとも 100ms 待機が発生するはず（rate=5/s → 0.2s 間隔）
    assert elapsed >= 0.1, f"Expected blocking at 6th request, but elapsed={elapsed:.3f}s"


@pytest.mark.demo_kabu
def test_bucket_types():
    """3 種の bucket が正しい rate を持つ。"""
    order = OrderBucket()
    wallet = WalletBucket()
    info = InfoBucket()
    assert order._rate == 5.0
    assert wallet._rate == 10.0
    assert info._rate == 10.0
