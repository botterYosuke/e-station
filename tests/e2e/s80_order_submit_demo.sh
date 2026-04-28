#!/usr/bin/env bash
# tests/e2e/s80_order_submit_demo.sh — 立花デモ口座 現物成行買い E2E テスト
#
# 「POST /api/order/submit」 → Python エンジン → 立花デモ API → OrderAccepted の
# フルパスを端対端で確認する。第二暗証番号は DEV_TACHIBANA_SECOND_PASSWORD から
# 自動注入（iced modal 不要）。
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
#   bash tests/e2e/s80_order_submit_demo.sh
#
#   # 別銘柄・数量を指定:
#   ORDER_CODE=9984 ORDER_QTY=100 bash tests/e2e/s80_order_submit_demo.sh
#
# Exit codes:
#   0   PASS
#   1   FAIL — バイナリ / 前提条件不足
#   2   FAIL — ハンドシェイク / ログインタイムアウト
#   3   FAIL — アサーション失敗
#   4   FAIL — 安全制約（本番口座 / DEV_TACHIBANA_DEMO 未設定）
#   77  SKIP — クレデンシャル未設定（通常 CI / オフライン）

set -uo pipefail

# ── パラメータ ────────────────────────────────────────────────────────────────
OBSERVE_S="${OBSERVE_S:-60}"
PORT="${PORT:-19879}"       # 19876=smoke, 19877=relogin, 19878=demo-login, 19879=order-submit
TOKEN="${TOKEN:-e2e-tachibana-order-submit-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-order-submit-engine.log}"
ORDER_CODE="${ORDER_CODE:-7203}"      # Toyota (流動性が高いデモ銘柄)
ORDER_QTY="${ORDER_QTY:-100}"        # 単元株
ORDER_INSTRUMENT="${ORDER_CODE}.TSE"

RUST_LOG_FILE="$HOME/AppData/Roaming/flowsurface/flowsurface-current.log"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    RUST_LOG_FILE="$HOME/Library/Application Support/flowsurface/flowsurface-current.log"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    RUST_LOG_FILE="$HOME/.local/share/flowsurface/flowsurface-current.log"
fi

BINARY="${BINARY:-./target/debug/flowsurface}"
[[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]] && \
    BINARY="./target/debug/flowsurface.exe"

CONTROL_API="${CONTROL_API:-http://127.0.0.1:9876}"

# WAL ファイルパス（デフォルトのキャッシュディレクトリ）
WAL_PATH="$HOME/.cache/flowsurface/engine/tachibana_orders.jsonl"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    WAL_PATH="$HOME/Library/Caches/flowsurface/engine/tachibana_orders.jsonl"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    WAL_PATH="$HOME/.cache/flowsurface/engine/tachibana_orders.jsonl"
fi

EXPECT_HANDSHAKE_RE='engine handshake complete|Python data engine ready|Connected to external data engine'
EXPECT_VENUE_READY_RE='tachibana: VenueReady — venue is now authenticated'
EXPECT_SP_FAST_PATH='second_password pre-populated from DEV_TACHIBANA_SECOND_PASSWORD'

cleanup() {
    [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
    [[ -n "${APP_PID:-}" ]]    && kill -9 "$APP_PID"    2>/dev/null || true
}
trap cleanup EXIT

log()  { printf '[s80-order-submit] %s\n' "$*" >&2; }
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
    skip "DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD が未設定。
.env を読み込んでから実行:
  set -a && source .env && set +a
  bash $0"
fi
if [[ -z "$SECOND_PASSWORD" ]]; then
    skip "DEV_TACHIBANA_SECOND_PASSWORD が未設定。
.env に DEV_TACHIBANA_SECOND_PASSWORD=<第二暗証番号> を設定してください。"
fi

# ── 安全制約: デモ口座のみ ────────────────────────────────────────────────────
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
        taskkill /PID "$pid" /F 2>/dev/null && log "ポート $PORT の古いプロセス (PID=$pid) を終了" || true
    done
else
    fuser -k "${PORT}/tcp" 2>/dev/null || true
fi
sleep 0.3

# ── Python エンジン起動 ───────────────────────────────────────────────────────
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

# ── ハンドシェイク待機 ───────────────────────────────────────────────────────
log "ハンドシェイク待機 (最大 15 秒)..."
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

# ── HTTP 制御 API 疎通確認 ────────────────────────────────────────────────────
if ! curl -sf "$CONTROL_API/api/replay/status" >/dev/null 2>&1; then
    log "FAIL: HTTP 制御 API ($CONTROL_API) に到達できません"; exit 3
fi

# ── ログイン要求 ─────────────────────────────────────────────────────────────
log "POST $CONTROL_API/api/sidebar/tachibana/request-login ..."
HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -X POST "$CONTROL_API/api/sidebar/tachibana/request-login" 2>&1)
if [[ "$HTTP_STATUS" != "202" ]]; then
    fail "login request: HTTP $HTTP_STATUS (期待: 202)"
