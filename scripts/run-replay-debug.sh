#!/usr/bin/env bash
# Equivalent to the "replay - Rust: Debug (CodeLLDB)" VSCode launch configuration.
# Builds flowsurface in debug mode and runs it with --mode replay.
#
# Usage:
#   bash scripts/run-replay-debug.sh
#
# Env overrides (via .env file at repo root, or shell env):
#   Any variable defined in .env is loaded automatically.
#   FLOWSURFACE_ENGINE_TOKEN  — must match the running engine's --token

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present (same as envFile in launch.json)
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +o allexport
fi

# Prepend .venv/Scripts to PATH (same as launch.json env.PATH)
export PATH="$REPO_ROOT/.venv/Scripts:$PATH"

echo "[run-replay-debug] building (debug)..."
cargo build --manifest-path "$REPO_ROOT/Cargo.toml"

echo "[run-replay-debug] starting flowsurface --mode replay"
exec "$REPO_ROOT/target/debug/flowsurface.exe" --mode replay
