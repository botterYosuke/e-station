#!/usr/bin/env bash
# tests/e2e/s83_ec_dedup_e2e.sh — EC 重複検知 E2E テスト (fault injection)
#
# 約定通知の重複検知（TachibanaEventClient._seen）が実際に動いていることを
# Python エンジンの再起動で再現する。
#
# シナリオ:
#   1. 成行買い発注 → OrderFilled が 1 回記録される
#   2. Python エンジンプロセスを強制終了（WS 切断）
#   3. エンジン再起動 → 再ログイン → EVENT WS 再接続
#   4. 同一 (venue_order_id, trade_id) の EC フレームが再送される場合でも
#      OrderFilled が追加で記録されないことを確認
#
# 検証項目:
#   [1] 発注: HTTP 201 + venue_order_id
#   [2] OrderFilled が 1 回記録される
#   [3] エンジン再起動後、エンジンログの OrderFilled が増えない
#   [4] C-M2: second_password 漏洩なし
#
# 注意:
#   再起動後の再送 EC は立花サーバーの実装次第で来ない場合がある。
#   "再接続後に同一 EC が再送されるか" はデモ環境で保証されていない。
#   そのため本テストは:
#     - 再起動後の OrderFilled が増加しないことを確認
#     - 増加なし = PASS（重複検知が動いているか、単に再送がないかのいずれか）
#   として扱う。重複検知単体は test_ec_dedup.py でユニット検証済み。
#
# 前提条件:
#   1. cargo build
#   2. .env:
#        DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD / DEV_TACHIBANA_DEMO=true
#        DEV_TACHIBANA_SECOND_PASSWORD
#   3. 市場時間中のみ有効（成行が約定しないと OrderFilled が来ない）
#
# Usage:
#   set -a && source .env && set +a
#   bash tests/e2e/s83_ec_dedup_e2e.sh
#
# Exit codes:
#   0   PASS
#   1   FAIL — バイナリ / 前提条件不足
#   2   FAIL — ハンドシェイク / ログインタイムアウト
#   3   FAIL — アサーション失敗
#   4   FAIL — 安全制約
#   5   SKIP — 市場時間外 (OrderFilled が来なかった)
#   77  SKIP — クレデンシャル未設定

set -uo pipefail

# ── パラメータ ────────────────────────────────────────────────────────────────
OBSERVE_S="${OBSERVE_S:-90}"
FILL_WAIT_S="${FILL_WAIT_S:-60}"
RESTART_WAIT_S="${RESTART_WAIT_S:-30}"   # 再起動後の追加 OrderFilled 待機秒
PORT="${PORT:-19883}"
TOKEN="${TOKEN:-e2e-tachibana-ec-dedup-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-ec-dedup-engine.log}"
ENGINE_LOG2="${ENGINE_LOG2:-/tmp/flowsurface-e2e-ec-dedup-engine2.log}"
ORDER_CODE="${ORDER_CODE:-7203}"
ORDER_QTY="${ORDER_QTY:-100}"
ORDER_INSTRUMENT="${ORDER_CODE}.TSE"

RUST_LOG_FILE="$HOME/AppData/Roaming/flowsurface/flowsurface-current.log"
[[ "${OSTYPE:-}" == darwin* ]] && RUST_LOG_FILE="$HOME/Library/Application Support/flowsurface/flowsurface-current.log"
[[ "${OSTYPE:-}" == linux* ]]  && RUST_LOG_FILE="$HOME/.local/share/flowsurface/flowsurface-current.log"

BINARY="${BINARY:-./target/debug/flowsurface}"
[[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]] && \
    BINARY="./target/debug/flowsurface.exe"

CONTROL_API="${CONTROL_API:-http://127.0.0.1:9876}"

EXPECT_HANDSHAKE_RE='engine handshake complete|Python data engine ready|Connected to external data engine'
EXPECT_VENUE_READY='tachibana: VenueReady — venue is now authenticated'

ENGINE_PID=""
APP_PID=""

