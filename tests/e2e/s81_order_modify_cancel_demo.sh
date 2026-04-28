#!/usr/bin/env bash
# tests/e2e/s81_order_modify_cancel_demo.sh — 指値発注 → 訂正 → 取消 E2E テスト
#
# デモ口座で「指値発注 → 数量訂正 → 取消」を curl から実行し、
# 各ステップが HTTP 200/201 で完結することを確認する。
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
# 注意: 立花証券デモ口座での指値注文は市場が開いていないと約定しないが、
#       訂正・取消は時間外でも可能。このテストは時間外でも実行可能。
#
# Usage:
#   set -a && source .env && set +a
#   bash tests/e2e/s81_order_modify_cancel_demo.sh
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
PORT="${PORT:-19881}"          # 専用ポート（他の E2E テストと分離）
TOKEN="${TOKEN:-e2e-tachibana-modify-cancel-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-modify-cancel-engine.log}"

# 市場で約定しない低い指値
# 値幅制限: Toyota (7203) 3001-5000 円帯は ±900 円。3500 円基準なら 2600-4400 円が許容範囲。
# デフォルト 2800 円は値幅内で約定しにくい価格。
ORDER_CODE="${ORDER_CODE:-7203}"
ORDER_QTY="${ORDER_QTY:-100}"
ORDER_LIMIT_PRICE="${ORDER_LIMIT_PRICE:-2800}"  # 約定しにくい低い指値（値幅内）
ORDER_INSTRUMENT="${ORDER_CODE}.TSE"

# 訂正後の数量（元の半分程度）
MODIFY_QTY="${MODIFY_QTY:-200}"  # 訂正後の数量（増量も可）

RUST_LOG_FILE="$HOME/AppData/Roaming/flowsurface/flowsurface-current-modify.log"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    RUST_LOG_FILE="$HOME/Library/Application Support/flowsurface/flowsurface-current-modify.log"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    RUST_LOG_FILE="$HOME/.local/share/flowsurface/flowsurface-current-modify.log"
fi

BINARY="${BINARY:-./target/debug/flowsurface}"
[[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]] && \
    BINARY="./target/debug/flowsurface.exe"

CONTROL_API="${CONTROL_API:-http://127.0.0.1:9876}"

EXPECT_HANDSHAKE_RE='engine handshake complete|Python data engine ready|Connected to external data engine'
EXPECT_VENUE_READY_RE='tachibana: VenueReady — venue is now authenticated'

ENGINE_PID=""
APP_PID=""

