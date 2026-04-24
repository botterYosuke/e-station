"""Check direct Binance WS connectivity."""
import asyncio
import json
import sys
import websockets


async def check(url: str, label: str, timeout: float = 20.0):
    print(f"[{label}] Connecting to {url}", flush=True)
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            print(f"[{label}] Connected! Waiting up to {timeout}s for first message...", flush=True)
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            stream = data.get("stream", "?")
            print(f"[{label}] Got message: stream={stream}", flush=True)
            if "data" in data:
                d = data["data"]
                print(f"[{label}]   Trade: price={d.get('p', '?')}, qty={d.get('q', '?')}", flush=True)
            return True
    except Exception as e:
        print(f"[{label}] Error: {type(e).__name__}: {e}", flush=True)
        return False


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade"
    label = sys.argv[2] if len(sys.argv) > 2 else "futures"
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
    await check(url, label, timeout)


asyncio.run(main())
