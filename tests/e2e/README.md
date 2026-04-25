# Phase 7 — E2E Smoke Tests

Manual QA scripts for the Rust + Python IPC pipeline. Not wired into
`cargo test` because they require live network access to all five
exchange venues.

## Prerequisites

- Release build: `cargo build --release`
- `uv` installed (Python engine deps via `uv run python -m engine`)

## Running

```bash
bash tests/e2e/smoke.sh                    # 30 s soak (default)
OBSERVE_S=120 bash tests/e2e/smoke.sh      # 2 min soak
PORT=29876 bash tests/e2e/smoke.sh         # custom port
```

Exit codes:

| code | meaning |
|------|---------|
| 0    | PASS — handshake + soak window had no silent failures |
| 1    | binary missing — run `cargo build --release` |
| 2    | handshake never completed within 15 s |
| 3    | silent failure detected (printed above) |

## Scenarios

The script covers:

1. **Startup**: handshake completes within 15 s.
2. **Auto-stream**: 5 venues each connect (Binance/Bybit/Hyperliquid/MEXC/OKX).
3. **Stability**: zero `DepthGap`, `parse error`, `snapshot fetch failed`,
   `fetch_ticker_metadata timeout`, `TickerStats parse error` in the
   observation window.

## Manual scenarios (not yet automated)

These require GUI interaction; document outcome in PR descriptions:

- Click a Binance ticker → chart renders with depth + trade flow ≤ 5 s.
- `kill -9 <python pid>` → toast appears → app reconnects ≤ 5 s →
  depth resyncs without restart.
- Disable network briefly → reconnect after restoring → no crash, no
  duplicate stream subscriptions.

## Adding a new check

Edit [smoke.sh](smoke.sh) and add a `check "<regex>" "<log file>" "<label>"`
call between the observation window and the exit clause. Keep regexes
narrow — false positives are worse than missing edge cases here, since
this script gates merges into `main`.
