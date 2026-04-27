"""
Trade stream smoke test.

Role in the Phase-2 benchmark set:
- confirms Hello/Ready succeeds
- confirms Subscribe(trade) produces Trades events
- helps catch auth / routing / Binance stream issues quickly

This is intentionally a smoke test, not a benchmark acceptance script.
"""

import argparse
import asyncio
import json
import statistics
import time

import websockets


def percentile(sorted_values: list[float], ratio: float) -> float:
    if not sorted_values:
        raise ValueError("percentile() requires at least one value")
    index = min(len(sorted_values) - 1, int(len(sorted_values) * ratio))
    return sorted_values[index]


async def test(
    port: int,
    token: str,
    events: int = 30,
    timeout_sec: float = 30.0,
) -> None:
    url = f"ws://127.0.0.1:{port}"
    print("=== Trade stream smoke test ===", flush=True)
    print(f"Target      : {url}", flush=True)
    print("Purpose     : verify trade stream connectivity and event flow", flush=True)

    async with websockets.connect(url, open_timeout=10) as ws:
        hello = {
            "op": "Hello",
            "token": token,
            "schema_major": 1,
            "schema_minor": 0,
            "client_version": "trade-stream-smoke-test",
        }
        await ws.send(json.dumps(hello))
        ready_raw = await ws.recv()
        ready = json.loads(ready_raw)
        assert ready.get("event") == "Ready", f"unexpected handshake reply: {ready}"
        print(
            f"Handshake OK: engine_version={ready.get('engine_version')} "
            f"engine_session_id={ready.get('engine_session_id')}",
            flush=True,
        )

        await ws.send(
            json.dumps(
                {
                    "op": "Subscribe",
                    "venue": "binance",
                    "ticker": "BTCUSDT",
                    "stream": "trade",
                }
            )
        )
        print(
            f"Subscribed : Binance BTCUSDT trade stream, waiting for {events} events",
            flush=True,
        )

        count = 0
        intervals: list[float] = []
        batch_sizes: list[int] = []
        prev_time = None

        try:
            async with asyncio.timeout(timeout_sec):
                while count < events:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    now = time.perf_counter()
                    if msg.get("event") != "Trades":
                        print(f"  other event : {msg.get('event')}", flush=True)
                        continue

                    trades = msg.get("trades", [])
                    batch_sizes.append(len(trades))
                    if prev_time is not None:
                        intervals.append((now - prev_time) * 1000)
                    prev_time = now
                    count += 1

                    if count <= 5 or count % 10 == 0:
                        print(
                            f"  trade event #{count}: {len(trades)} trades",
                            flush=True,
                        )
        except TimeoutError:
            print(f"Timeout after {timeout_sec:.1f}s, got {count} trade events", flush=True)
            raise

        print("\n=== Smoke test summary ===", flush=True)
        print(f"  trades events: {count}", flush=True)
        print(f"  batch max    : {max(batch_sizes)}", flush=True)
        print(f"  batch p50    : {statistics.median(batch_sizes):.0f}", flush=True)
        if intervals:
            intervals.sort()
            print(f"  interval min : {intervals[0]:.2f} ms", flush=True)
            print(f"  interval p50 : {statistics.median(intervals):.2f} ms", flush=True)
            print(f"  interval p95 : {percentile(intervals, 0.95):.2f} ms", flush=True)
            print(f"  interval max : {intervals[-1]:.2f} ms", flush=True)
        print("Result      : PASS (stream events observed)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port_pos", nargs="?", type=int)
    parser.add_argument("token_pos", nargs="?")
    parser.add_argument("--port", dest="port_opt", type=int)
    parser.add_argument("--token", dest="token_opt", type=str)
    parser.add_argument("--events", type=int, default=30)
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    args = parser.parse_args()

    port = args.port_opt if args.port_opt is not None else args.port_pos
    token = args.token_opt if args.token_opt is not None else args.token_pos
    if port is None or token is None:
        parser.error("port and token are required (positional or --port/--token)")

    asyncio.run(
        test(
            port,
            token,
            events=args.events,
            timeout_sec=args.timeout_sec,
        )
    )


if __name__ == "__main__":
    main()
