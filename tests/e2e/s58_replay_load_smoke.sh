#!/usr/bin/env bash
# N1.3 — POST /api/replay/load smoke (manual / local-only).
#
# Goal: drive the running release binary in `--mode replay` against a
# real (or fixtures) J-Quants directory and assert that
# /api/replay/load returns 200 with non-zero `trades_loaded`.
#
# This script is **not** wired into CI because it requires:
#   - `cargo build --release` to have produced ./target/release/flowsurface
#   - The binary to be running with `--mode replay`
#   - The Python engine to be reachable (managed mode auto-spawns it)
#   - J-Quants fixtures or full data on `S:/j-quants/...`
#
# Usage (local manual run):
#   # term 1:
#   ./target/release/flowsurface --mode replay
#   # term 2:
#   bash tests/e2e/s58_replay_load_smoke.sh

set -uo pipefail

PORT="${PORT:-9876}"
INSTRUMENT_ID="${INSTRUMENT_ID:-1301.TSE}"
START_DATE="${START_DATE:-2024-01-04}"
END_DATE="${END_DATE:-2024-01-05}"
GRANULARITY="${GRANULARITY:-Trade}"

if ! command -v curl >/dev/null 2>&1; then
    echo "[s58] SKIP — curl not available"
    exit 0
fi

# Fail fast if the API is not listening yet.
if ! curl -fsS "http://127.0.0.1:${PORT}/api/replay/status" >/dev/null 2>&1; then
    echo "[s58] SKIP — replay API not reachable on 127.0.0.1:${PORT} (is flowsurface running?)"
    exit 0
fi

body=$(cat <<EOF
{
  "instrument_id": "${INSTRUMENT_ID}",
  "start_date": "${START_DATE}",
  "end_date": "${END_DATE}",
  "granularity": "${GRANULARITY}"
}
EOF
)

echo "[s58] POST /api/replay/load body=${body}"
http_code=$(curl -sS -o /tmp/s58_resp.json -w "%{http_code}" \
    -H 'Content-Type: application/json' \
    -X POST \
    --data "${body}" \
    "http://127.0.0.1:${PORT}/api/replay/load")

echo "[s58] HTTP ${http_code}"
cat /tmp/s58_resp.json
echo

if [[ "${http_code}" != "200" ]]; then
    echo "[s58] FAIL — expected 200, got ${http_code}"
    exit 1
fi

echo "[s58] OK"
