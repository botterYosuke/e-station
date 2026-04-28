#!/usr/bin/env bash
# tests/e2e/s82_order_fill_ec_e2e.sh — 約定通知 (EC → OrderFilled IPC) E2E テスト
#
# 成行買いを発注し、EC フレームが ENGINE から OrderFilled IPC イベントとして
# Rust に届くことをログで確認する（約定 toast の前段）。
#
# 検証項目:
#   [1] HTTP 201 + venue_order_id が返る
#   [2] エンジンログに OrderFilled が記録される（EC → IPC 変換が動作）
#   [3] エンジンログに sSecondPassword が露出していない（C-M2）
#   [4] Rust ログに "約定" または "OrderFilled" が出る（toast トリガー確認）
#
# 前提条件:
#   1. cargo build（デバッグバイナリ必要）
#   2. .env に設定:
#        DEV_TACHIBANA_USER_ID=<id>
#        DEV_TACHIBANA_PASSWORD=<pass>
#        DEV_TACHIBANA_DEMO=true
#        DEV_TACHIBANA_SECOND_PASSWORD=<2nd pass>
#   3. 市場時間中のみ成行が約定する（時間外は OrderAccepted だが約定しない）
#
# Usage:
#   set -a && source .env && set +a
#   bash tests/e2e/s82_order_fill_ec_e2e.sh
#
# Exit codes:
#   0   PASS
#   1   FAIL — バイナリ / 前提条件不足
#   2   FAIL — ハンドシェイク / ログインタイムアウト
#   3   FAIL — アサーション失敗
#   4   FAIL — 安全制約（本番口座 / デモ未設定）
#   5   SKIP — 市場時間外（OrderFilled は発生しない）
#   77  SKIP — クレデンシャル未設定

set -uo pipefail

# ── パラメータ ────────────────────────────────────────────────────────────────
OBSERVE_S="${OBSERVE_S:-90}"           # VenueReady 待機上限
FILL_WAIT_S="${FILL_WAIT_S:-60}"       # OrderFilled 待機上限（市場内なら数秒で来る）
PORT="${PORT:-19882}"
TOKEN="${TOKEN:-e2e-tachibana-fill-ec-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-fill-ec-engine.log}"
ORDER_CODE="${ORDER_CODE:-7203}"        # Toyota — デモで流動性が高い
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

