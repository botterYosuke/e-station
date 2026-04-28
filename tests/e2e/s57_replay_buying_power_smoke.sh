#!/usr/bin/env bash
# N1.16 — REPLAY 買付余力スモーク
#
# Goal: verify that GET /api/replay/portfolio returns valid JSON
# after a replay session has produced ReplayBuyingPower events.
# Full E2E (requires running engine + replay strategy) is deferred.
#
# Current scope (stub):
#   - Verify the script exists and runs without errors
#   - SKIP if release binary is not built
#
# Usage:
#   bash tests/e2e/s57_replay_buying_power_smoke.sh

set -uo pipefail

echo "[s57] REPLAY buying-power smoke — stub"
echo "[s57] NOTE: stub. Full E2E coverage will be added with full REPLAY E2E suite."

BINARY="${BINARY:-./target/release/flowsurface}"
[[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]] && BINARY="./target/release/flowsurface.exe"

if [[ ! -x "$BINARY" ]]; then
    echo "[s57] SKIP — release binary not built at $BINARY"
    echo "[s57]        run: cargo build --release"
    exit 0
fi

echo "[s57] OK (stub) — extend in full REPLAY E2E phase"
exit 0
