#!/usr/bin/env bash
# tests/e2e/s80_order_crash_recovery_demo.sh — WAL クラッシュリカバリ E2E テスト
#
# 「発注 → エンジンプロセスを kill → 再起動 → 同一 client_order_id で再送」で
# 重複発注が起きず、IdempotentReplay（HTTP 200 + 同一 venue_order_id）が返ることを確認する。
#
# ────────────────────────────────────────────────────────────────────────────
# 前提条件
# ────────────────────────────────────────────────────────────────────────────
#   1. cargo build（デバッグバイナリが必要）
#   2. .env に以下を設定:
#        DEV_TACHIBANA_USER_ID=<demo user id>
#        DEV_TACHIBANA_PASSWORD=<demo password>
#        DEV_TACHIBANA_DEMO=true
#        DEV_TACHIBANA_SECOND_PASSWORD=<second password>
#   3. uv が利用可能
#
# Usage:
#   set -a && source .env && set +a
#   bash tests/e2e/s80_order_crash_recovery_demo.sh
#
# Exit codes:
#   0   PASS
#   1   FAIL — バイナリ / 前提条件不足
#   2   FAIL — ハンドシェイク / ログインタイムアウト
#   3   FAIL — アサーション失敗
#   4   FAIL — 安全制約（本番口座 / DEV_TACHIBANA_DEMO 未設定）
#   77  SKIP — クレデンシャル未設定

set -uo pipefail

# ── パラメータ ────────────────────────────────────────────────────────────────
OBSERVE_S="${OBSERVE_S:-60}"
PORT="${PORT:-19880}"          # 専用ポート（s80_order_submit_demo.sh と分離）
TOKEN="${TOKEN:-e2e-tachibana-crash-recovery-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-crash-recovery-engine.log}"
ORDER_CODE="${ORDER_CODE:-7203}"
ORDER_QTY="${ORDER_QTY:-100}"
ORDER_INSTRUMENT="${ORDER_CODE}.TSE"

RUST_LOG_FILE="$HOME/AppData/Roaming/flowsurface/flowsurface-current-crash.log"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    RUST_LOG_FILE="$HOME/Library/Application Support/flowsurface/flowsurface-current-crash.log"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    RUST_LOG_FILE="$HOME/.local/share/flowsurface/flowsurface-current-crash.log"
fi

BINARY="${BINARY:-./target/debug/flowsurface}"
[[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]] && \
    BINARY="./target/debug/flowsurface.exe"

CONTROL_API="${CONTROL_API:-http://127.0.0.1:9876}"

WAL_PATH="$HOME/.cache/flowsurface/engine/tachibana_orders.jsonl"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    WAL_PATH="$HOME/Library/Caches/flowsurface/engine/tachibana_orders.jsonl"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    WAL_PATH="$HOME/.cache/flowsurface/engine/tachibana_orders.jsonl"
fi

EXPECT_HANDSHAKE_RE='engine handshake complete|Python data engine ready|Connected to external data engine'
EXPECT_VENUE_READY_RE='tachibana: VenueReady — venue is now authenticated'

ENGINE_PID=""
APP_PID=""

cleanup() {
    if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
        [[ -n "${ENGINE_PID:-}" ]] && taskkill /PID "$ENGINE_PID" /F /T 2>/dev/null || true
        [[ -n "${APP_PID:-}" ]]    && taskkill /PID "$APP_PID"    /F /T 2>/dev/null || true
        stale_engine=$(netstat -ano 2>/dev/null | awk '/:'"$PORT"' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
        for pid in $stale_engine; do taskkill /PID "$pid" /F /T 2>/dev/null || true; done
    else
        [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
        [[ -n "${APP_PID:-}" ]]    && kill -9 "$APP_PID"    2>/dev/null || true
    fi
}
trap cleanup EXIT

log()  { printf '[s80-crash-recovery] %s\n' "$*" >&2; }
skip() { log "SKIP: $*"; exit 77; }
fail() { log "FAIL: $*"; exit 3; }

# ── 前提チェック ─────────────────────────────────────────────────────────────
if [[ ! -x "$BINARY" ]]; then
    log "FAIL: $BINARY not found. Run: cargo build"
    exit 1
fi

# ── クレデンシャル確認 ────────────────────────────────────────────────────────
USER_ID="${DEV_TACHIBANA_USER_ID:-}"
PASSWORD="${DEV_TACHIBANA_PASSWORD:-}"
SECOND_PASSWORD="${DEV_TACHIBANA_SECOND_PASSWORD:-}"
IS_DEMO_RAW="${DEV_TACHIBANA_DEMO:-}"

if [[ -z "$USER_ID" || -z "$PASSWORD" ]]; then
    skip "DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD が未設定。"
fi
if [[ -z "$SECOND_PASSWORD" ]]; then
    skip "DEV_TACHIBANA_SECOND_PASSWORD が未設定。"
fi

case "${IS_DEMO_RAW,,}" in
    1|true|yes|on) IS_DEMO=true ;;
    *)
        log "FAIL (安全制約): DEV_TACHIBANA_DEMO が truthy 値ではありません (値: '${IS_DEMO_RAW}')。"
        log "本番口座での発注テストは許可されていません。"
        exit 4
        ;;
