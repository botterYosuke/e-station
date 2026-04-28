"""
Phase-2 IPC benchmark preflight.

This script is intentionally positioned as a "preflight" benchmark rather than
the final acceptance benchmark from docs/benchmarks/phase-2.md.

What this script does:
- measures Hello -> Ready handshake round-trip on localhost
- subscribes to the Binance BTCUSDT trade stream and confirms events arrive
- reports local receive-side inter-arrival timing as a stream health signal

What this script does NOT do:
- measure spec-defined IPC added latency
- measure Rust receive / render-queue timing
- produce CPU usage comparisons

Those acceptance metrics still require Rust-side instrumentation and
`sent_at_ms` style event timestamps.

Usage:
    python scripts/measure_ipc_latency.py --port <port> --token <token>
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


async def handshake(ws, token: str) -> dict:
    hello = {
        "op": "Hello",
        "token": token,
        "schema_major": 1,
        "schema_minor": 0,
        "client_version": "measure-ipc-preflight",
    }
    t0 = time.perf_counter()
    await ws.send(json.dumps(hello))
    ready_raw = await ws.recv()
    t1 = time.perf_counter()
    ready = json.loads(ready_raw)
    assert ready.get("event") == "Ready", f"unexpected handshake reply: {ready}"
    return {
        "ready": ready,
        "handshake_ms": (t1 - t0) * 1000,
    }


async def measure(
    port: int,
    token: str,
    samples: int = 1000,
    warmup: int = 20,
    timeout_sec: float = 30.0,
) -> None:
    url = f"ws://127.0.0.1:{port}"
    print("=== IPC benchmark preflight ===", flush=True)
    print(f"Target      : {url}", flush=True)
    print("Purpose     : handshake timing + trade stream health check", flush=True)
    print("Not measured: spec-defined added IPC latency / CPU usage", flush=True)

    async with websockets.connect(url, open_timeout=10) as ws:
        hs = await handshake(ws, token)
        ready = hs["ready"]
        print(
            f"Handshake OK: engine_version={ready.get('engine_version')} "
            f"engine_session_id={ready.get('engine_session_id')}",
            flush=True,
        )
        print(f"Hello -> Ready RTT: {hs['handshake_ms']:.2f} ms", flush=True)

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
        print("Subscribed : Binance BTCUSDT trade stream", flush=True)
        print(f"Warm-up    : discarding first {warmup} trade events", flush=True)

        discarded = 0
        async with asyncio.timeout(timeout_sec):
            while discarded < warmup:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("event") == "Trades":
                    discarded += 1

        print(f"Sampling   : collecting {samples} trade events", flush=True)
        intervals: list[float] = []
        batch_sizes: list[int] = []
        prev = None
        collected = 0

        async with asyncio.timeout(timeout_sec):
            while collected < samples:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("event") != "Trades":
                    continue
                now = time.perf_counter()
                trades = msg.get("trades", [])
                batch_sizes.append(len(trades))
                if prev is not None:
                    intervals.append((now - prev) * 1000)
                prev = now
                collected += 1

        intervals.sort()
        batch_sizes.sort()

        print("\n=== Results (reference only) ===", flush=True)
        print("Metric: local receive-side trade event inter-arrival", flush=True)
        print(f"  samples      : {len(intervals)} intervals", flush=True)
        print(f"  min          : {intervals[0]:.2f} ms", flush=True)
        print(f"  p50          : {statistics.median(intervals):.2f} ms", flush=True)
        print(f"  p95          : {percentile(intervals, 0.95):.2f} ms", flush=True)
        print(f"  p99          : {percentile(intervals, 0.99):.2f} ms", flush=True)
        print(f"  max          : {intervals[-1]:.2f} ms", flush=True)
        print("Metric: trade batch size", flush=True)
        print(f"  p50          : {statistics.median(batch_sizes):.0f}", flush=True)
        print(f"  p95          : {percentile(batch_sizes, 0.95):.0f}", flush=True)
        print(f"  max          : {batch_sizes[-1]}", flush=True)
        print("\nInterpretation:", flush=True)
        print(
            "- This validates stream delivery and gives a localhost receive-side reference.",
            flush=True,
        )
        print(
            "- It is not the acceptance metric from spec.md §9.1 / phase-2.md §2.1.",
            flush=True,
        )
        print(
            "- Final IPC added latency still requires sent_at_ms + Rust-side receive timing.",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", type=str, required=True)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    args = parser.parse_args()
    asyncio.run(
        measure(
            args.port,
            args.token,
            samples=args.samples,
            warmup=args.warmup,
            timeout_sec=args.timeout_sec,
        )
    )


if __name__ == "__main__":
    main()
