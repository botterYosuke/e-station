"""Debug: connect to engine, subscribe to trade stream, and dump all raw events."""
import asyncio
import json
import sys
import time
import websockets


async def debug(port: int, token: str) -> None:
    url = f"ws://127.0.0.1:{port}"
    print(f"Connecting to {url}", flush=True)

    async with websockets.connect(url, open_timeout=10) as ws:
        hello = {
            "op": "Hello",
            "token": token,
            "schema_major": 1,
            "schema_minor": 0,
            "client_version": "debug-script",
        }
        await ws.send(json.dumps(hello))
        ready_raw = await ws.recv()
        print(f"Ready: {ready_raw[:200]}", flush=True)

        await ws.send(json.dumps({
            "op": "Subscribe",
            "venue": "binance",
            "ticker": "BTCUSDT",
            "stream": "trade",
        }))
        print("Subscribed. Waiting 30s for any event...", flush=True)

        start = time.monotonic()
        count = 0
        try:
            async with asyncio.timeout(30):
                while True:
                    raw = await ws.recv()
                    elapsed = time.monotonic() - start
                    count += 1
                    msg = json.loads(raw)
                    event = msg.get("event", "?")
                    print(f"  t={elapsed:.1f}s #{count} event={event} raw[:150]={raw[:150]}", flush=True)
        except TimeoutError:
            print(f"30s timeout. Total events: {count}", flush=True)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 19876
    token = sys.argv[2] if len(sys.argv) > 2 else "test-phase2-token"
    asyncio.run(debug(port, token))


if __name__ == "__main__":
    main()
