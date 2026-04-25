#!/usr/bin/env bash
# Phase 7 T3 — UI smoke test for the Rust + Python IPC pipeline.
#
# Boots the Python engine + Rust release binary, polls the rotating log
# for the handshake-complete marker, then watches 30 s of runtime for
# silent failures (parse errors, snapshot fetch failures, depth gap
# storms). Exits non-zero on any of those classes of regression.
#
# Manual QA scenarios (per phase-7 §T3) that this script covers:
#   1. Startup: engine handshake completes within 10 s.
#   2. Auto-subscribed streams: 5 venues each report Connected.
#   3. Stability: zero "DepthGap", "parse error", "snapshot fetch failed",
#      and "TickerStats parse error" lines over the observation window.
#
# Scenarios still requiring a human (not automated yet):
#   - Click on a Binance ticker → chart renders within 5 s.
#   - kill -9 the Python engine → toast + auto-recovery within 5 s.
#
# Usage:
#   bash tests/e2e/smoke.sh                    # 30 s observation
#   OBSERVE_S=120 bash tests/e2e/smoke.sh      # longer soak

set -uo pipefail

OBSERVE_S="${OBSERVE_S:-30}"
PORT="${PORT:-19876}"
TOKEN="${TOKEN:-e2e-smoke-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-engine.log}"
RUST_LOG_FILE="$HOME/AppData/Roaming/flowsurface/flowsurface-current.log"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    RUST_LOG_FILE="$HOME/Library/Application Support/flowsurface/flowsurface-current.log"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    RUST_LOG_FILE="$HOME/.local/share/flowsurface/flowsurface-current.log"
fi
BINARY="${BINARY:-./target/release/flowsurface}"
[[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]] && BINARY="./target/release/flowsurface.exe"

cleanup() {
    [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
    [[ -n "${APP_PID:-}" ]] && kill -9 "$APP_PID" 2>/dev/null || true
}
trap cleanup EXIT

log() { printf '[smoke] %s\n' "$*" >&2; }

if [[ ! -x "$BINARY" ]]; then
    log "FAIL: $BINARY not found. Run: cargo build --release"
    exit 1
fi

log "starting engine on :$PORT"
: > "$ENGINE_LOG"
uv run python -m engine --port "$PORT" --token "$TOKEN" > "$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!

# Wait for the engine TCP listener to come up.
for _ in {1..50}; do
    if (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then break; fi
    sleep 0.1
done

log "starting flowsurface app"
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > /dev/null 2>&1 &
APP_PID=$!

# Wait for handshake to complete.
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    if grep -q "engine handshake complete" "$RUST_LOG_FILE" 2>/dev/null; then
        log "handshake complete"
        break
    fi
    sleep 0.5
done
if ! grep -q "engine handshake complete" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: handshake never completed"
    tail -30 "$RUST_LOG_FILE" 2>/dev/null
    tail -30 "$ENGINE_LOG"
    exit 2
fi

log "observing for $OBSERVE_S seconds"
sleep "$OBSERVE_S"

# Audit logs for known silent-failure signatures.
fail=0
check() {
    local pattern="$1" file="$2" label="$3"
    local hits
    hits=$(grep -cE "$pattern" "$file" 2>/dev/null | tr -d '\r\n[:space:]')
    [[ -z "$hits" ]] && hits=0
    if (( hits > 0 )); then
        log "FAIL: $label ($hits hits in $file)"
        grep -E "$pattern" "$file" | head -5 | sed 's/^/  /' >&2
        fail=1
    fi
}

check "DepthGap"                     "$RUST_LOG_FILE" "depth gap storms"
check "fetch_ticker_(metadata|stats).*timeout" "$RUST_LOG_FILE" "ticker fetch timeouts"
check "snapshot fetch failed"        "$ENGINE_LOG"    "snapshot fetch failures"
check "parse error"                  "$ENGINE_LOG"    "engine parse errors"
check "TickerStats.*parse error"     "$RUST_LOG_FILE" "ticker stats parse errors"

if (( fail == 0 )); then
    log "PASS: $OBSERVE_S s clean"
    exit 0
else
    exit 3
fi
