#!/usr/bin/env bash
# tools/tests/test_secret_scan.sh — meta-tests for tools/secret_scan.sh (HIGH-D6)
#
# Verifies that the scanner:
#   1. Returns exit 1 for files that contain forbidden patterns (should_fail).
#   2. Returns exit 0 when the only matching file is the allowlisted one
#      (should_pass).
#
# Usage:
#   bash tools/tests/test_secret_scan.sh
#
# Exit codes:
#   0  All assertions passed
#   1  One or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCANNER="$REPO_ROOT/tools/secret_scan.sh"
FIXTURES_FAIL="$SCRIPT_DIR/fixtures/should_fail"
FIXTURES_PASS="$SCRIPT_DIR/fixtures/should_pass"

pass_count=0
fail_count=0

assert_exit() {
    local description="$1"
    local expected_exit="$2"
    shift 2
    local actual_exit=0
    "$@" >/dev/null 2>&1 || actual_exit=$?
    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
        echo "PASS: $description (exit $actual_exit)" >&2
        (( pass_count++ )) || true
    else
        echo "FAIL: $description — expected exit $expected_exit, got $actual_exit" >&2
        (( fail_count++ )) || true
    fi
}

# ── Test 1: should_fail fixture triggers exit 1 ──────────────────────────────
# We create a temp repo-like directory containing only the should_fail fixture
# and point the scanner at it.
tmpdir_fail="$(mktemp -d)"
trap 'rm -rf "$tmpdir_fail"' EXIT

cp -r "$FIXTURES_FAIL/." "$tmpdir_fail/"
cp "$REPO_ROOT/tools/secret_scan_patterns.txt" "$tmpdir_fail/../secret_scan_patterns_tmp.txt" 2>/dev/null || true

# Run scanner against the temp dir (set REPO_ROOT so it scans that subtree)
REPO_ROOT="$tmpdir_fail" assert_exit \
    "should_fail fixture causes exit 1" \
    1 \
    bash "$SCANNER"

# ── Test 2: should_pass fixture (allowlisted) causes exit 0 ──────────────────
# Create a temp repo structure where:
#   - python/engine/exchanges/tachibana_url.py contains the forbidden pattern
#   - allowlist contains that path
# The scanner must exit 0.
tmpdir_pass="$(mktemp -d)"
trap 'rm -rf "$tmpdir_fail" "$tmpdir_pass"' EXIT

mkdir -p "$tmpdir_pass/python/engine/exchanges"
mkdir -p "$tmpdir_pass/tools"

cp "$FIXTURES_PASS/tachibana_url.py" \
   "$tmpdir_pass/python/engine/exchanges/tachibana_url.py"
cp "$REPO_ROOT/tools/secret_scan_patterns.txt" \
   "$tmpdir_pass/tools/secret_scan_patterns.txt"
cp "$REPO_ROOT/tools/secret_scan_allowlist.txt" \
   "$tmpdir_pass/tools/secret_scan_allowlist.txt"

REPO_ROOT="$tmpdir_pass" assert_exit \
    "allowlisted file causes exit 0" \
    0 \
    bash "$SCANNER"

# ── Test 3: actual repo scan must pass (allowlist covers tachibana_url.py) ───
assert_exit \
    "actual repo scan returns exit 0 (no unallowed secrets)" \
    0 \
    bash "$SCANNER"

# ── Summary ──────────────────────────────────────────────────────────────────
echo "test_secret_scan.sh: $pass_count passed, $fail_count failed" >&2
if [[ "$fail_count" -gt 0 ]]; then
    exit 1
fi
exit 0
