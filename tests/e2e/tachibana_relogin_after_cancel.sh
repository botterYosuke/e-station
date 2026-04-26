#!/usr/bin/env bash
# tests/e2e/tachibana_relogin_after_cancel.sh — T35-U5-RelogE2E.
#
# Drives the "Tachibana venue selection → cancel → re-login" loop end-to-end
# against the production Rust + Python pipeline. The acceptance contract
# (plan §3 Step F) is:
#
#   1. flowsurface starts in dev mode with `DEV_TACHIBANA_*` UNSET, so the
#      tkinter login helper subprocess path is forced (no fast-path env
#      credentials).
#   2. The harness selects the Tachibana venue via the local HTTP control
#      API (port 9876). Engine emits `VenueLoginStarted{venue:"tachibana"}`
#      within 30 s — observed by tailing `flowsurface-current.log`.
#   3. Cancellation injection: the test closes stdin (EOF) on the tkinter
#      helper subprocess, triggering the `_read_stdin_payload` EOF branch
#      that resolves to `{"status":"cancelled"}`. Engine emits
#      `VenueLoginCancelled`.
#   4. The harness fires the "再ログイン" path via the HTTP API. Engine
#      emits a *second* `VenueLoginStarted` (total = 2). The first cancel
#      must NOT be followed by a duplicate VenueLoginStarted within the
#      same 100 ms window (= no auto-fire storm).
#
# Status: **scaffold only**. The HTTP control API at :9876
# (`src/replay_api.rs` per `.claude/skills/e2e-testing/SKILL.md`) is not
# yet present in `src/`. Until that lands, this script:
#   * verifies the binary + engine launch path
#   * exits with code 77 (autotools "skip") when the HTTP API endpoint
#     is unreachable, with a clear diagnostic so CI can distinguish
#     "skipped because prerequisite not built" from "actual failure"
#   * keeps the full driver sequence as TODO blocks below so a future
#     follow-up PR (HTTP API + tkinter helper EOF wiring) can replace
#     each TODO inline without restructuring the script
#
# Usage:
#   bash tests/e2e/tachibana_relogin_after_cancel.sh                  # 30 s
#   OBSERVE_S=60 bash tests/e2e/tachibana_relogin_after_cancel.sh     # CI nightly
#
# Exit codes:
#   0  PASS — full sequence observed (handshake → start → cancel → start)
#   1  binary missing
#   2  handshake never completed within 15 s
#   3  log assertion failed (counts off, parse errors, etc.)
#   77 SKIPPED — HTTP control API not built yet (expected on current main)

set -uo pipefail

OBSERVE_S="${OBSERVE_S:-30}"
PORT="${PORT:-19877}"   # distinct from smoke.sh default 19876 so they can
                        # run in parallel without port collision
TOKEN="${TOKEN:-e2e-tachibana-relogin-token}"
ENGINE_LOG="${ENGINE_LOG:-/tmp/flowsurface-e2e-tachibana-engine.log}"
RUST_LOG_FILE="$HOME/AppData/Roaming/flowsurface/flowsurface-current.log"
if [[ "${OSTYPE:-}" == darwin* ]]; then
    RUST_LOG_FILE="$HOME/Library/Application Support/flowsurface/flowsurface-current.log"
elif [[ "${OSTYPE:-}" == linux* ]]; then
    RUST_LOG_FILE="$HOME/.local/share/flowsurface/flowsurface-current.log"
fi

# Use the **debug** binary so any future debug-only `/api/test/*`
# endpoints (e.g. delete-persisted-session) are available — see
# `.claude/skills/e2e-testing/SKILL.md`.
BINARY="${BINARY:-./target/debug/flowsurface}"
[[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* || "${OS:-}" == "Windows_NT" ]] && \
    BINARY="./target/debug/flowsurface.exe"

CONTROL_API="${CONTROL_API:-http://127.0.0.1:9876}"

# Pinned pattern for the VenueLoginStarted log line. Defined as a constant
# at the top so tweaks to the engine log format land in one spot.
EXPECT_STARTED_RE='VenueLoginStarted.*venue.*tachibana'
EXPECT_CANCELLED_RE='VenueLoginCancelled.*venue.*tachibana'

cleanup() {
    [[ -n "${ENGINE_PID:-}" ]] && kill -9 "$ENGINE_PID" 2>/dev/null || true
    [[ -n "${APP_PID:-}" ]] && kill -9 "$APP_PID" 2>/dev/null || true
}
trap cleanup EXIT

