#!/usr/bin/env bash
# tools/secret_scan.sh — Tachibana secret / prod-URL leak scanner (T7, F-L1)
#
# Reads patterns from tools/secret_scan_patterns.txt (one regex per line;
# blank lines and lines starting with '#' are ignored).
# Reads file-level allowlist from tools/secret_scan_allowlist.txt.
#
# For each pattern, searches the entire repository for matches.  Any match
# whose file path appears in the allowlist is suppressed.  All remaining
# matches are printed and the script exits with code 1.
#
# Usage:
#   bash tools/secret_scan.sh                # from repo root
#   REPO_ROOT=/path/to/repo bash tools/secret_scan.sh
#
# Exit codes:
#   0  No disallowed matches found
#   1  One or more disallowed matches found (details printed to stderr)
#   2  Usage / configuration error

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

PATTERNS_FILE="$SCRIPT_DIR/secret_scan_patterns.txt"
ALLOWLIST_FILE="$SCRIPT_DIR/secret_scan_allowlist.txt"

if [[ ! -f "$PATTERNS_FILE" ]]; then
    echo "ERROR: patterns file not found: $PATTERNS_FILE" >&2
    exit 2
fi

# Load allowlist paths (relative to repo root → absolute)
declare -a ALLOWLIST=()
if [[ -f "$ALLOWLIST_FILE" ]]; then
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        ALLOWLIST+=("$REPO_ROOT/${line//\\/\/}")
    done < "$ALLOWLIST_FILE"
fi

# Build grep exclusion args from allowlist
# NOTE: EXCLUDE_ARGS uses --exclude with full relative paths, but GNU grep's
# --exclude only matches on the basename. The actual allowlist filtering is
# performed by the abs_filepath comparison below (lines 82-87).
# These args are kept for potential future use with --exclude-dir equivalents.
EXCLUDE_ARGS=()
for al in "${ALLOWLIST[@]}"; do
    # Normalise to relative path for grep --exclude-dir / path matching
    rel="${al#"$REPO_ROOT/"}"
    EXCLUDE_ARGS+=(--exclude="$rel")
done

# Directories always excluded from scanning
DEFAULT_EXCLUDE_DIRS=(
    ".git"
    ".venv"
    "target"
    ".claude"
    "node_modules"
    "docs"
    "__pycache__"
    ".pytest_cache"
)

EXCLUDE_DIR_ARGS=()
for d in "${DEFAULT_EXCLUDE_DIRS[@]}"; do
    EXCLUDE_DIR_ARGS+=(--exclude-dir="$d")
done

found=0

while IFS= read -r pattern; do
    # Skip blank lines and comments
    [[ -z "$pattern" || "$pattern" == \#* ]] && continue

    # grep recursively for the pattern; collect matching file:line pairs
    while IFS= read -r match; do
        # Extract file path (before first colon)
        filepath="${match%%:*}"
        abs_filepath="$REPO_ROOT/$filepath"

        # Check if the file is in the allowlist
        skip=0
        for al in "${ALLOWLIST[@]}"; do
            if [[ "$abs_filepath" == "$al" ]]; then
                skip=1
                break
            fi
        done

        if [[ "$skip" -eq 0 ]]; then
            echo "FAIL: secret pattern /${pattern}/ matched in $match" >&2
            found=1
        fi
    done < <(
        cd "$REPO_ROOT" && \
        grep -rnE "${EXCLUDE_DIR_ARGS[@]}" "${EXCLUDE_ARGS[@]}" -- "$pattern" . 2>/dev/null \
            | sed 's|^\./||'
    )
done < "$PATTERNS_FILE"

if [[ "$found" -eq 0 ]]; then
    echo "secret_scan: OK (no disallowed patterns found)" >&2
    exit 0
else
    exit 1
fi
