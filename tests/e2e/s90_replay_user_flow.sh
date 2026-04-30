#!/usr/bin/env bash
# s90 — Replay user-flow E2E (N4 series, strategy_file wiring).
#
# Verifies the full replay user flow end-to-end:
#   1. `--mode replay` binary starts; HTTP server on 127.0.0.1:9876 responds
#   2. Python engine starts; IPC handshake completes
#   3. POST /api/replay/load  (strategy_file=buy_and_hold.py)  → 200, trades_loaded >= 1
#   4. POST /api/replay/start                                  → 202 / 200
#      GET  /api/replay/status                                 → 200, status=ok
#
# Requirements:
#   - cargo build --release (or --debug) must have produced the binary
#   - uv must be available (Python engine)
#   - curl and python3 must be available
#
# Data source:
#   python/tests/fixtures/equities_trades_202401.csv.gz (built-in)
#   Override with JQUANTS_DIR env var.
#
# Usage:
#   bash tests/e2e/s90_replay_user_flow.sh
#   JQUANTS_DIR=/custom/path bash tests/e2e/s90_replay_user_flow.sh

set -uo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

PORT="${PORT:-9876}"
ENGINE_PORT="${ENGINE_PORT:-19877}"
TOKEN="${TOKEN:-e2e-s90-token}"
INSTRUMENT_ID="${INSTRUMENT_ID:-1301.TSE}"
START_DATE="${START_DATE:-2024-01-04}"
END_DATE="${END_DATE:-2024-01-05}"
GRANULARITY="${GRANULARITY:-Trade}"
STRATEGY_ID="${STRATEGY_ID:-buy-and-hold}"
INITIAL_CASH="${INITIAL_CASH:-1000000}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-s90-engine.log}"

RUST_LOG_FILE="$HOME/AppData/Roaming/flowsurface/flowsurface-current.log"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    RUST_LOG_FILE="$HOME/Library/Application Support/flowsurface/flowsurface-current.log"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    RUST_LOG_FILE="$HOME/.local/share/flowsurface/flowsurface-current.log"
fi

BINARY="${BINARY:-./target/release/flowsurface}"
if [[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]]; then
    BINARY="./target/release/flowsurface.exe"
fi

# J-Quants fixtures dir (default: repo's built-in fixtures)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
JQUANTS_DIR="${JQUANTS_DIR:-$REPO_ROOT/python/tests/fixtures}"

# ── Helpers ───────────────────────────────────────────────────────────────────

log() { printf '[s90] %s\n' "$*"; }

