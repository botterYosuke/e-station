#!/usr/bin/env bash
# Equivalent to the "replay - Rust: Debug (CodeLLDB)" VSCode launch configuration.
# Builds flowsurface in debug mode, then runs replay_dev_load.sh in the background
# (equivalent to "replay: watch & load (active file)" task), then starts the exe.
#
# Usage:
#   bash scripts/run-replay-debug.sh [strategy_file]
#
#   strategy_file — path to a strategy .py file passed to replay_dev_load.sh
#                   (equivalent to VSCode's ${file} — the active editor file)
#
# Env overrides (via .env file at repo root, or shell env):
#   Any variable defined in .env is loaded automatically.
#   FLOWSURFACE_ENGINE_TOKEN  — must match the running engine's --token
#   REPLAY_INSTRUMENT_ID / REPLAY_START_DATE / REPLAY_END_DATE etc.
#     (forwarded to replay_dev_load.sh — see that script for full list)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STRATEGY_FILE="${1:-}"

if [[ -z "$STRATEGY_FILE" ]]; then
    echo "[run-replay-debug] ERROR: strategy_file is required"
    echo "  Usage: bash scripts/run-replay-debug.sh <strategy.py>"
    echo "  Example: bash scripts/run-replay-debug.sh docs/example/buy_and_hold.py"
    exit 1
fi

# Load .env if present (same as envFile in launch.json)
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +o allexport
fi

# Prepend .venv/Scripts to PATH (same as launch.json env.PATH)
export PATH="$REPO_ROOT/.venv/Scripts:$PATH"

# Replay parameters — override via env or .env file.
export REPLAY_INSTRUMENT_ID="${REPLAY_INSTRUMENT_ID:-1301.TSE}"
export REPLAY_START_DATE="${REPLAY_START_DATE:-2025-01-06}"
export REPLAY_END_DATE="${REPLAY_END_DATE:-2025-03-31}"
export REPLAY_GRANULARITY="${REPLAY_GRANULARITY:-Daily}"

echo "[run-replay-debug] building (debug)..."
cargo build --manifest-path "$REPO_ROOT/Cargo.toml"

# Start replay_dev_load.sh in background (= "replay: watch & load (active file)" task).
# It polls the HTTP server and POSTs load + start once the exe is ready.
echo "[run-replay-debug] starting replay_dev_load.sh in background (strategy_file=${STRATEGY_FILE:-<none>})..."
bash "$REPO_ROOT/scripts/replay_dev_load.sh" "$STRATEGY_FILE" &

echo "[run-replay-debug] starting flowsurface --mode replay"
exec "$REPO_ROOT/target/debug/flowsurface.exe" --mode replay
