"""Compare single-recv vs repeated-cancel recv against Binance WS."""
import asyncio
import time
import orjson
import websockets

URL = "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade"


async def test_single_recv(timeout: float = 10.0):
    """No cancellation loop — just wait for first message."""
    print(f"\n[single-recv] timeout={timeout}s", flush=True)
    async with websockets.connect(URL) as ws:
        print("[single-recv] Connected", flush=True)
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            msg = orjson.loads(raw)
            data = msg.get("data", {})
            print(f"[single-recv] Got message: p={data.get('p')}, q={data.get('q')}", flush=True)
        except asyncio.TimeoutError:
            print(f"[single-recv] Timeout after {timeout}s", flush=True)


async def test_cancel_loop(duration: float = 10.0, batch_interval: float = 0.033):
    """Repeatedly cancel recv() like the engine does."""
    print(f"\n[cancel-loop] duration={duration}s, batch_interval={batch_interval}s", flush=True)
    async with websockets.connect(URL) as ws:
        print("[cancel-loop] Connected", flush=True)
        start = time.monotonic()
        recv_count = 0
        timeout_count = 0
        while time.monotonic() - start < duration:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=batch_interval)
                recv_count += 1
                msg = orjson.loads(raw)
                data = msg.get("data", {})
                print(f"[cancel-loop] Trade #{recv_count}: p={data.get('p')}", flush=True)
            except asyncio.TimeoutError:
                timeout_count += 1
        print(f"[cancel-loop] recv={recv_count}, timeouts={timeout_count}", flush=True)


async def main():
    await test_single_recv(timeout=10.0)
    await asyncio.sleep(1)
    await test_cancel_loop(duration=10.0, batch_interval=0.033)


asyncio.run(main())