log() { printf '[tachibana-relogin] %s\n' "$*" >&2; }
skip() {
    log "SKIP: $*"
    exit 77
}

if [[ ! -x "$BINARY" ]]; then
    log "FAIL: $BINARY not found. Run: cargo build"
    exit 1
fi

# ── Pre-flight skip: HTTP control API not yet wired ──────────────────
# Avoid the 15 s engine + app boot cost when we already know the test
# cannot drive the venue selection. Detect by grepping `src/main.rs`
# for the API module declaration. When `src/replay_api.rs` lands and is
# wired into main, this check naturally flips to "proceed".
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if ! grep -qE '^mod replay_api;|^pub mod replay_api;' "$REPO_ROOT/src/main.rs"; then
    skip "src/main.rs does not declare the HTTP control API module \
('replay_api'). The driver stages of this E2E need the API surface \
documented in .claude/skills/e2e-testing/SKILL.md (port 9876). \
Implement that and remove the skip-check at the top of this script. \
Engine + handshake portion of this scenario is not run because \
the rest cannot be."
fi

# ── Force dialog path: ensure DEV_TACHIBANA_* env vars are NOT set so
# the engine routes login through the tkinter helper instead of the
# fast-path credential injection.
unset DEV_TACHIBANA_USER_ID 2>/dev/null || true
unset DEV_TACHIBANA_PASSWORD 2>/dev/null || true
unset DEV_TACHIBANA_IS_DEMO 2>/dev/null || true

log "starting engine on :$PORT"
: > "$ENGINE_LOG"
uv run python -m engine --port "$PORT" --token "$TOKEN" > "$ENGINE_LOG" 2>&1 &
ENGINE_PID=$!