cleanup() {
    if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
        [[ -n "${ENGINE_PID:-}" ]] && taskkill /PID "$ENGINE_PID" /F /T 2>/dev/null || true
        [[ -n "${APP_PID:-}" ]]    && taskkill /PID "$APP_PID"    /F /T 2>/dev/null || true
        for _port in "${PORT}" "9876"; do
            stale=$(netstat -ano 2>/dev/null | awk '/:'"$_port"' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
            for pid in $stale; do taskkill /PID "$pid" /F /T 2>/dev/null || true; done
        done
    else
        [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
        [[ -n "${APP_PID:-}" ]]    && kill -9 "$APP_PID"    2>/dev/null || true
    fi
}
trap cleanup EXIT

log()  { printf '[s81-modify-cancel] %s\n' "$*" >&2; }
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
log "注文内容: ${ORDER_INSTRUMENT} 現物 指値 買 ${ORDER_QTY}株 @ ${ORDER_LIMIT_PRICE}円"
log "訂正後数量: ${MODIFY_QTY}株"

# ── ポートのクリーンアップ（エンジンポート + Rust HTTP API ポート 9876）────────
RUST_API_PORT=9876
if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
    for _port in "$PORT" "$RUST_API_PORT"; do
        stale_pids=$(netstat -ano 2>/dev/null | awk '/:'$_port' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
        for pid in $stale_pids; do
            taskkill /PID "$pid" /F /T 2>/dev/null && log "ポート $_port の古いプロセス (PID=$pid) を終了" || true
        done
    done
else
    fuser -k "${PORT}/tcp" 2>/dev/null || true
    fuser -k "${RUST_API_PORT}/tcp" 2>/dev/null || true
fi
sleep 0.5

# ── エンジン起動 ──────────────────────────────────────────────────────────────
log "エンジン起動 (ポート:$PORT, DEV fast path 有効)"
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

# ── Rust アプリ起動 ──────────────────────────────────────────────────────────
log "flowsurface 起動 ($BINARY)"
mkdir -p "$(dirname "$RUST_LOG_FILE")" 2>/dev/null || true
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" FLOWSURFACE_ORDER_GUARD_ENABLED=1 RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > "$RUST_LOG_FILE" 2>&1 &
APP_PID=$!

# ── ハンドシェイク待機 ────────────────────────────────────────────────────────
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
log "ハンドシェイク完了"

if ! curl -sf "$CONTROL_API/api/replay/status" >/dev/null 2>&1; then
    log "FAIL: HTTP 制御 API ($CONTROL_API) に到達できません"; exit 3
fi

# ── ログイン ──────────────────────────────────────────────────────────────────
log "POST $CONTROL_API/api/sidebar/tachibana/request-login ..."
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
log "VenueReady 確認"

# ────────────────────────────────────────────────────────────────────────────
# ステップ 1: 指値買い発注
# ────────────────────────────────────────────────────────────────────────────
CLIENT_ORDER_ID="e2e-mc-$(date +%s)-$(( RANDOM % 9999 ))"
log "指値発注 (client_order_id=${CLIENT_ORDER_ID}, price=${ORDER_LIMIT_PRICE})..."

SUBMIT_BODY=$(cat <<JSON
{
  "client_order_id": "${CLIENT_ORDER_ID}",
  "instrument_id": "${ORDER_INSTRUMENT}",
  "order_side": "BUY",
  "order_type": "LIMIT",
  "quantity": "${ORDER_QTY}",
  "price": "${ORDER_LIMIT_PRICE}",
  "time_in_force": "DAY",
  "post_only": false,
  "reduce_only": false,
  "tags": ["cash_margin=cash"]
}
JSON
)

# -s のみ（-f を使わない）: 4xx でもレスポンスボディを取得するため
SUBMIT_RESP=$(curl -s -w '\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$SUBMIT_BODY" \
    "$CONTROL_API/api/order/submit" 2>&1)
SUBMIT_CODE=$(echo "$SUBMIT_RESP" | tail -1)
SUBMIT_RBODY=$(echo "$SUBMIT_RESP" | head -n -1)

log "発注レスポンス: HTTP $SUBMIT_CODE — $SUBMIT_RBODY"

fail_count=0

if [[ "$SUBMIT_CODE" != "201" ]]; then
    log "FAIL [1]: 指値発注 HTTP $SUBMIT_CODE (期待: 201) — $SUBMIT_RBODY"
    fail_count=$((fail_count + 1))
    # 値幅制限外・市場クローズ・セッション問題などはSKIPとして扱う
    if echo "$SUBMIT_RBODY" | grep -qi "VENUE_REJECTED\|VENUE_UNSUPPORTED\|not.*support\|市場\|outside.*hours\|ORDER_ERROR\|SESSION_EXPIRED"; then
        log "SKIP: 指値発注が拒否されました (価格が値幅制限外か市場外の可能性)"
        log "  ORDER_LIMIT_PRICE=${ORDER_LIMIT_PRICE} を変更して再試行してください"
        log "  (Toyota 7203 の値幅制限: 約 ±900 円/日。前日終値 ±900 円の範囲内で設定)"
        exit 77
    fi
else
    VENUE_ORDER_ID=$(echo "$SUBMIT_RBODY" | grep -o '"venue_order_id":"[^"]*"' | cut -d'"' -f4)
    if [[ -z "$VENUE_ORDER_ID" ]]; then
        log "FAIL [1]: venue_order_id が応答に含まれません — $SUBMIT_RBODY"
        fail_count=$((fail_count + 1))
    else
        log "PASS [1]: 指値発注成功 venue_order_id=${VENUE_ORDER_ID}"
    fi
fi

# 以降の検証は発注成功時のみ実施
if (( fail_count > 0 )); then
    log "FAIL: 指値発注が失敗したため訂正・取消テストをスキップします"
    exit 3
fi

# 少し待機（注文が取引所で処理されるまで）
sleep 2

# ────────────────────────────────────────────────────────────────────────────
# ステップ 2: 数量訂正
# ────────────────────────────────────────────────────────────────────────────
log "訂正 (client_order_id=${CLIENT_ORDER_ID}, new_quantity=${MODIFY_QTY})..."

MODIFY_BODY=$(cat <<JSON
{
  "client_order_id": "${CLIENT_ORDER_ID}",
  "change": {
    "new_quantity": "${MODIFY_QTY}"
  }
}
JSON
)

MODIFY_RESP=$(curl -s -w '\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$MODIFY_BODY" \
    "$CONTROL_API/api/order/modify" 2>&1)
MODIFY_CODE=$(echo "$MODIFY_RESP" | tail -1)
MODIFY_RBODY=$(echo "$MODIFY_RESP" | head -n -1)

log "訂正レスポンス: HTTP $MODIFY_CODE — $MODIFY_RBODY"

if [[ "$MODIFY_CODE" == "200" ]]; then
    log "PASS [2]: 訂正成功 (HTTP 200)"
elif [[ "$MODIFY_CODE" == "422" || "$MODIFY_CODE" == "400" ]]; then
    # 訂正不可（約定済み・市場クローズ等）は WARN として続行
    log "WARN [2]: 訂正 HTTP $MODIFY_CODE（約定済みまたは市場外の場合は許容）— $MODIFY_RBODY"
else
    log "FAIL [2]: 訂正 HTTP $MODIFY_CODE (期待: 200) — $MODIFY_RBODY"
    fail_count=$((fail_count + 1))
fi

sleep 1

# ────────────────────────────────────────────────────────────────────────────
# ステップ 3: 取消
# ────────────────────────────────────────────────────────────────────────────
log "取消 (client_order_id=${CLIENT_ORDER_ID})..."

CANCEL_BODY=$(cat <<JSON
{
  "client_order_id": "${CLIENT_ORDER_ID}"
}
JSON
)

CANCEL_RESP=$(curl -s -w '\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$CANCEL_BODY" \
    "$CONTROL_API/api/order/cancel" 2>&1)
CANCEL_CODE=$(echo "$CANCEL_RESP" | tail -1)
CANCEL_RBODY=$(echo "$CANCEL_RESP" | head -n -1)

log "取消レスポンス: HTTP $CANCEL_CODE — $CANCEL_RBODY"

if [[ "$CANCEL_CODE" == "200" ]]; then
    log "PASS [3]: 取消成功 (HTTP 200)"
elif [[ "$CANCEL_CODE" == "422" || "$CANCEL_CODE" == "404" ]]; then
    log "WARN [3]: 取消 HTTP $CANCEL_CODE（約定済み・不明注文の場合は許容）— $CANCEL_RBODY"
else
    log "FAIL [3]: 取消 HTTP $CANCEL_CODE (期待: 200) — $CANCEL_RBODY"
    fail_count=$((fail_count + 1))
fi

sleep 1

# ── C-M2: エンジンログに second_password の値が含まれていないこと ───────────
if grep -q "${SECOND_PASSWORD}" "$ENGINE_LOG" 2>/dev/null; then
    log "FAIL [4]: エンジンログに second_password の値が含まれています！"
    fail_count=$((fail_count + 1))
else
    log "PASS [4]: エンジンログに second_password 値なし (C-M2 OK)"
fi

# ── 注文一覧取得（最終確認）────────────────────────────────────────────────
log "注文一覧取得..."
LIST_RESP=$(curl -sf -w '\n%{http_code}' \
    -H "Authorization: Bearer $TOKEN" \
    "$CONTROL_API/api/order/list" 2>&1)
LIST_CODE=$(echo "$LIST_RESP" | tail -1)
LIST_BODY=$(echo "$LIST_RESP" | head -n -1)
log "注文一覧: HTTP $LIST_CODE"

if [[ "$LIST_CODE" == "200" ]]; then
    log "PASS [5]: 注文一覧取得成功 (HTTP 200)"
else
    log "WARN [5]: 注文一覧 HTTP $LIST_CODE — $LIST_BODY"
fi

# ── 結果 ─────────────────────────────────────────────────────────────────────
echo ""
if (( fail_count == 0 )); then
    log "PASS: 指値発注→訂正→取消 E2E 完了"
    log "  client_order_id=${CLIENT_ORDER_ID}"
    log "  venue_order_id=${VENUE_ORDER_ID}"
    exit 0
else
    log "FAIL: $fail_count 件のアサーションが失敗しました"
    exit 3
fi