cleanup() {
    if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
        [[ -n "$ENGINE_PID" ]] && taskkill /PID "$ENGINE_PID" /F /T 2>/dev/null || true
        [[ -n "$APP_PID" ]]    && taskkill /PID "$APP_PID"    /F /T 2>/dev/null || true
        stale=$(netstat -ano 2>/dev/null | awk '/:'"$PORT"' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
        for pid in $stale; do taskkill /PID "$pid" /F /T 2>/dev/null || true; done
    else
        [[ -n "$ENGINE_PID" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
        [[ -n "$APP_PID" ]]    && kill -9 "$APP_PID"    2>/dev/null || true
    fi
}
trap cleanup EXIT

log()  { printf '[s83-ec-dedup] %s\n' "$*" >&2; }
skip() { log "SKIP: $*"; exit 77; }
fail() { log "FAIL: $*"; exit 3; }

# ── 前提チェック ─────────────────────────────────────────────────────────────
[[ ! -x "$BINARY" ]] && { log "FAIL: $BINARY not found"; exit 1; }

USER_ID="${DEV_TACHIBANA_USER_ID:-}"
PASSWORD="${DEV_TACHIBANA_PASSWORD:-}"
SECOND_PASSWORD="${DEV_TACHIBANA_SECOND_PASSWORD:-}"
IS_DEMO_RAW="${DEV_TACHIBANA_DEMO:-}"

[[ -z "$USER_ID" || -z "$PASSWORD" ]] && skip "DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD 未設定"
[[ -z "$SECOND_PASSWORD" ]]           && skip "DEV_TACHIBANA_SECOND_PASSWORD 未設定"

case "${IS_DEMO_RAW,,}" in
    1|true|yes|on) ;;
    *) log "FAIL (安全制約): DEV_TACHIBANA_DEMO が truthy ではありません"; exit 4 ;;
esac

log "デモ口座確認 (user_id=${USER_ID})"

# ── ポートクリーンアップ ─────────────────────────────────────────────────────
if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
    stale_pids=$(netstat -ano 2>/dev/null | awk '/:'$PORT' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
    for pid in $stale_pids; do taskkill /PID "$pid" /F 2>/dev/null || true; done
else
    fuser -k "${PORT}/tcp" 2>/dev/null || true
fi
sleep 0.3

# ─────────────────────────────────────────────────────────────────────────────
# Phase A: 初回起動 → 発注 → OrderFilled 確認
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase A: 初回起動・発注・OrderFilled 確認 ==="

: > "$ENGINE_LOG"
FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 \
DEV_TACHIBANA_USER_ID="$USER_ID" \
DEV_TACHIBANA_PASSWORD="$PASSWORD" \
DEV_TACHIBANA_DEMO="$IS_DEMO_RAW" \
DEV_TACHIBANA_SECOND_PASSWORD="$SECOND_PASSWORD" \
    uv run python -m engine --port "$PORT" --token "$TOKEN" > "$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!

for _ in {1..50}; do
    (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break
    sleep 0.1
done
(echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null || {
    log "FAIL: エンジン起動失敗"; tail -10 "$ENGINE_LOG"; exit 1
}

mkdir -p "$(dirname "$RUST_LOG_FILE")" 2>/dev/null || true
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" FLOWSURFACE_ORDER_GUARD_ENABLED=1 RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > "$RUST_LOG_FILE" 2>&1 &
APP_PID=$!

log "ハンドシェイク待機..."
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null && break
    sleep 0.5
done
grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null || {
    log "FAIL: ハンドシェイクタイムアウト"; exit 2
}

curl -sf "$CONTROL_API/api/replay/status" >/dev/null 2>&1 || { log "FAIL: HTTP API 不到達"; exit 3; }

HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -X POST "$CONTROL_API/api/sidebar/tachibana/request-login" 2>&1)
[[ "$HTTP_STATUS" == "202" ]] || fail "login HTTP $HTTP_STATUS"

log "VenueReady 待機..."
deadline=$((SECONDS + OBSERVE_S))
while (( SECONDS < deadline )); do
    grep -qF "$EXPECT_VENUE_READY" "$RUST_LOG_FILE" 2>/dev/null && break
    sleep 0.5
done
grep -qF "$EXPECT_VENUE_READY" "$RUST_LOG_FILE" 2>/dev/null || {
    log "FAIL: VenueReady タイムアウト"; exit 2
}
log "VenueReady 確認"
sleep 2  # EVENT WS 接続待機

CLIENT_ORDER_ID="s83-$(date +%s)-$(( RANDOM % 9999 ))"
log "発注: ${ORDER_INSTRUMENT} 現物 成行 買 ${ORDER_QTY} 株 (client_order_id=${CLIENT_ORDER_ID})"

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

SUBMIT_RESPONSE=$(curl -sf -w '\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$ORDER_BODY" \
    "$CONTROL_API/api/order/submit" 2>&1)
SUBMIT_HTTP_CODE=$(echo "$SUBMIT_RESPONSE" | tail -1)
SUBMIT_BODY=$(echo "$SUBMIT_RESPONSE" | head -n -1)

VENUE_ORDER_ID=$(echo "$SUBMIT_BODY" | grep -o '"venue_order_id":"[^"]*"' | cut -d'"' -f4)
log "発注: HTTP $SUBMIT_HTTP_CODE / venue_order_id=${VENUE_ORDER_ID:-N/A}"

fail_count=0

if [[ "$SUBMIT_HTTP_CODE" == "201" || "$SUBMIT_HTTP_CODE" == "200" ]]; then
    log "PASS [1]: 発注成功 HTTP $SUBMIT_HTTP_CODE"
else
    log "FAIL [1]: HTTP $SUBMIT_HTTP_CODE"
    fail_count=$((fail_count + 1))
fi

# OrderFilled 初回待機
log "OrderFilled 待機 (最大 ${FILL_WAIT_S} 秒)..."
deadline=$((SECONDS + FILL_WAIT_S))
filled_count_before=0
while (( SECONDS < deadline )); do
    filled_count_before=$(grep -c '"OrderFilled"' "$ENGINE_LOG" 2>/dev/null || echo 0)
    (( filled_count_before > 0 )) && break
    sleep 1
done

if (( filled_count_before > 0 )); then
    log "PASS [2]: OrderFilled ${filled_count_before} 件を確認"
else
    if grep -q '"OrderAccepted"' "$ENGINE_LOG" 2>/dev/null; then
        log "SKIP [2]: OrderAccepted あり・OrderFilled なし → 市場時間外の可能性"
        log "  発注した注文をキャンセルしてください: venue_order_id=${VENUE_ORDER_ID:-unknown}"
        exit 5
    fi
    log "FAIL [2]: OrderFilled も OrderAccepted も来ませんでした"
    fail_count=$((fail_count + 1))
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase B: エンジン再起動 → OrderFilled が増加しないことを確認
# ─────────────────────────────────────────────────────────────────────────────
log "=== Phase B: エンジン再起動（fault injection） ==="

# Python エンジンを強制終了
if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
    taskkill /PID "$ENGINE_PID" /F /T 2>/dev/null || true
else
    kill -9 "$ENGINE_PID" 2>/dev/null || true
fi
log "エンジン PID $ENGINE_PID を強制終了"
sleep 1

# ポートが解放されるまで待機
for _ in {1..20}; do
    (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null || break
    sleep 0.3
done

# 再起動（別ログファイル）
log "エンジン再起動..."
: > "$ENGINE_LOG2"
FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 \
DEV_TACHIBANA_USER_ID="$USER_ID" \
DEV_TACHIBANA_PASSWORD="$PASSWORD" \
DEV_TACHIBANA_DEMO="$IS_DEMO_RAW" \
DEV_TACHIBANA_SECOND_PASSWORD="$SECOND_PASSWORD" \
    uv run python -m engine --port "$PORT" --token "$TOKEN" >> "$ENGINE_LOG2" 2>&1 &
ENGINE_PID=$!

for _ in {1..50}; do
    (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break
    sleep 0.2
done
log "エンジン再起動完了 (PID $ENGINE_PID)"

# Rust が自動再接続するまで待機
log "Rust 再接続・再ログイン待機 (最大 ${OBSERVE_S} 秒)..."
deadline=$((SECONDS + OBSERVE_S))
while (( SECONDS < deadline )); do
    grep -qF "$EXPECT_VENUE_READY" "$RUST_LOG_FILE" 2>/dev/null && break
    # 再起動後の VenueReady は Rust 側ログに追記される
    sleep 1
done

# VenueReady（2回目）を確認 — grep -c で出現回数
venue_ready_count=$(grep -c "$EXPECT_VENUE_READY" "$RUST_LOG_FILE" 2>/dev/null || echo 0)
if (( venue_ready_count >= 2 )); then
    log "PASS: VenueReady ${venue_ready_count} 回確認（再接続成功）"
elif (( venue_ready_count == 1 )); then
    log "WARN: VenueReady が 1 回のみ（再接続前の Rust ログが残っている可能性）"
fi

# 再起動後に EVENT WS が再接続してしばらく待機
sleep "$RESTART_WAIT_S"
log "${RESTART_WAIT_S} 秒待機完了"

# [3] OrderFilled が増えていないことを確認（両ログを合算）
filled_count_engine1=$(grep -c '"OrderFilled"' "$ENGINE_LOG"  2>/dev/null || echo 0)
filled_count_engine2=$(grep -c '"OrderFilled"' "$ENGINE_LOG2" 2>/dev/null || echo 0)
total_filled=$((filled_count_engine1 + filled_count_engine2))

log "OrderFilled 件数: エンジン1=${filled_count_engine1} エンジン2=${filled_count_engine2} 合計=${total_filled}"

if (( total_filled <= filled_count_before )); then
    log "PASS [3]: 再起動後に OrderFilled が増加していない"
    log "  (重複検知 _seen が機能、または立花サーバーが再送しなかった)"
else
    log "FAIL [3]: 再起動後に OrderFilled が増加しました"
    log "  再起動前: ${filled_count_before}, 合計: ${total_filled}"
    log "  (重複検知が機能していない可能性)"
    fail_count=$((fail_count + 1))
fi

# [4] C-M2 確認（両ログ合算）
secret_leak=false
grep -q "${SECOND_PASSWORD}" "$ENGINE_LOG"  2>/dev/null && secret_leak=true
grep -q "${SECOND_PASSWORD}" "$ENGINE_LOG2" 2>/dev/null && secret_leak=true
if $secret_leak; then
    log "FAIL [4]: エンジンログに second_password の値が含まれています！"
    fail_count=$((fail_count + 1))
else
    log "PASS [4]: second_password 漏洩なし (C-M2 OK)"
fi

# ── 結果 ─────────────────────────────────────────────────────────────────────
echo ""
if (( fail_count == 0 )); then
    log "PASS: EC 重複検知 E2E 完了"
    log "  client_order_id=${CLIENT_ORDER_ID}"
    log "  venue_order_id=${VENUE_ORDER_ID:-N/A}"
    log "  OrderFilled 合計: ${total_filled} 件 (期待: ${filled_count_before} 件以下)"
    exit 0
else
    log "FAIL: $fail_count 件のアサーションが失敗しました"
    exit 3
fi