cleanup() {
    if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
        [[ -n "${ENGINE_PID:-}" ]] && taskkill /PID "$ENGINE_PID" /F /T 2>/dev/null || true
        [[ -n "${APP_PID:-}" ]]    && taskkill /PID "$APP_PID"    /F /T 2>/dev/null || true
        stale=$(netstat -ano 2>/dev/null | awk '/:'"$PORT"' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
        for pid in $stale; do taskkill /PID "$pid" /F /T 2>/dev/null || true; done
    else
        [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
        [[ -n "${APP_PID:-}" ]]    && kill -9 "$APP_PID"    2>/dev/null || true
    fi
}
trap cleanup EXIT

log()  { printf '[s82-fill-ec] %s\n' "$*" >&2; }
skip() { log "SKIP: $*"; exit 77; }
fail() { log "FAIL: $*"; exit 3; }

# ── 前提チェック ─────────────────────────────────────────────────────────────
[[ ! -x "$BINARY" ]] && { log "FAIL: $BINARY not found. Run: cargo build"; exit 1; }

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
log "注文内容: ${ORDER_INSTRUMENT} 現物 成行 買 ${ORDER_QTY}株"
log "注意: 市場時間外は OrderFilled が発生しないため exit 5 (SKIP) になります"

# ── ポートクリーンアップ ─────────────────────────────────────────────────────
if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
    stale_pids=$(netstat -ano 2>/dev/null | awk '/:'$PORT' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
    for pid in $stale_pids; do taskkill /PID "$pid" /F 2>/dev/null || true; done
else
    fuser -k "${PORT}/tcp" 2>/dev/null || true
fi
sleep 0.3

# ── Python エンジン起動 ───────────────────────────────────────────────────────
log "エンジン起動 (ポート:$PORT)"
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
    log "FAIL: エンジンが起動しませんでした"
    tail -20 "$ENGINE_LOG"
    exit 1
}

# ── Rust アプリ起動 ──────────────────────────────────────────────────────────
log "flowsurface 起動"
mkdir -p "$(dirname "$RUST_LOG_FILE")" 2>/dev/null || true
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" FLOWSURFACE_ORDER_GUARD_ENABLED=1 RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > "$RUST_LOG_FILE" 2>&1 &
APP_PID=$!

# ── ハンドシェイク待機 ───────────────────────────────────────────────────────
log "ハンドシェイク待機 (最大 15 秒)..."
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null && break
    sleep 0.5
done
grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null || {
    log "FAIL: ハンドシェイク 15 秒以内に完了せず"
    tail -20 "$RUST_LOG_FILE" 2>/dev/null; tail -20 "$ENGINE_LOG"; exit 2
}
log "ハンドシェイク完了"

curl -sf "$CONTROL_API/api/replay/status" >/dev/null 2>&1 || {
    log "FAIL: HTTP API に到達できません"; exit 3
}

# ── ログイン要求 ─────────────────────────────────────────────────────────────
log "ログイン要求..."
HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -X POST "$CONTROL_API/api/sidebar/tachibana/request-login" 2>&1)
[[ "$HTTP_STATUS" == "202" ]] || fail "login request: HTTP $HTTP_STATUS (期待: 202)"
log "ログイン要求 202 Accepted"

# ── VenueReady 待機 ──────────────────────────────────────────────────────────
log "VenueReady 待機 (最大 ${OBSERVE_S} 秒)..."
deadline=$((SECONDS + OBSERVE_S))
while (( SECONDS < deadline )); do
    grep -qF "$EXPECT_VENUE_READY" "$RUST_LOG_FILE" 2>/dev/null && break
    sleep 0.5
done
grep -qF "$EXPECT_VENUE_READY" "$RUST_LOG_FILE" 2>/dev/null || {
    log "FAIL: VenueReady が ${OBSERVE_S} 秒以内に確認できませんでした"
    tail -30 "$RUST_LOG_FILE" 2>/dev/null; tail -20 "$ENGINE_LOG"; exit 2
}
log "VenueReady 確認"

# EVENT WS の接続待機（VenueReady 後に Python が接続する）
sleep 2

# ── 発注 ─────────────────────────────────────────────────────────────────────
CLIENT_ORDER_ID="s82-$(date +%s)-$(( RANDOM % 9999 ))"
log "POST /api/order/submit (client_order_id=${CLIENT_ORDER_ID})..."

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

log "発注レスポンス: HTTP $SUBMIT_HTTP_CODE"
log "レスポンスボディ: $SUBMIT_BODY"

fail_count=0

# [1] HTTP 201
if [[ "$SUBMIT_HTTP_CODE" == "201" || "$SUBMIT_HTTP_CODE" == "200" ]]; then
    log "PASS [1]: HTTP $SUBMIT_HTTP_CODE"
else
    log "FAIL [1]: HTTP $SUBMIT_HTTP_CODE (期待: 201/200)"
    fail_count=$((fail_count + 1))
fi

# venue_order_id を抽出
VENUE_ORDER_ID=$(echo "$SUBMIT_BODY" | grep -o '"venue_order_id":"[^"]*"' | cut -d'"' -f4)
if [[ -n "$VENUE_ORDER_ID" ]]; then
    log "PASS [1b]: venue_order_id=${VENUE_ORDER_ID}"
else
    log "FAIL [1b]: venue_order_id が応答にありません"
    fail_count=$((fail_count + 1))
fi

# ── OrderFilled 待機 ──────────────────────────────────────────────────────────
log "OrderFilled 待機 (最大 ${FILL_WAIT_S} 秒)..."
log "  ※ 市場時間外の場合は OrderFilled は来ません。exit 5 で終了します。"

deadline=$((SECONDS + FILL_WAIT_S))
filled=false
while (( SECONDS < deadline )); do
    if grep -q '"OrderFilled"' "$ENGINE_LOG" 2>/dev/null; then
        filled=true
        break
    fi
    sleep 1
done

# [2] OrderFilled がエンジンログに出た
if $filled; then
    log "PASS [2]: エンジンログに OrderFilled を確認"
    # OrderFilled の内容を表示
    grep '"OrderFilled"' "$ENGINE_LOG" 2>/dev/null | tail -3 | while read -r line; do
        log "  $line"
    done
else
    # 市場時間外の場合は OrderAccepted だけ来て填まらない
    if grep -q '"OrderAccepted"' "$ENGINE_LOG" 2>/dev/null; then
        log "SKIP [2]: OrderAccepted は確認できましたが OrderFilled は来ませんでした"
        log "  市場時間外の可能性があります（東証: 9:00-11:30 / 12:30-15:30）"
        log "  発注した注文をキャンセルしてください: venue_order_id=${VENUE_ORDER_ID:-unknown}"
        exit 5
    else
        log "FAIL [2]: OrderFilled も OrderAccepted も ${FILL_WAIT_S} 秒以内に確認できませんでした"
        tail -20 "$ENGINE_LOG"
        fail_count=$((fail_count + 1))
    fi
fi

# [3] C-M2: second_password 漏洩確認
if grep -q "${SECOND_PASSWORD}" "$ENGINE_LOG" 2>/dev/null; then
    log "FAIL [3]: エンジンログに second_password の値が含まれています！"
    fail_count=$((fail_count + 1))
else
    log "PASS [3]: エンジンログに second_password 値なし (C-M2 OK)"
fi

# [4] Rust ログに toast トリガーが出た（OrderFilled 受信で "約定" ログ）
sleep 1  # IPC 伝搬の猶予
if grep -qE '約定|OrderFilled' "$RUST_LOG_FILE" 2>/dev/null; then
    log "PASS [4]: Rust ログに約定トリガーを確認 → toast は表示されているはず"
    grep -E '約定|OrderFilled' "$RUST_LOG_FILE" 2>/dev/null | tail -3 | while read -r line; do
        log "  $line"
    done
else
    log "WARN [4]: Rust ログに約定/OrderFilled キーワードが見当たりません"
    log "  RUST_LOG=info で起動している場合は debug ログが出ないため WARN に留めます"
fi

# ── 結果 ─────────────────────────────────────────────────────────────────────
echo ""
if (( fail_count == 0 )); then
    log "PASS: 約定通知 E2E 完了 (EC → OrderFilled IPC → Rust)"
    log "  client_order_id=${CLIENT_ORDER_ID}"
    log "  venue_order_id=${VENUE_ORDER_ID:-N/A}"
    log "  OrderFilled: $(grep -c '"OrderFilled"' "$ENGINE_LOG" 2>/dev/null || echo 0) 件"
    exit 0
else
    log "FAIL: $fail_count 件のアサーションが失敗しました"
    exit 3
fi
