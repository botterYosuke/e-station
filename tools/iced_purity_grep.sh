#!/usr/bin/env bash
# iced_purity_grep.sh — structural guard for invariants
#   T35-H7-NoStaticInUpdate / T35-H8-NoBlockOnInUpdate / T35-H9-SingleRecoveryPath.
#
# `Flowsurface::update()` and `Flowsurface::subscription()` must not
# touch the engine-client globals (`ENGINE_CONNECTION_TX`, `ENGINE_MANAGER`,
# `ENGINE_RESTARTING`) directly, and must not call `block_on(...)` —
# every async hop goes through `Task::perform`. The AST tests under
# `tests/main_update_no_*.rs` already enforce this from Rust; this
# shell-level grep provides a fast, language-agnostic CI smoke check.
#
# Exit code 0 = clean, 1 = forbidden literal found.
#
# Usage:
#   bash tools/iced_purity_grep.sh
#
# Wired into `.github/workflows/rust.yml::iced-purity-lint`.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN_RS="$ROOT/src/main.rs"

if [[ ! -f "$MAIN_RS" ]]; then
  echo "iced_purity_grep: src/main.rs not found at $MAIN_RS" >&2
  exit 1
fi

# Extract the body of `fn update(...)` and `fn subscription(...)` from
# main.rs. We delimit by the next top-level `    fn ` declaration in
# the same impl block (4-space indent + `fn `). This is good enough
# for our single-impl Flowsurface struct.
extract_fn() {
  local fn_name="$1"
  awk -v needle="    fn ${fn_name}(" '
    index($0, needle) == 1 { in_fn = 1; depth = 0; next }
    in_fn {
      n = gsub(/\{/, "&"); depth += n
      n = gsub(/\}/, "&"); depth -= n
      print
      if (depth <= 0 && /^    \}/) { in_fn = 0 }
    }
  ' "$MAIN_RS"
}

UPDATE_BODY="$(extract_fn update || true)"
SUBSCRIPTION_BODY="$(extract_fn subscription || true)"

FAIL=0

check() {
  local body="$1"; local where="$2"; local pattern="$3"; local hint="$4"
  if grep -nE "$pattern" <<<"$body" >/dev/null; then
    echo "iced_purity_grep: forbidden pattern '$pattern' inside $where — $hint" >&2
    grep -nE "$pattern" <<<"$body" | sed "s/^/  $where:/" >&2
    FAIL=1
  fi
}

# update() body: no static engine globals, no block_on.
check "$UPDATE_BODY" "fn update" "ENGINE_CONNECTION_TX|ENGINE_MANAGER|ENGINE_RESTARTING" \
  "use Flowsurface struct fields populated via Subscription instead (T35-H7)"
check "$UPDATE_BODY" "fn update" "block_on\\(" \
  "wrap async work in Task::perform(...) (T35-H8)"

# subscription() body: same restrictions to keep recovery wiring single-source.
check "$SUBSCRIPTION_BODY" "fn subscription" "block_on\\(" \
  "subscription() is a stream factory; no synchronous waits"

if [[ "$FAIL" -ne 0 ]]; then
  echo "iced_purity_grep: FAILED" >&2
  exit 1
fi

echo "iced_purity_grep: OK"