fi
log "ログイン要求 202 Accepted"

# ── VenueReady 待機 ──────────────────────────────────────────────────────────
log "VenueReady 待機 (最大 ${OBSERVE_S} 秒)..."
deadline=$((SECONDS + OBSERVE_S))
while (( SECONDS < deadline )); do
    if grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then break; fi
    sleep 0.5
done
if ! grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: VenueReady が ${OBSERVE_S} 秒以内に確認できませんでした"
    tail -30 "$RUST_LOG_FILE" 2>/dev/null; tail -20 "$ENGINE_LOG"; exit 2
fi
log "VenueReady 確認"

# ── 第二暗証番号の注入確認 ──────────────────────────────────────────────────
if ! grep -q "$EXPECT_SP_FAST_PATH" "$ENGINE_LOG" 2>/dev/null; then
    log "WARN: second_password dev fast path ログが見当たりません"
    log "  DEV_TACHIBANA_SECOND_PASSWORD が正しく渡されているか確認してください"
fi

# ── 発注 ─────────────────────────────────────────────────────────────────────
CLIENT_ORDER_ID="e2e-$(date +%s)-$(( RANDOM % 9999 ))"
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

# ── アサーション ─────────────────────────────────────────────────────────────
fail_count=0

# [1] HTTP 201 Created (新規) または 200 OK (idempotent replay)
if [[ "$SUBMIT_HTTP_CODE" != "201" && "$SUBMIT_HTTP_CODE" != "200" ]]; then
    log "FAIL [1]: HTTP $SUBMIT_HTTP_CODE (期待: 201 または 200)"
    log "  ボディ: $SUBMIT_BODY"
    fail_count=$((fail_count + 1))
else
    log "PASS [1]: HTTP $SUBMIT_HTTP_CODE"
fi

# [2] venue_order_id が応答に含まれる
if ! echo "$SUBMIT_BODY" | grep -q '"venue_order_id"'; then
    log "FAIL [2]: レスポンスに venue_order_id が含まれません"
    log "  ボディ: $SUBMIT_BODY"
    fail_count=$((fail_count + 1))
else
    VENUE_ORDER_ID=$(echo "$SUBMIT_BODY" | grep -o '"venue_order_id":"[^"]*"' | cut -d'"' -f4)
    log "PASS [2]: venue_order_id=${VENUE_ORDER_ID}"
fi

# [3] WAL にエントリが書かれた（second_password が漏洩していないこと）
sleep 0.5  # WAL flush のための猶予
if [[ -f "$WAL_PATH" ]]; then
    if grep -q "$CLIENT_ORDER_ID" "$WAL_PATH" 2>/dev/null; then
        log "PASS [3]: WAL に client_order_id が記録されています"
    else
        log "WARN [3]: WAL に client_order_id が見当たりません (flush 遅延の可能性)"
    fi
    # 第二暗証番号漏洩チェック
    if grep -qi "second_password\|sSecondPassword" "$WAL_PATH" 2>/dev/null; then
        log "FAIL [3b]: WAL に second_password が含まれています！"
        fail_count=$((fail_count + 1))
    else
        log "PASS [3b]: WAL に second_password なし (C-M2 OK)"
    fi
fi

# [4] エンジンログに second_password の値が含まれていないこと（C-M2）
if grep -q "${SECOND_PASSWORD}" "$ENGINE_LOG" 2>/dev/null; then
    log "FAIL [4]: エンジンログに second_password の値が含まれています！"
    fail_count=$((fail_count + 1))
else
    log "PASS [4]: エンジンログに second_password 値なし (C-M2 OK)"
fi

# ── 結果 ─────────────────────────────────────────────────────────────────────
echo ""
if (( fail_count == 0 )); then
    log "PASS: 立花デモ口座 発注 E2E 完了"
    log "  client_order_id=${CLIENT_ORDER_ID}"
    log "  venue_order_id=${VENUE_ORDER_ID:-N/A}"
    exit 0
else
    log "FAIL: $fail_count 件のアサーションが失敗しました"
    exit 3
fi
