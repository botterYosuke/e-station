#!/usr/bin/env bash
# Dev helper: wait for replay HTTP server, then POST load + start.
#
# Usage (VSCode task):
#   bash scripts/replay_dev_load.sh <strategy_file>
#
# Env overrides:
#   REPLAY_INSTRUMENT_ID  (default: 1301.TSE)
#   REPLAY_START_DATE     (default: 2024-01-04)
#   REPLAY_END_DATE       (default: 2024-01-05)
#   REPLAY_GRANULARITY    (default: Trade)
#   REPLAY_INITIAL_CASH   (default: 1000000)
#   REPLAY_STRATEGY_ID    (default: user-strategy)
#   PORT                  (default: 9876)

set -uo pipefail

STRATEGY_FILE="${1:-}"
PORT="${PORT:-9876}"
INSTRUMENT_ID="${REPLAY_INSTRUMENT_ID:-1301.TSE}"
START_DATE="${REPLAY_START_DATE:-2024-01-04}"
END_DATE="${REPLAY_END_DATE:-2024-01-05}"
GRANULARITY="${REPLAY_GRANULARITY:-Trade}"
INITIAL_CASH="${REPLAY_INITIAL_CASH:-1000000}"
STRATEGY_ID="${REPLAY_STRATEGY_ID:-user-strategy}"

log() { printf '[replay-load] %s\n' "$*"; }

# ── Step 1: wait for HTTP server ──────────────────────────────────────────────
log "waiting for HTTP server on :$PORT ..."
for _i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PORT/api/replay/status" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! curl -fsS "http://127.0.0.1:$PORT/api/replay/status" >/dev/null 2>&1; then
    log "FAIL — server did not start within 60 s"
    exit 1
fi
log "server ready"

# ── Step 2: POST /api/replay/load ─────────────────────────────────────────────
# python でパスを JSON エンコードして Windows パス(バックスラッシュ)を安全に扱う
load_body=$(python -c "
import json, sys
d = {
    'instrument_id': sys.argv[1],
    'start_date':    sys.argv[2],
    'end_date':      sys.argv[3],
    'granularity':   sys.argv[4],
}
if sys.argv[5]:
    d['strategy_file'] = sys.argv[5]
print(json.dumps(d))
" "$INSTRUMENT_ID" "$START_DATE" "$END_DATE" "$GRANULARITY" "$STRATEGY_FILE")

log "POST /api/replay/load  strategy_file=${STRATEGY_FILE:-<none>}"
load_resp=$(curl -sS -w '\n%{http_code}' -X POST \
    -H 'Content-Type: application/json' \
    --data "$load_body" \
    "http://127.0.0.1:$PORT/api/replay/load")
load_code=$(echo "$load_resp" | tail -1)
echo "$load_resp" | head -1
if [[ "$load_code" != "200" ]]; then
    log "FAIL — /api/replay/load returned $load_code"
    exit 1
fi
log "load OK (HTTP $load_code)"

# ── Step 3: POST /api/replay/start ───────────────────────────────────────────
start_body=$(python -c "
import json, sys
print(json.dumps({
    'instrument_id': sys.argv[1],
    'start_date':    sys.argv[2],
    'end_date':      sys.argv[3],
    'granularity':   sys.argv[4],
    'strategy_id':   sys.argv[5],
    'initial_cash':  sys.argv[6],
}))
" "$INSTRUMENT_ID" "$START_DATE" "$END_DATE" "$GRANULARITY" "$STRATEGY_ID" "$INITIAL_CASH")

log "POST /api/replay/start"
start_resp=$(curl -sS -w '\n%{http_code}' -X POST \
    -H 'Content-Type: application/json' \
    --data "$start_body" \
    "http://127.0.0.1:$PORT/api/replay/start")
start_code=$(echo "$start_resp" | tail -1)
echo "$start_resp" | head -1
if [[ "$start_code" != "202" && "$start_code" != "200" ]]; then
    log "FAIL — /api/replay/start returned $start_code"
    exit 1
fi
log "start OK (HTTP $start_code)"
log "done"
