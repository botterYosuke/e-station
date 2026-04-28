#!/usr/bin/env bash
# tests/e2e/tachibana_demo_login.sh — Tachibana デモ口座ログイン E2E テスト
#
# HTTP 制御 API (ポート 9876) 経由で「立花ログイン」ボタン相当の操作を行い、
# デモ口座への認証が完了することを端対端で確認する。
#
# ────────────────────────────────────────────────────────────────────────────
# アカウント選択の仕様確認
# ────────────────────────────────────────────────────────────────────────────
#   デモ口座 : DEV_TACHIBANA_DEMO=true  (このテストで使用)
#   本番口座 : tkinter ダイアログで TACHIBANA_ALLOW_PROD=1 を設定したとき
#             「本番」ラジオボタンが表示され選択可能になる。
#             本番口座はこのスクリプトでは一切テストしない（安全制約）。
#
# ────────────────────────────────────────────────────────────────────────────
# 前提条件
# ────────────────────────────────────────────────────────────────────────────
#   1. cargo build  (デバッグバイナリが必要)
#   2. .env に以下が設定されているか、環境変数として export されていること:
#        DEV_TACHIBANA_USER_ID=<demo user id>
#        DEV_TACHIBANA_PASSWORD=<demo password>
#        DEV_TACHIBANA_DEMO=true
#   3. Python 環境: uv が利用可能
#
# Usage:
#   set -a && source .env && set +a
#   bash tests/e2e/tachibana_demo_login.sh
#
#   OBSERVE_S=60 bash tests/e2e/tachibana_demo_login.sh  # longer timeout
#
# Exit codes:
#   0   PASS — login succeeded and VenueReady confirmed
#   1   FAIL — binary / prerequisite missing
#   2   FAIL — handshake timeout
#   3   FAIL — assertion failed (VenueReady / demo flag / etc.)
#   4   FAIL — DEMO 口座以外でのテストを拒否 (安全制約)
#   77  SKIP — credentials not set (normal CI / offline behaviour)

set -uo pipefail

OBSERVE_S="${OBSERVE_S:-45}"
PORT="${PORT:-19878}"   # 19876=smoke, 19877=relogin, 19878=demo-login
TOKEN="${TOKEN:-e2e-tachibana-demo-login-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-tachibana-demo-engine.log}"

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

# ── ログ検索パターン ─────────────────────────────────────────────────────────
EXPECT_VENUE_READY_RE='tachibana: VenueReady — venue is now authenticated'
EXPECT_DEV_FAST_PATH_RE='using dev env fast path'
EXPECT_DEMO_FLAG_RE='is_demo=True'
EXPECT_HANDSHAKE_RE='engine handshake complete|Python data engine ready|Connected to external data engine'

cleanup() {
    [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
    [[ -n "${APP_PID:-}" ]]    && kill -9 "$APP_PID"    2>/dev/null || true
}
trap cleanup EXIT

log()  { printf '[tachibana-demo-login] %s\n' "$*" >&2; }
skip() { log "SKIP: $*"; exit 77; }
fail() { log "FAIL: $*"; exit 3; }

# ── 前提チェック ─────────────────────────────────────────────────────────────
if [[ ! -x "$BINARY" ]]; then
    log "FAIL: $BINARY not found. Run: cargo build"
    exit 1
fi

# ── 認証情報の確認 ───────────────────────────────────────────────────────────
USER_ID="${DEV_TACHIBANA_USER_ID:-}"
PASSWORD="${DEV_TACHIBANA_PASSWORD:-}"
IS_DEMO_RAW="${DEV_TACHIBANA_DEMO:-}"

if [[ -z "$USER_ID" || -z "$PASSWORD" ]]; then
    skip "DEV_TACHIBANA_USER_ID / DEV_TACHIBANA_PASSWORD が未設定。
.env を読み込んでから実行してください:
  set -a && source .env && set +a
  bash $0"
fi

# ── 安全制約: 必ずデモ口座を使う ────────────────────────────────────────────
case "${IS_DEMO_RAW,,}" in
    1|true|yes|on) IS_DEMO=true ;;
    *)
        log "FAIL (安全制約): DEV_TACHIBANA_DEMO が truthy 値に設定されていません (値: '${IS_DEMO_RAW}')。"
        log "本番口座でのテストは許可されていません。.env に DEV_TACHIBANA_DEMO=true を設定してください。"
        exit 4
        ;;
