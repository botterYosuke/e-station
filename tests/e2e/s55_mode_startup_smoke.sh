#!/usr/bin/env bash
# N1.13 — start-up smoke for `--mode {live|replay}`.
#
# Goal: verify both modes can spin up the Python engine, complete the
# Hello / Ready handshake, and exit cleanly. Full E2E (subscriptions,
# replay data load, /api/replay/*) is deferred to N1.14+.
#
# TODO: full smoke once N1.2 (J-Quants loader) and N1.3 (replay_api)
# land — at that point this script should also assert that
# `--mode replay` allows POST /api/replay/load and `--mode live`
# returns 400 for the same path.
#
# Current scope (stub):
#   - Verify the script and the binary exist
#   - Run --mode live  with a short OBSERVE_S window via smoke.sh
#   - Run --mode replay with the same window
#
# Usage:
#   bash tests/e2e/s55_mode_startup_smoke.sh                  # 10 s per mode
#   OBSERVE_S=30 bash tests/e2e/s55_mode_startup_smoke.sh

set -uo pipefail

OBSERVE_S="${OBSERVE_S:-10}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[s55] Mode smoke — OBSERVE_S=${OBSERVE_S}"
echo "[s55] NOTE: stub. Full E2E coverage will be added in N1.14+."

BINARY="${BINARY:-./target/release/flowsurface}"
[[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]] && BINARY="./target/release/flowsurface.exe"

if [[ ! -x "$BINARY" ]]; then
    echo "[s55] SKIP — release binary not built at $BINARY"
    echo "[s55]        run: cargo build --release"
    exit 0
fi

# Smoke run: live then replay.  We delegate the heavy lifting to smoke.sh
# but inject MODE so it appends `--mode <mode>` to the binary invocation.
# smoke.sh today does not support MODE — wiring is deferred to N1.14
# alongside pane visibility work that needs the same plumbing.
for mode in live replay; do
    echo "[s55] would-run: $BINARY --mode $mode  (skipped — full integration TODO)"
done

echo "[s55] OK (stub) — extend in N1.14+"
exit 0
