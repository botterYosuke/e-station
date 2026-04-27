#!/usr/bin/env bash
# Build the standalone `flowsurface-engine` binary via PyInstaller.
#
# Output:
#   target/release/python-engine/flowsurface-engine[.exe]
#
# Usage:
#   scripts/build-engine.sh                      # uses uv tool run
#   PYINSTALLER=pyinstaller scripts/build-engine.sh   # use plain pyinstaller
#   PYTHON=python3.12 scripts/build-engine.sh         # pin interpreter
#
# The Win/Mac/Linux package scripts copy the resulting binary alongside
# `flowsurface(.exe)` so that `EngineCommand::resolve` finds it at runtime.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="$REPO_ROOT/target/release/python-engine"
SPEC="$REPO_ROOT/python/engine.spec"

if [ ! -f "$SPEC" ]; then
  echo "build-engine: spec not found: $SPEC" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

PYINSTALLER="${PYINSTALLER:-}"
if [ -z "$PYINSTALLER" ]; then
  if command -v uv >/dev/null 2>&1; then
    # uv tool run grabs PyInstaller into an isolated env without polluting
    # the workspace virtualenv — preferred in CI.
    PYINSTALLER="uv tool run pyinstaller"
  elif command -v pyinstaller >/dev/null 2>&1; then
    PYINSTALLER="pyinstaller"
  else
    echo "build-engine: PyInstaller not found. Install via:" >&2
    echo "  uv tool install pyinstaller   # recommended" >&2
    echo "  pip install pyinstaller       # or system pip" >&2
    exit 1
  fi
fi

echo "build-engine: using $PYINSTALLER"
echo "build-engine: spec=$SPEC"
echo "build-engine: output=$OUT_DIR"

# PyInstaller ignores --workpath/--distpath if the spec hardcodes them, but the
# spec we ship leaves both at the defaults so the CLI flags take effect.
$PYINSTALLER \
  --clean --noconfirm \
  --distpath "$OUT_DIR" \
  --workpath "$OUT_DIR/build" \
  "$SPEC"

if [ "${OS:-}" = "Windows_NT" ]; then
  EXE="$OUT_DIR/flowsurface-engine.exe"
else
  EXE="$OUT_DIR/flowsurface-engine"
fi

if [ ! -f "$EXE" ]; then
  echo "build-engine: expected output missing: $EXE" >&2
  exit 1
fi

echo "build-engine: produced $EXE"