esac

log "デモ口座認証情報を確認 (user_id=${USER_ID}, is_demo=${IS_DEMO})"

# ── ポートのクリーンアップ ──────────────────────────────────────────────────
# 前回のテスト実行が中断された場合に残るプロセスを強制終了する。
if [[ "${OS:-}" == "Windows_NT" || "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
    # Windows: netstat で PID を取得して taskkill で終了
    stale_pids=$(netstat -ano 2>/dev/null | awk '/:'$PORT' +0\.0\.0\.0:0 +LISTENING/{print $NF}' | sort -u)
    for pid in $stale_pids; do
        taskkill /PID "$pid" /F 2>/dev/null && log "ポート $PORT の古いプロセス (PID=$pid) を終了" || true
    done
else
    # Unix: ss / fuser
    fuser -k "${PORT}/tcp" 2>/dev/null || true
fi
sleep 0.3

# ── Python エンジン起動 ──────────────────────────────────────────────────────
# FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 を設定することで、
# 受信した RequestVenueLogin に対して環境変数の dev fast path が有効になる。
log "エンジン起動 (ポート:$PORT, FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1)"
: > "$ENGINE_LOG"
FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 \
DEV_TACHIBANA_USER_ID="$USER_ID" \
DEV_TACHIBANA_PASSWORD="$PASSWORD" \
DEV_TACHIBANA_DEMO="$IS_DEMO_RAW" \
    uv run python -m engine --port "$PORT" --token "$TOKEN" > "$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!

# エンジン TCP リスナーが起動するまで待機
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
log "flowsurface アプリ起動 (デバッグビルド, $BINARY)"
# デバッグビルドは stdout にログを出す (logger.rs: is_debug=true)。
# リリースビルドは AppData ファイルに書くが stdout は空。
# どちらも RUST_LOG_FILE に向けることで grep が効くようにする。
mkdir -p "$(dirname "$RUST_LOG_FILE")" 2>/dev/null || true
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > "$RUST_LOG_FILE" 2>&1 &
APP_PID=$!

# ── ハンドシェイク待機 ───────────────────────────────────────────────────────
log "ハンドシェイク待機 (最大 15 秒)..."
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    if grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null; then
        log "ハンドシェイク完了"
        break
    fi
    sleep 0.5
done
if ! grep -qE "$EXPECT_HANDSHAKE_RE" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: ハンドシェイクが 15 秒以内に完了しませんでした"
    tail -30 "$RUST_LOG_FILE" 2>/dev/null
    tail -20 "$ENGINE_LOG"
    exit 2
fi

# ── HTTP 制御 API 疎通確認 ───────────────────────────────────────────────────
log "HTTP 制御 API 疎通確認 ($CONTROL_API/api/replay/status)..."
if ! curl -sf "$CONTROL_API/api/replay/status" >/dev/null 2>&1; then
    log "FAIL: HTTP 制御 API ($CONTROL_API) に到達できません"
    log "src/main.rs に Subscription::run(replay_api_stream) が追加されているか確認してください"
    exit 3
fi
log "HTTP 制御 API: OK"

# ── 立花ログイン要求 ─────────────────────────────────────────────────────────
# POST /api/sidebar/tachibana/request-login は、
# 「立花ログイン」ボタンクリックと同等の RequestVenueLogin IPC を Rust → Python に送信する。
# Python 側は FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 + DEV_TACHIBANA_* により
# tkinter ダイアログを起動せずに直接デモ口座へ認証する（dev env fast path）。
log "POST $CONTROL_API/api/sidebar/tachibana/request-login (デモログイン要求)..."
HTTP_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -X POST "$CONTROL_API/api/sidebar/tachibana/request-login" 2>&1)
if [[ "$HTTP_STATUS" != "202" ]]; then
    log "FAIL: HTTP ステータス $HTTP_STATUS (期待: 202)"
    exit 3
fi
log "ログイン要求送信完了 (202 Accepted)"

# ── VenueReady 待機 ──────────────────────────────────────────────────────────
log "VenueReady 待機 (最大 ${OBSERVE_S} 秒)..."
venue_ready_deadline=$((SECONDS + OBSERVE_S))
while (( SECONDS < venue_ready_deadline )); do
    if grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then
        log "VenueReady 確認 (Rust ログ)"
        break
    fi
    sleep 0.5
done

# ── アサーション ─────────────────────────────────────────────────────────────
fail_count=0

# セッションキャッシュが有効だった場合、dev fast path ログは出力されない（正常動作）
CACHE_PATH_TAKEN=false
if grep -q "cached session is valid" "$ENGINE_LOG" 2>/dev/null; then
    CACHE_PATH_TAKEN=true
    log "INFO: セッションキャッシュが有効 — キャッシュ経路でログイン済み"
fi

# [1] Rust: VenueReady を受信したか
if ! grep -qF "$EXPECT_VENUE_READY_RE" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL [1]: Rust ログに VenueReady が見当たりません"
    log "  期待パターン: $EXPECT_VENUE_READY_RE"
    log "  --- Rust ログ末尾 ---"
    tail -30 "$RUST_LOG_FILE" 2>/dev/null
    fail_count=$((fail_count + 1))
else
    log "PASS [1]: Rust VenueReady 確認済み"
fi

# [2] Python: dev env fast path が使われたか（キャッシュ経路では省略される）
if ! grep -q "$EXPECT_DEV_FAST_PATH_RE" "$ENGINE_LOG" 2>/dev/null; then
    if [[ "$CACHE_PATH_TAKEN" == "true" ]]; then
        log "SKIP [2]: キャッシュ経路のためdev fast path ログなし（正常）"
    else
        log "FAIL [2]: Python ログに 'using dev env fast path' が見当たりません"
        log "  FLOWSURFACE_DEV_TACHIBANA_LOGIN_ALLOWED=1 が有効になっているか確認してください"
        log "  --- Python エンジンログ末尾 ---"
        tail -20 "$ENGINE_LOG"
        fail_count=$((fail_count + 1))
    fi
else
    log "PASS [2]: Python dev env fast path 確認済み"
fi

# [3] Python: is_demo=True でログインしたか（キャッシュ経路では省略される）
if ! grep -q "$EXPECT_DEMO_FLAG_RE" "$ENGINE_LOG" 2>/dev/null; then
    if [[ "$CACHE_PATH_TAKEN" == "true" ]]; then
        log "SKIP [3]: キャッシュ経路のためis_demo ログなし（正常）"
    else
        log "FAIL [3]: Python ログに 'is_demo=True' が見当たりません"
        log "  DEV_TACHIBANA_DEMO=true が正しく伝達されているか確認してください"
        tail -20 "$ENGINE_LOG"
        fail_count=$((fail_count + 1))
    fi
else
    log "PASS [3]: デモ口座認証確認済み (is_demo=True)"
fi

# ── 本番口座選択の仕様確認（実際にはテストしない）───────────────────────────
log ""
log "--- アカウント選択仕様の確認 ---"
log "  デモ口座: DEV_TACHIBANA_DEMO=true (このテストで確認済み)"
log "  本番口座: TACHIBANA_ALLOW_PROD=1 を設定して tkinter ダイアログを起動すると"
log "           「デモ」「本番」のラジオボタンが表示され選択可能"
log "           (tachibana_login_dialog.py の allow_prod_choice 経路)"
log "  ※ 本番口座でのログインはこの E2E テストでは実施しない (安全制約)"
log "---"
log ""

# ── 結果 ─────────────────────────────────────────────────────────────────────
if (( fail_count == 0 )); then
    log "PASS: Tachibana デモ口座ログイン E2E 完了"
    log "  user_id=${USER_ID}, is_demo=true"
    exit 0
else
    log "FAIL: $fail_count 件のアサーションが失敗しました"
    exit 3
fi