# Wait for the engine TCP listener to come up.
for _ in {1..50}; do
    if (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then break; fi
    sleep 0.1
done

log "starting flowsurface app (debug build)"
: > "$RUST_LOG_FILE" 2>/dev/null || true
FLOWSURFACE_ENGINE_TOKEN="$TOKEN" RUST_LOG=info \
    "$BINARY" --data-engine-url "ws://127.0.0.1:$PORT" > /dev/null 2>&1 &
APP_PID=$!

# Wait for handshake to complete before driving venue selection.
deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
    if grep -q "engine handshake complete\|Python data engine ready\|Connected to external data engine" "$RUST_LOG_FILE" 2>/dev/null; then
        log "handshake complete"
        break
    fi
    sleep 0.5
done
if ! grep -q "engine handshake complete\|Python data engine ready\|Connected to external data engine" "$RUST_LOG_FILE" 2>/dev/null; then
    log "FAIL: handshake never completed within 15 s"
    tail -30 "$RUST_LOG_FILE" 2>/dev/null
    tail -30 "$ENGINE_LOG"
    exit 2
fi

# ── HTTP control API gate ─────────────────────────────────────────────
# The full driver below depends on the local HTTP control API documented
# in `.claude/skills/e2e-testing/SKILL.md`. That API
# (`src/replay_api.rs`) has not yet been wired into `src/main.rs`, so
# this script SKIPs cleanly until it lands. CI distinguishes 77 (skip)
# from 0 (pass) so the gap is visible in dashboards.
if ! curl -sf "$CONTROL_API/api/replay/status" >/dev/null 2>&1; then
    skip "HTTP control API at $CONTROL_API not reachable. Implement \
src/replay_api.rs per .claude/skills/e2e-testing/SKILL.md before \
this scenario can run end-to-end. (Engine + handshake portion of \
this E2E completed successfully; only the venue-driving stages are \
gated.)"
fi

# ── Stage 1: select Tachibana venue, expect VenueLoginStarted ────────
# TODO(http-api): translate the existing TickersTable
# `Message::ToggleExchangeFilter(Venue::Tachibana)` into an HTTP
# endpoint (e.g. `POST /api/sidebar/toggle-venue {"venue":"tachibana"}`)
# and call it here. Until then the assertion below is best-effort.
log "stage 1: trigger Tachibana venue selection (HTTP API placeholder)"
# curl -sf -X POST -H 'Content-Type: application/json' \
#     -d '{"venue":"tachibana"}' "$CONTROL_API/api/sidebar/toggle-venue" >/dev/null

# Wait up to 30 s for the first VenueLoginStarted. Timeout = handshake
# 15 s + tkinter spawn 10 s + slack 5 s.
stage1_deadline=$((SECONDS + 30))
while (( SECONDS < stage1_deadline )); do
    if grep -qE "$EXPECT_STARTED_RE" "$RUST_LOG_FILE" 2>/dev/null; then
        log "stage 1 PASS: VenueLoginStarted observed"
        break
    fi
    sleep 0.5
done
started_count=$(grep -cE "$EXPECT_STARTED_RE" "$RUST_LOG_FILE" 2>/dev/null | tr -d '\r\n[:space:]')
started_count=${started_count:-0}
if (( started_count < 1 )); then
    log "FAIL stage 1: VenueLoginStarted not observed within 30 s"
    tail -50 "$RUST_LOG_FILE" 2>/dev/null
    exit 3
fi

# ── Stage 2: cancel the in-flight login by EOF'ing the helper stdin ──
# TODO(http-api): expose the helper-PID via the control API or kill the
# subprocess directly. The helper supports `--headless` mode where stdin
# EOF is interpreted as `{"status":"cancelled"}` (see review-fixes
# 2026-04-25 round 4 group E / `_read_stdin_payload`).
log "stage 2: inject cancel via stdin EOF (HTTP API placeholder)"
# curl -sf -X POST "$CONTROL_API/api/test/tachibana/cancel-helper" >/dev/null

stage2_deadline=$((SECONDS + 10))
while (( SECONDS < stage2_deadline )); do
    if grep -qE "$EXPECT_CANCELLED_RE" "$RUST_LOG_FILE" 2>/dev/null; then
        log "stage 2 PASS: VenueLoginCancelled observed"
        break
    fi
    sleep 0.5
done
cancelled_count=$(grep -cE "$EXPECT_CANCELLED_RE" "$RUST_LOG_FILE" 2>/dev/null | tr -d '\r\n[:space:]')
cancelled_count=${cancelled_count:-0}
if (( cancelled_count < 1 )); then
    log "FAIL stage 2: VenueLoginCancelled not observed within 10 s of cancel injection"
    exit 3
fi

# ── Stage 3: re-login via banner / sidebar Manual button ─────────────
# TODO(http-api): expose the inline 「立花 ログイン」 / banner Relogin
# button through the control API (e.g.
# `POST /api/sidebar/tachibana/request-login`). The expected behaviour
# is `Trigger::Manual` — Flowsurface forwards the request as
# `Command::RequestVenueLogin{venue:"tachibana"}` and the engine spawns
# a fresh tkinter helper.
log "stage 3: trigger manual re-login (HTTP API placeholder)"
# curl -sf -X POST "$CONTROL_API/api/sidebar/tachibana/request-login" >/dev/null

stage3_deadline=$((SECONDS + 10))
while (( SECONDS < stage3_deadline )); do
    started_count=$(grep -cE "$EXPECT_STARTED_RE" "$RUST_LOG_FILE" 2>/dev/null | tr -d '\r\n[:space:]')
    started_count=${started_count:-0}
    if (( started_count >= 2 )); then
        log "stage 3 PASS: second VenueLoginStarted observed (total $started_count)"
        break
    fi
    sleep 0.5
done

# ── Final assertions ─────────────────────────────────────────────────
fail=0
final_started=$(grep -cE "$EXPECT_STARTED_RE" "$RUST_LOG_FILE" 2>/dev/null | tr -d '\r\n[:space:]')
final_started=${final_started:-0}
final_cancelled=$(grep -cE "$EXPECT_CANCELLED_RE" "$RUST_LOG_FILE" 2>/dev/null | tr -d '\r\n[:space:]')
final_cancelled=${final_cancelled:-0}

if (( final_started != 2 )); then
    log "FAIL: expected exactly 2 VenueLoginStarted (init + relogin), got $final_started"
    fail=1
fi
if (( final_cancelled != 1 )); then
    log "FAIL: expected exactly 1 VenueLoginCancelled, got $final_cancelled"
    fail=1
fi

# Auto-fire storm guard: no more than one VenueLoginStarted should ever
# appear within 100 ms of a VenueLoginCancelled — that pattern would
# indicate the LoginInFlight gate is leaking.
# (Implementing strict ms timestamp parsing belongs with the HTTP API
# follow-up; this simple count check catches gross regressions.)

if (( fail == 0 )); then
    log "PASS: cancel → re-login round trip clean (started=$final_started, cancelled=$final_cancelled)"
    exit 0
else
    exit 3
fi
