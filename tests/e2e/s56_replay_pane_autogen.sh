#!/usr/bin/env bash
# N1.14 — REPLAY pane auto-generation smoke test.
#
# Goal: verify that POST /api/replay/load triggers the creation of a
# TimeAndSales pane and a CandlestickChart pane in the Iced dashboard.
#
# Current scope (stub):
#   - Verify the script exists and is runnable
#   - SKIP when the release binary is not present (CI / dev without a build)
#
# Full E2E:
#   Once the Python engine is wired up with J-Quants data (N1.2) and the
#   visual assertion framework exists (N1.14 UI), this script should:
#     1. Start the app in --mode replay
#     2. POST /api/replay/load with a test instrument
#     3. Assert that exactly 2 new panes appear (TimeAndSales + CandlestickChart)
#     4. Close one pane, reload, assert only 1 pane appears (dismiss logic)
#
# Usage:
#   bash tests/e2e/s56_replay_pane_autogen.sh

set -uo pipefail

echo "[s56] REPLAY pane auto-generation smoke — N1.14 stub"

BINARY="${BINARY:-./target/release/flowsurface}"

if [[ ! -x "$BINARY" ]]; then
    echo "[s56] SKIP: binary not found at $BINARY (run cargo build --release first)"
    exit 0
fi

echo "[s56] binary found at $BINARY — full visual E2E pending N1.14 UI phase"
echo "[s56] PASS (stub)"
exit 0
