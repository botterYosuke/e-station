"""
Reference benchmark for FetchKlines request/response round-trip.

Role in the Phase-2 benchmark set:
- useful for smoke-testing request/response IPC wiring
- useful for tracking REST + Python + localhost WebSocket end-to-end fetch RTT
- NOT valid as the acceptance metric for "IPC added latency"

Why not:
- FetchKlines includes Binance REST latency
- it does not include Rust-side receive / render-queue instrumentation
- it cannot be compared directly against spec.md's trade-stream added-latency target

Usage:
    python scripts/measure_klines_latency.py --port <port> --token <token>
"""

import argparse
import asyncio
import json
import statistics
import time
import uuid

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
        "client_version": "measure-klines-reference",
    }
    t0 = time.perf_counter()
    await ws.send(json.dumps(hello))
    ready_raw = await ws.recv()
    t1 = time.perf_counter()
    ready = json.loads(ready_raw)
    assert ready.get("event") == "Ready", f"unexpected: {ready}"
    return {
        "ready": ready,
        "handshake_ms": (t1 - t0) * 1000,
    }


async def wait_for_klines(ws, request_id: str) -> None:
    while True:
        raw = await ws.recv()
        msg = json.loads(raw)
        if msg.get("event") == "Klines" and msg.get("request_id") == request_id:
            return
        if msg.get("event") == "Error" and msg.get("request_id") == request_id:
            raise RuntimeError(f"FetchKlines failed: {msg}")


async def measure(
    port: int,
    token: str,
    samples: int = 1000,
    warmup: int = 10,
    timeout_sec: float = 60.0,
) -> None:
    url = f"ws://127.0.0.1:{port}"
    print("=== FetchKlines RTT reference benchmark ===", flush=True)
    print(f"Target      : {url}", flush=True)
    print("Purpose     : request/response smoke test + reference RTT", flush=True)
    print("Not measured: pure IPC overhead / Rust-side added latency", flush=True)

    async with websockets.connect(url, open_timeout=10) as ws:
        hs = await handshake(ws, token)
        ready = hs["ready"]
        print(
            f"Handshake OK: engine_version={ready.get('engine_version')} "
            f"engine_session_id={ready.get('engine_session_id')}",
            flush=True,
        )
        print(f"Hello -> Ready RTT: {hs['handshake_ms']:.2f} ms", flush=True)

        print(f"Warm-up    : {warmup} FetchKlines calls", flush=True)
        async with asyncio.timeout(timeout_sec):
            for _ in range(warmup):
                rid = str(uuid.uuid4())
                await ws.send(
                    json.dumps(
                        {
                            "op": "FetchKlines",
                            "request_id": rid,
                            "venue": "binance",
                            "ticker": "BTCUSDT",
                            "timeframe": "1m",
                            "limit": 5,
                        }
                    )
                )
                await wait_for_klines(ws, rid)

        print(f"Sampling   : {samples} FetchKlines round-trips", flush=True)
        latencies: list[float] = []
        async with asyncio.timeout(timeout_sec):
            for i in range(samples):
                rid = str(uuid.uuid4())
                t0 = time.perf_counter()
                await ws.send(
                    json.dumps(
                        {
                            "op": "FetchKlines",
                            "request_id": rid,
                            "venue": "binance",
                            "ticker": "BTCUSDT",
                            "timeframe": "1m",
                            "limit": 5,
                        }
                    )
                )
                await wait_for_klines(ws, rid)
                t1 = time.perf_counter()
                latencies.append((t1 - t0) * 1000)
                if (i + 1) % 100 == 0:
                    print(
                        f"  progress    : {i + 1}/{samples} "
                        f"last={latencies[-1]:.2f} ms",
                        flush=True,
                    )

        latencies.sort()
        print("\n=== Results (reference only) ===", flush=True)
        print("Metric: FetchKlines request/response RTT", flush=True)
        print(f"  samples      : {len(latencies)}", flush=True)
        print(f"  min          : {latencies[0]:.2f} ms", flush=True)
        print(f"  p50          : {statistics.median(latencies):.2f} ms", flush=True)
        print(f"  p95          : {percentile(latencies, 0.95):.2f} ms", flush=True)
        print(f"  p99          : {percentile(latencies, 0.99):.2f} ms", flush=True)
        print(f"  max          : {latencies[-1]:.2f} ms", flush=True)
        print("\nInterpretation:", flush=True)
        print(
            "- This includes Binance REST latency, Python processing, and localhost WS transport.",
            flush=True,
        )
        print(
            "- Use it as a regression signal for fetch-path behavior, not as the spec acceptance metric.",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", type=str, required=True)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
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