cleanup() {
    [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
    [[ -n "${APP_PID:-}" ]]    && kill -9 "$APP_PID"    2>/dev/null || true
}
trap cleanup EXIT

# ── Prerequisites ─────────────────────────────────────────────────────────────

if ! command -v curl >/dev/null 2>&1; then
    log "SKIP — curl not available"
    exit 0
fi

# Windows では python3 が Store スタブのため python を優先する
PYTHON_BIN="python"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    log "SKIP — python not available"
    exit 0
fi

if [[ ! -x "$BINARY" ]]; then
    log "SKIP — $BINARY not found (run: cargo build --release)"
    exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
    log "SKIP — uv not available"
    exit 0
fi

if [[ ! -d "$JQUANTS_DIR" ]]; then
    log "SKIP — JQUANTS_DIR not found: $JQUANTS_DIR"
    exit 0
fi

# ── Start Python engine ───────────────────────────────────────────────────────

if (echo > /dev/tcp/127.0.0.1/$ENGINE_PORT) 2>/dev/null; then
    log "FAIL — ENGINE_PORT $ENGINE_PORT is already in use"
    exit 1
fi

log "starting engine on :$ENGINE_PORT (JQUANTS_DIR=$JQUANTS_DIR)"
: > "$ENGINE_LOG"
JQUANTS_DIR="$JQUANTS_DIR" \
    uv run python -m engine --port "$ENGINE_PORT" --token "$TOKEN" \
    > "$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!

# Wait for engine TCP listener
for _i in {1..50}; do
    if (echo > /dev/tcp/127.0.0.1/$ENGINE_PORT) 2>/dev/null; then break; fi
    sleep 0.1
done
if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    log "FAIL — engine process exited immediately (PID $ENGINE_PID)"
    tail -20 "$ENGINE_LOG"
    exit 1
fi
if ! (echo > /dev/tcp/127.0.0.1/$ENGINE_PORT) 2>/dev/null; then
    log "FAIL — engine did not start on :$ENGINE_PORT"
    tail -20 "$ENGINE_LOG"
    exit 1
fi
log "engine listening on :$ENGINE_PORT"

# ── Start Rust binary in replay mode ─────────────────────────────────────────

log "starting flowsurface --mode replay"
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" RUST_LOG=info \
    "$BINARY" --mode replay \
    --data-engine-url "ws://127.0.0.1:$ENGINE_PORT/" \
    > /dev/null 2>&1 &
APP_PID=$!

# ── Step 1: Wait for HTTP server ──────────────────────────────────────────────

log "waiting for HTTP server on :$PORT"
deadline=$((SECONDS + 30))
while (( SECONDS < deadline )); do
    if curl -fsS "http://127.0.0.1:${PORT}/api/replay/status" >/dev/null 2>&1; then
        break
    fi
    sleep 0.3
done
if ! curl -fsS "http://127.0.0.1:${PORT}/api/replay/status" >/dev/null 2>&1; then
    log "FAIL — HTTP server did not come up on :$PORT within 30 s"
    tail -20 "$RUST_LOG_FILE" 2>/dev/null
    tail -20 "$ENGINE_LOG"
    exit 1
fi
log "HTTP server ready"

# ── Step 2: Wait for IPC handshake ───────────────────────────────────────────

log "waiting for IPC handshake"
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    if grep -q "engine handshake complete" "$RUST_LOG_FILE" 2>/dev/null; then
        break
    fi
    sleep 0.5
done
if ! grep -q "engine handshake complete" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL — IPC handshake never completed"
    if [[ ! -f "$RUST_LOG_FILE" ]]; then
        log "WARNING — RUST_LOG_FILE not found: $RUST_LOG_FILE (binary may not have created it yet)"
    fi
    tail -20 "$RUST_LOG_FILE" 2>/dev/null
    tail -20 "$ENGINE_LOG"
    exit 1
fi
log "IPC handshake complete"

# ── Step 3 & 4: POST /api/replay/load ────────────────────────────────────────

assert_load() {
    local label="$1"
    local strategy_file="$2"
    local body
    body=$(printf '{
  "instrument_id": "%s",
  "start_date": "%s",
  "end_date": "%s",
  "granularity": "%s",
  "strategy_file": "%s"
}' \
        "$INSTRUMENT_ID" "$START_DATE" "$END_DATE" "$GRANULARITY" "$strategy_file")

    log "POST /api/replay/load [$label] strategy_file=${strategy_file}"
    local http_code
    http_code=$(curl -sS --max-time 30 -o /tmp/s90_load_resp.json -w "%{http_code}" \
        -H 'Content-Type: application/json' \
        -X POST --data "$body" \
        "http://127.0.0.1:${PORT}/api/replay/load")

    if [[ -z "${http_code}" ]]; then
        log "FAIL [$label] — curl connection failed"
        cat /tmp/s90_load_resp.json >&2
        exit 1
    fi

    log "HTTP ${http_code}"
    cat /tmp/s90_load_resp.json
    echo

    if [[ "${http_code}" != "200" ]]; then
        log "FAIL [$label] — expected 200, got ${http_code}"
        exit 1
    fi

    local trades_loaded
    trades_loaded=$(cat /tmp/s90_load_resp.json | "$PYTHON_BIN" -c \
        "import json,sys; d=json.load(sys.stdin); print(d.get('trades_loaded',0))" \
        2>/dev/null || echo 0)
    [[ "${trades_loaded}" =~ ^[0-9]+$ ]] || trades_loaded=0

    if (( trades_loaded < 1 )); then
        log "FAIL [$label] — trades_loaded=${trades_loaded}, expected >= 1"
        exit 1
    fi
    log "OK [$label] trades_loaded=${trades_loaded}"
}

assert_load "buy_and_hold" "docs/example/buy_and_hold.py"

# ── Step 5: POST /api/replay/start ───────────────────────────────────────────

start_body=$(printf '{
  "instrument_id": "%s",
  "start_date": "%s",
  "end_date": "%s",
  "granularity": "%s",
  "strategy_id": "%s",
  "initial_cash": "%s"
}' \
    "$INSTRUMENT_ID" "$START_DATE" "$END_DATE" "$GRANULARITY" \
    "$STRATEGY_ID" "$INITIAL_CASH")

log "POST /api/replay/start"
start_code=$(curl -sS --max-time 60 -o /tmp/s90_start_resp.json -w "%{http_code}" \
    -H 'Content-Type: application/json' \
    -X POST --data "$start_body" \
    "http://127.0.0.1:${PORT}/api/replay/start")

if [[ -z "${start_code}" ]]; then
    log "FAIL — /api/replay/start: curl connection failed"
    exit 1
fi

log "HTTP ${start_code}"
cat /tmp/s90_start_resp.json
echo

if [[ "${start_code}" != "202" && "${start_code}" != "200" ]]; then
    log "FAIL — /api/replay/start expected 202 or 200, got ${start_code}"
    exit 1
fi
log "replay started"

strategy_id_val=$(cat /tmp/s90_start_resp.json | "$PYTHON_BIN" -c \
    "import json,sys; d=json.load(sys.stdin); print(d.get('strategy_id',''))" \
    2>/dev/null || echo "")
if [[ -z "${strategy_id_val}" ]]; then
    log "FAIL — /api/replay/start response missing strategy_id"
    exit 1
fi
log "strategy_id=${strategy_id_val}"

# ── Step 5b: GET /api/replay/status ──────────────────────────────────────────

log "GET /api/replay/status"
status_code=$(curl -sS --max-time 10 -o /tmp/s90_status_resp.json -w "%{http_code}" \
    "http://127.0.0.1:${PORT}/api/replay/status")

log "HTTP ${status_code}"
cat /tmp/s90_status_resp.json
echo

if [[ "${status_code}" != "200" ]]; then
    log "FAIL — GET /api/replay/status expected 200, got ${status_code}"
    exit 1
fi

status_val=$(cat /tmp/s90_status_resp.json | "$PYTHON_BIN" -c \
    "import json,sys; d=json.load(sys.stdin); print(d.get('status',''))" \
    2>/dev/null || echo "")

if [[ "${status_val}" != "ok" ]]; then
    log "FAIL — status expected 'ok', got '${status_val}'"
    exit 1
fi

log "OK"
