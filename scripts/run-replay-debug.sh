#!/usr/bin/env bash
# Equivalent to the "replay - Rust: Debug (CodeLLDB)" VSCode launch configuration.
# Builds flowsurface in debug mode, then runs replay_dev_load.sh in the background
# (equivalent to "replay: watch & load (active file)" task), then starts the exe.
#
# Usage:
#   bash scripts/run-replay-debug.sh <strategy_file> <instrument_id> <start_date> <end_date> [granularity]
#
#   strategy_file  — path to a strategy .py file (e.g. docs/example/buy_and_hold.py)
#   instrument_id  — e.g. 1301.TSE
#   start_date     — e.g. 2025-01-06  (YYYY-MM-DD)
#   end_date       — e.g. 2025-03-31  (YYYY-MM-DD)
#   granularity    — Daily | Minute | Trade  (default: Daily)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STRATEGY_FILE="${1:?strategy_file is required (e.g. docs/example/buy_and_hold.py)}"
INSTRUMENT_ID="${2:?instrument_id is required (e.g. 1301.TSE)}"
START_DATE="${3:?start_date is required (e.g. 2025-01-06)}"
END_DATE="${4:?end_date is required (e.g. 2025-03-31)}"
GRANULARITY="${5:-Daily}"

# Prepend .venv/Scripts to PATH (same as launch.json env.PATH)
export PATH="$REPO_ROOT/.venv/Scripts:$PATH"

echo "[run-replay-debug] building (debug)..."
cargo build --manifest-path "$REPO_ROOT/Cargo.toml"

# Start replay_dev_load.sh in background (= "replay: watch & load (active file)" task).
# It polls the HTTP server and POSTs load + start once the exe is ready.
echo "[run-replay-debug] starting replay_dev_load.sh in background..."
bash "$REPO_ROOT/scripts/replay_dev_load.sh" \
    "$STRATEGY_FILE" "$INSTRUMENT_ID" "$START_DATE" "$END_DATE" "$GRANULARITY" &

echo "[run-replay-debug] starting flowsurface --mode replay"
exec "$REPO_ROOT/target/debug/flowsurface.exe" --mode replay
