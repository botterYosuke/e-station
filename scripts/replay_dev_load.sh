#!/usr/bin/env bash
# Dev helper: wait for replay HTTP server, then POST load + start.
#
# Usage:
#   bash scripts/replay_dev_load.sh <strategy_file> <instrument_id> <start_date> <end_date> [granularity]
#
# Arguments:
#   strategy_file  — path to strategy .py file (e.g. docs/example/buy_and_hold.py)
#   instrument_id  — e.g. 1301.TSE
#   start_date     — e.g. 2025-01-06  (YYYY-MM-DD)
#   end_date       — e.g. 2025-03-31  (YYYY-MM-DD)
#   granularity    — Daily | Minute | Trade  (default: Daily)
#
# Optional env vars:
#   REPLAY_INITIAL_CASH   (default: 1000000)
#   REPLAY_STRATEGY_ID    (default: user-strategy)
#   PORT                  (default: 9876)

set -uo pipefail

# tee / process-substitution / exec 経由でログ出力していると、何故か VSCode 経由
# の起動時にすべての output が消えてしまう環境がある。ここでは直接 append で
# 各ステップを記録して、どこまで実行できたかを残す。
LOG_DIR="${HOME}/.cache/flowsurface"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/replay_dev_load.log"
trace() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" >> "$LOG_FILE"; }
trace "----- start pid=$$ shell=$BASH_VERSION cwd=$(pwd) -----"
trace "argc=$# argv=$*"
trace "HOME=${HOME:-} PATH=${PATH:-}"

# 常に "[replay-load] done" を出力して VSCode の background task の endsPattern に
# マッチさせる。これがないと前回失敗した task インスタンスが「実行中」のまま残り、
# 次の F5 で bash が再起動しない。exit code は trap 前のまま温存される。
trap 'rc=$?; trace "trap fired exit=$rc BASH_LINENO=${BASH_LINENO[*]:-?}"; echo "[replay-load] done (exit=$rc) log=$LOG_FILE"; exit $rc' EXIT

trace "before arg parsing"
STRATEGY_FILE="${1:?strategy_file is required (e.g. docs/example/buy_and_hold.py)}"
INSTRUMENT_ID="${2:?instrument_id is required (e.g. 1301.TSE)}"
START_DATE="${3:?start_date is required (e.g. 2025-01-06)}"
END_DATE="${4:?end_date is required (e.g. 2025-03-31)}"
GRANULARITY="${5:-Daily}"
PORT="${PORT:-9876}"
INITIAL_CASH="${REPLAY_INITIAL_CASH:-1000000}"
STRATEGY_ID="${REPLAY_STRATEGY_ID:-user-strategy}"
trace "after arg parsing strategy=$STRATEGY_FILE inst=$INSTRUMENT_ID start=$START_DATE end=$END_DATE gran=$GRANULARITY port=$PORT"

log() { local msg="$*"; printf '[replay-load] %s\n' "$msg"; trace "log: $msg"; }

# ── Step 1: wait for HTTP server ──────────────────────────────────────────────
# CodeLLDB が debug ビルド flowsurface.exe を起動して HTTP 9876 を listen するまで
# Windows debug ビルドだと数分かかることがある。タイムアウトを十分に取る。
WAIT_TIMEOUT_S="${WAIT_TIMEOUT_S:-600}"
log "waiting for HTTP server on :$PORT (timeout ${WAIT_TIMEOUT_S}s) ..."
for _i in $(seq 1 "$WAIT_TIMEOUT_S"); do
    if curl -fsS "http://127.0.0.1:$PORT/api/replay/status" >/dev/null 2>&1; then
        log "server ready (after ${_i}s)"
        break
    fi
    if (( _i % 10 == 0 )); then
        log "still waiting ... ${_i}s elapsed"
    fi
    sleep 1
done
if ! curl -fsS "http://127.0.0.1:$PORT/api/replay/status" >/dev/null 2>&1; then
    log "FAIL — server did not start within ${WAIT_TIMEOUT_S} s"
    exit 1
fi

# ── Step 2: POST /api/replay/load ─────────────────────────────────────────────
# JSON を pure bash で組み立てる。VSCode 経由で起動された bash は PATH 上の
# python が Windows Store のスタブに解決されて無音で空文字を返すケースがあり、
# python に頼ると body が空のまま POST されて 400 になる。
json_escape() {
    # \ → \\ , " → \"  (制御文字は含まれない前提)
    local s=${1//\\/\\\\}
    s=${s//\"/\\\"}
    printf '%s' "$s"
}
load_body=$(printf '{"instrument_id":"%s","start_date":"%s","end_date":"%s","granularity":"%s"}' \
    "$(json_escape "$INSTRUMENT_ID")" \
    "$(json_escape "$START_DATE")" \
    "$(json_escape "$END_DATE")" \
    "$(json_escape "$GRANULARITY")")

log "POST /api/replay/load  strategy_file=$STRATEGY_FILE"
load_resp=$(curl -sS -w '\n%{http_code}' -X POST \
    -H 'Content-Type: application/json' \
    --data "$load_body" \
    "http://127.0.0.1:$PORT/api/replay/load")
load_code=$(echo "$load_resp" | tail -1)
load_body_resp=$(echo "$load_resp" | head -n -1)
echo "$load_body_resp"
trace "load response code=$load_code body=$load_body_resp"
trace "load request body=$load_body"
if [[ "$load_code" != "200" ]]; then
    log "FAIL — /api/replay/load returned $load_code body=$load_body_resp"
    exit 1
fi
log "load OK (HTTP $load_code)"

# /api/replay/load は pane 生成完了まで blocking で 200 を返す契約になっている
# （replay-launch-empty-pane-issue.md 第五原因 / replay-load-start-race-fix-plan.md）。
# 200 が返った時点で AutoGenerateReplayPanes 処理は完了しているので
# sleep は不要。

# ── Step 3: POST /api/replay/start ───────────────────────────────────────────
if [[ -n "$STRATEGY_FILE" ]]; then
    start_body=$(printf '{"instrument_id":"%s","start_date":"%s","end_date":"%s","granularity":"%s","strategy_id":"%s","initial_cash":"%s","strategy_file":"%s"}' \
        "$(json_escape "$INSTRUMENT_ID")" \
        "$(json_escape "$START_DATE")" \
        "$(json_escape "$END_DATE")" \
        "$(json_escape "$GRANULARITY")" \
        "$(json_escape "$STRATEGY_ID")" \
        "$(json_escape "$INITIAL_CASH")" \
        "$(json_escape "$STRATEGY_FILE")")
else
    start_body=$(printf '{"instrument_id":"%s","start_date":"%s","end_date":"%s","granularity":"%s","strategy_id":"%s","initial_cash":"%s"}' \
        "$(json_escape "$INSTRUMENT_ID")" \
        "$(json_escape "$START_DATE")" \
        "$(json_escape "$END_DATE")" \
        "$(json_escape "$GRANULARITY")" \
        "$(json_escape "$STRATEGY_ID")" \
        "$(json_escape "$INITIAL_CASH")")
fi

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