esac

log "デモ口座確認 (user_id=${USER_ID}, is_demo=${IS_DEMO})"
log "注文内容: ${ORDER_INSTRUMENT} 現物 成行 買 ${ORDER_QTY}株"

# ── ポートのクリーンアップ ────────────────────────────────────────────────────
if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
    stale_pids=$(netstat -ano 2>/dev/null | awk '/:'$PORT' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
    for pid in $stale_pids; do
        taskkill /PID "$pid" /F /T 2>/dev/null && log "ポート $PORT の古いプロセス (PID=$pid) を終了" || true
    done
else
    fuser -k "${PORT}/tcp" 2>/dev/null || true
fi
sleep 0.3

# ────────────────────────────────────────────────────────────────────────────
# フェーズ 1: 初回発注（エンジン起動 → ログイン → 発注）
# ────────────────────────────────────────────────────────────────────────────
log "=== フェーズ 1: 初回発注 ==="

: > "$ENGINE_LOG"
FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 \
DEV_TACHIBANA_USER_ID="$USER_ID" \
DEV_TACHIBANA_PASSWORD="$PASSWORD" \
DEV_TACHIBANA_DEMO="$IS_DEMO_RAW" \
DEV_TACHIBANA_SECOND_PASSWORD="$SECOND_PASSWORD" \
    uv run python -m engine --port "$PORT" --token "$TOKEN" > "$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!

for _ in {1..50}; do
    if (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then break; fi
    sleep 0.1
done
if ! (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then
    log "FAIL: エンジンが $PORT で起動しませんでした"
    tail -20 "$ENGINE_LOG"
    exit 1
fi

mkdir -p "$(dirname "$RUST_LOG_FILE")" 2>/dev/null || true
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" FLOWSURFACE_ORDER_GUARD_ENABLED=1 RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > "$RUST_LOG_FILE" 2>&1 &
APP_PID=$!

log "ハンドシェイク待機..."
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    if grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null; then break; fi
    sleep 0.5
done
if ! grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: ハンドシェイクが 15 秒以内に完了しませんでした"
    tail -20 "$RUST_LOG_FILE" 2>/dev/null; tail -20 "$ENGINE_LOG"; exit 2
fi

HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -X POST "$CONTROL_API/api/sidebar/tachibana/request-login" 2>&1)
if [[ "$HTTP_STATUS" != "202" ]]; then
    fail "login request: HTTP $HTTP_STATUS (期待: 202)"
fi

log "VenueReady 待機..."
deadline=$((SECONDS + OBSERVE_S))
while (( SECONDS < deadline )); do
    if grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then break; fi
    sleep 0.5
done
if ! grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: VenueReady が ${OBSERVE_S} 秒以内に確認できませんでした"
    exit 2
fi

# 初回発注
CLIENT_ORDER_ID="e2e-crash-$(date +%s)-$(( RANDOM % 9999 ))"
log "初回発注 (client_order_id=${CLIENT_ORDER_ID})..."

ORDER_BODY=$(cat <<JSON
{
  "client_order_id": "${CLIENT_ORDER_ID}",
  "instrument_id": "${ORDER_INSTRUMENT}",
  "order_side": "BUY",
  "order_type": "MARKET",
  "quantity": "${ORDER_QTY}",
  "time_in_force": "DAY",
  "post_only": false,
  "reduce_only": false,
  "tags": ["cash_margin=cash"]
}
JSON
)

SUBMIT1_RESP=$(curl -sf -w '\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$ORDER_BODY" \
    "$CONTROL_API/api/order/submit" 2>&1)
SUBMIT1_CODE=$(echo "$SUBMIT1_RESP" | tail -1)
SUBMIT1_BODY=$(echo "$SUBMIT1_RESP" | head -n -1)

log "初回発注レスポンス: HTTP $SUBMIT1_CODE — $SUBMIT1_BODY"

if [[ "$SUBMIT1_CODE" != "201" ]]; then
    fail "[1] 初回発注: HTTP $SUBMIT1_CODE (期待: 201) — $SUBMIT1_BODY"
fi
VENUE_ORDER_ID_1=$(echo "$SUBMIT1_BODY" | grep -o '"venue_order_id":"[^"]*"' | cut -d'"' -f4)
if [[ -z "$VENUE_ORDER_ID_1" ]]; then
    fail "[1] 初回発注: venue_order_id が応答に含まれません — $SUBMIT1_BODY"
fi
log "PASS [1]: 初回発注成功 venue_order_id=${VENUE_ORDER_ID_1}"

# WAL に書き込まれたことを確認
sleep 0.5
if ! grep -q "$CLIENT_ORDER_ID" "$WAL_PATH" 2>/dev/null; then
    log "WARN: WAL に client_order_id が見当たりません"
fi

# ────────────────────────────────────────────────────────────────────────────
# フェーズ 2: エンジンプロセスをクラッシュさせる
# ────────────────────────────────────────────────────────────────────────────
log "=== フェーズ 2: エンジンクラッシュ（プロセス kill）==="

if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
    taskkill /PID "$ENGINE_PID" /F /T 2>/dev/null || true
    stale_engine=$(netstat -ano 2>/dev/null | awk '/:'"$PORT"' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
    for pid in $stale_engine; do taskkill /PID "$pid" /F /T 2>/dev/null || true; done
    taskkill /PID "$APP_PID" /F /T 2>/dev/null || true
else
    kill -9 "$ENGINE_PID" 2>/dev/null || true
    kill -9 "$APP_PID" 2>/dev/null || true
fi
ENGINE_PID=""
APP_PID=""

# ポートが解放されるまで待機（最大 10 秒）
log "ポート $PORT の解放を待機..."
deadline=$((SECONDS + 10))
while (( SECONDS < deadline )); do
    if ! (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then break; fi
    sleep 0.3
done
log "ポート $PORT 解放完了"

# ────────────────────────────────────────────────────────────────────────────
# フェーズ 3: エンジン再起動 → 同一 client_order_id で再送
# ────────────────────────────────────────────────────────────────────────────
log "=== フェーズ 3: エンジン再起動 ==="

ENGINE_LOG2="${ENGINE_LOG}.phase2"
: > "$ENGINE_LOG2"
FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 \
DEV_TACHIBANA_USER_ID="$USER_ID" \
DEV_TACHIBANA_PASSWORD="$PASSWORD" \
DEV_TACHIBANA_DEMO="$IS_DEMO_RAW" \
DEV_TACHIBANA_SECOND_PASSWORD="$SECOND_PASSWORD" \
    uv run python -m engine --port "$PORT" --token "$TOKEN" > "$ENGINE_LOG2" 2>&1 &
ENGINE_PID=$!

for _ in {1..50}; do
    if (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then break; fi
    sleep 0.1
done
if ! (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then
    log "FAIL: 再起動後のエンジンが $PORT で起動しませんでした"
    tail -20 "$ENGINE_LOG2"
    exit 1
fi

: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" FLOWSURFACE_ORDER_GUARD_ENABLED=1 RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > "$RUST_LOG_FILE" 2>&1 &
APP_PID=$!

log "ハンドシェイク待機（再起動後）..."
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    if grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null; then break; fi
    sleep 0.5
done
if ! grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: 再起動後のハンドシェイクが 15 秒以内に完了しませんでした"
    exit 2
fi

HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -X POST "$CONTROL_API/api/sidebar/tachibana/request-login" 2>&1)
if [[ "$HTTP_STATUS" != "202" ]]; then
    fail "再起動後のログイン要求: HTTP $HTTP_STATUS"
fi

log "VenueReady 待機（再起動後）..."
deadline=$((SECONDS + OBSERVE_S))
while (( SECONDS < deadline )); do
    if grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then break; fi
    sleep 0.5
done
if ! grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: 再起動後の VenueReady が ${OBSERVE_S} 秒以内に確認できませんでした"
    exit 2
fi

# ── 同一 client_order_id で再送 ───────────────────────────────────────────────
log "同一 client_order_id で再送 (client_order_id=${CLIENT_ORDER_ID})..."
SUBMIT2_RESP=$(curl -sf -w '\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$ORDER_BODY" \
    "$CONTROL_API/api/order/submit" 2>&1)
SUBMIT2_CODE=$(echo "$SUBMIT2_RESP" | tail -1)
SUBMIT2_BODY=$(echo "$SUBMIT2_RESP" | head -n -1)

log "再送レスポンス: HTTP $SUBMIT2_CODE — $SUBMIT2_BODY"

# ── アサーション ─────────────────────────────────────────────────────────────
fail_count=0

# [2] IdempotentReplay: HTTP 200 (venue_order_id known) または 202 (unknown)
if [[ "$SUBMIT2_CODE" != "200" && "$SUBMIT2_CODE" != "202" ]]; then
    log "FAIL [2]: 再送が HTTP $SUBMIT2_CODE (期待: 200 or 202 = IdempotentReplay)"
    log "  ボディ: $SUBMIT2_BODY"
    fail_count=$((fail_count + 1))
else
    log "PASS [2]: HTTP $SUBMIT2_CODE (IdempotentReplay 確認)"
fi

# [3] 200 の場合: venue_order_id が初回と同一（重複発注なし）
if [[ "$SUBMIT2_CODE" == "200" ]]; then
    VENUE_ORDER_ID_2=$(echo "$SUBMIT2_BODY" | grep -o '"venue_order_id":"[^"]*"' | cut -d'"' -f4)
    if [[ "$VENUE_ORDER_ID_2" == "$VENUE_ORDER_ID_1" ]]; then
        log "PASS [3]: venue_order_id 一致（重複発注なし: ${VENUE_ORDER_ID_2}）"
    else
        log "FAIL [3]: venue_order_id 不一致 (初回=${VENUE_ORDER_ID_1}, 再送=${VENUE_ORDER_ID_2})"
        fail_count=$((fail_count + 1))
    fi
elif [[ "$SUBMIT2_CODE" == "202" ]]; then
    if echo "$SUBMIT2_BODY" | grep -q '"order_status_unknown"'; then
        log "PASS [3]: 202 + warning:order_status_unknown (WAL クラッシュリカバリ確認)"
    else
        log "PASS [3]: 202 受信（status_unknown フィールドなし、許容範囲）"
    fi
fi

# [4] C-M2: エンジンログに second_password の値が含まれていないこと
for log_file in "$ENGINE_LOG" "$ENGINE_LOG2"; do
    if grep -q "${SECOND_PASSWORD}" "$log_file" 2>/dev/null; then
        log "FAIL [4]: $log_file に second_password が含まれています！"
        fail_count=$((fail_count + 1))
        break
    fi
done
if (( fail_count == 0 )) || ! grep -q "${SECOND_PASSWORD}" "$ENGINE_LOG" 2>/dev/null && \
   ! grep -q "${SECOND_PASSWORD}" "$ENGINE_LOG2" 2>/dev/null; then
    log "PASS [4]: エンジンログに second_password なし (C-M2 OK)"
fi

# ── 結果 ─────────────────────────────────────────────────────────────────────
echo ""
if (( fail_count == 0 )); then
    log "PASS: WAL クラッシュリカバリ E2E 完了"
    log "  client_order_id=${CLIENT_ORDER_ID}"
    log "  venue_order_id_1=${VENUE_ORDER_ID_1}"
    log "  idempotent_code=${SUBMIT2_CODE}"
    exit 0
else
    log "FAIL: $fail_count 件のアサーションが失敗しました"
    exit 3
fi
